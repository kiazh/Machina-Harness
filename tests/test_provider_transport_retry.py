"""Transient TLS/socket retries for provider requests and streams.

A stale keepalive connection can surface ``SSLV3_ALERT_BAD_RECORD_MAC`` during a
long turn. The transport retries such failures on a fresh connection, but only
before any output exists and never for timeouts or certificate errors.
"""

from __future__ import annotations

import contextlib
import ssl
from typing import Any

import httpx
import pytest

from machina.core.provider import direct, transport
from machina.core.provider.direct import DirectProvider
from machina.core.provider.specs import PROVIDER_SPECS

pytestmark = pytest.mark.asyncio


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://example.test/v1/chat/completions")


def _response(status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, request=_request(), json={"ok": True})


class _PostClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, httpx.Response)
        return outcome


class _StreamClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def build_request(self, method: str, url: str, *, json: dict[str, Any]) -> httpx.Request:
        return httpx.Request(method, f"https://example.test{url}", json=json)

    async def send(self, request: httpx.Request, *, stream: bool = False) -> Any:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeStreamResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.request = _request()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=self,  # type: ignore[arg-type]
            )


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "_transport_retry_delay", lambda attempt: 0.0)
    monkeypatch.setattr(transport, "_retry_delay", lambda attempt, resp, free: 0.0)
    monkeypatch.setattr(direct, "_transport_retry_delay", lambda attempt: 0.0)


# ---------------------------------------------------------------------------
# Non-streaming POST
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "failure",
    [
        ssl.SSLError(1, "bad record mac"),
        httpx.ConnectError("TLS handshake failed"),
        httpx.ReadError("connection reset by peer"),
    ],
)
async def test_post_retries_transient_transport_errors_then_succeeds(failure) -> None:
    client = _PostClient([failure, failure, _response()])

    resp = await transport._post_with_retry(
        client,  # type: ignore[arg-type]
        "/chat/completions",
        {"model": "deepseek-v4.1-flash"},
    )

    assert resp.status_code == 200
    assert client.calls == 3


async def test_post_transport_retries_are_bounded() -> None:
    failures = [ssl.SSLError(1, "bad record mac")] * (transport._MAX_TRANSPORT_RETRIES + 1)
    client = _PostClient(failures)

    with pytest.raises(ssl.SSLError):
        await transport._post_with_retry(
            client,  # type: ignore[arg-type]
            "/chat/completions",
            {"model": "deepseek-v4.1-flash"},
        )

    assert client.calls == transport._MAX_TRANSPORT_RETRIES + 1


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("slow response", request=_request()),
        httpx.ConnectTimeout("slow connect", request=_request()),
        ValueError("not a transport failure"),
    ],
)
async def test_post_does_not_retry_non_transient_errors(failure) -> None:
    client = _PostClient([failure, _response()])

    with pytest.raises(type(failure)):
        await transport._post_with_retry(
            client,  # type: ignore[arg-type]
            "/chat/completions",
            {"model": "deepseek-v4.1-flash"},
        )

    assert client.calls == 1


async def test_post_does_not_retry_certificate_verification_failures() -> None:
    cert_error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    wrapped = httpx.ConnectError("TLS handshake failed")
    wrapped.__cause__ = cert_error
    client = _PostClient([wrapped, _response()])

    with pytest.raises(httpx.ConnectError):
        await transport._post_with_retry(
            client,  # type: ignore[arg-type]
            "/chat/completions",
            {"model": "deepseek-v4.1-flash"},
        )

    assert client.calls == 1


async def test_transport_retry_does_not_consume_the_429_budget() -> None:
    client = _PostClient([ssl.SSLError(1, "bad record mac"), _response(429), _response()])

    resp = await transport._post_with_retry(
        client,  # type: ignore[arg-type]
        "/chat/completions",
        {"model": "deepseek-v4.1-flash"},
    )

    assert resp.status_code == 200
    assert client.calls == 3


async def test_post_preserves_the_429_retry_budget() -> None:
    client = _PostClient([_response(429), _response(429), _response(429)])

    with pytest.raises(httpx.HTTPStatusError):
        await transport._post_with_retry(
            client,  # type: ignore[arg-type]
            "/chat/completions",
            {"model": "deepseek-v4.1-flash"},
        )

    assert client.calls == transport._MAX_RETRIES + 1


async def test_post_returns_429_immediately_when_429_retry_is_disabled() -> None:
    client = _PostClient([_response(429), _response()])

    resp = await transport._post_with_retry(
        client,  # type: ignore[arg-type]
        "/chat/completions",
        {"model": "deepseek-v4.1-flash"},
        retry_429=False,
    )

    assert resp.status_code == 429
    assert client.calls == 1


# ---------------------------------------------------------------------------
# Stream opening
# ---------------------------------------------------------------------------
async def test_stream_retries_transient_open_errors_then_succeeds() -> None:
    response = _FakeStreamResponse()
    client = _StreamClient(
        [ssl.SSLError(1, "bad record mac"), httpx.ConnectError("handshake failed"), response]
    )

    async with transport._stream_with_retry(
        client,  # type: ignore[arg-type]
        "/chat/completions",
        {"model": "deepseek-v4.1-flash"},
    ) as opened:
        assert opened is response

    assert client.calls == 3
    assert response.closed is True


async def test_stream_does_not_retry_after_the_response_is_yielded() -> None:
    response = _FakeStreamResponse()
    client = _StreamClient([response, _FakeStreamResponse()])

    with pytest.raises(ssl.SSLError):
        async with transport._stream_with_retry(
            client,  # type: ignore[arg-type]
            "/chat/completions",
            {"model": "deepseek-v4.1-flash"},
        ):
            raise ssl.SSLError(1, "bad record mac")

    assert client.calls == 1


async def test_stream_does_not_retry_non_transient_open_errors() -> None:
    client = _StreamClient([ValueError("bad payload"), _FakeStreamResponse()])

    with pytest.raises(ValueError):
        async with transport._stream_with_retry(
            client,  # type: ignore[arg-type]
            "/chat/completions",
            {"model": "deepseek-v4.1-flash"},
        ):
            pass

    assert client.calls == 1


async def test_stream_retries_429_before_yielding() -> None:
    rejected = _FakeStreamResponse(429)
    accepted = _FakeStreamResponse()
    client = _StreamClient([rejected, accepted])

    async with transport._stream_with_retry(
        client,  # type: ignore[arg-type]
        "/chat/completions",
        {"model": "deepseek-v4.1-flash"},
    ) as opened:
        assert opened is accepted

    assert client.calls == 2
    assert rejected.closed is True


# ---------------------------------------------------------------------------
# Direct provider (opencode-go / deepseek-v4.1-flash path)
# ---------------------------------------------------------------------------
class _NoopLimiter:
    async def acquire(self, tokens: int = 1) -> None:
        return None


class _SSEResponse:
    status_code = 200

    def __init__(self, lines: list[str] | None = None, failure: BaseException | None = None):
        self._lines = list(lines or [])
        self._failure = failure
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        if self._failure is not None:
            raise self._failure

    async def aclose(self) -> None:
        self.closed = True


_HELLO_STREAM = [
    'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":"stop"}]}',
    "data: [DONE]",
]


def _provider() -> DirectProvider:
    provider = DirectProvider(
        PROVIDER_SPECS["opencode-go"],
        api_key="test-token",
        model="deepseek-v4.1-flash",
    )
    provider._rate_limiter = _NoopLimiter()  # type: ignore[assignment]
    return provider


def _patch_stream(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    @contextlib.asynccontextmanager
    async def fake_stream(client, url, payload, **kwargs):
        calls.append(kwargs)
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        yield response

    monkeypatch.setattr(direct, "_stream_with_retry", fake_stream)
    return calls


async def test_direct_stream_retries_before_the_first_event(monkeypatch) -> None:
    calls = _patch_stream(
        monkeypatch,
        [_SSEResponse(failure=ssl.SSLError(1, "bad record mac")), _SSEResponse(_HELLO_STREAM)],
    )
    provider = _provider()
    try:
        events = [
            event async for event in provider.stream_complete([{"role": "user", "content": "hi"}])
        ]
    finally:
        await provider.close()

    assert len(calls) == 2
    assert calls[0]["transport_retries"] == 0
    assert [event.type for event in events] == ["text", "done"]
    assert events[0].content == "hello"


async def test_direct_stream_does_not_retry_after_the_first_event(monkeypatch) -> None:
    calls = _patch_stream(
        monkeypatch,
        [
            _SSEResponse(
                ['data: {"choices":[{"delta":{"content":"partial"}}]}'],
                failure=ssl.SSLError(1, "bad record mac"),
            ),
            _SSEResponse(_HELLO_STREAM),
        ],
    )
    provider = _provider()
    try:
        stream = provider.stream_complete([{"role": "user", "content": "hi"}])
        first = await anext(stream)
        assert first.content == "partial"
        with pytest.raises(ssl.SSLError):
            await anext(stream)
        await stream.aclose()
    finally:
        await provider.close()

    assert len(calls) == 1


async def test_direct_stream_does_not_retry_non_transport_failures(monkeypatch) -> None:
    calls = _patch_stream(
        monkeypatch,
        [_SSEResponse(failure=ValueError("decoder bug")), _SSEResponse(_HELLO_STREAM)],
    )
    provider = _provider()
    try:
        with pytest.raises(ValueError):
            [event async for event in provider.stream_complete([{"role": "user", "content": "hi"}])]
    finally:
        await provider.close()

    assert len(calls) == 1


async def test_direct_stream_transport_retries_are_bounded(monkeypatch) -> None:
    failures = [
        _SSEResponse(failure=ssl.SSLError(1, "bad record mac"))
        for _ in range(transport._MAX_TRANSPORT_RETRIES + 1)
    ]
    calls = _patch_stream(monkeypatch, failures)
    provider = _provider()
    try:
        with pytest.raises(ssl.SSLError):
            [event async for event in provider.stream_complete([{"role": "user", "content": "hi"}])]
    finally:
        await provider.close()

    assert len(calls) == transport._MAX_TRANSPORT_RETRIES + 1
