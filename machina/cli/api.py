"""Call the local Machina HTTP API with the configured API key."""

from __future__ import annotations

import json
import os
from urllib.parse import urljoin

import httpx
import typer

from machina.cli import output
from machina.cli.common import EXIT_FAILED

DEFAULT_URL = "http://127.0.0.1:8000"


def _headers(raw: list[str], api_key: str | None) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    for item in raw:
        name, separator, value = item.partition(":")
        if not separator or not name.strip():
            raise ValueError(f"invalid header {item!r}; expected 'Name: value'")
        headers[name.strip()] = value.strip()
    key = api_key or os.environ.get("MACHINA_API_KEY", "").strip()
    if key:
        headers.setdefault("X-API-Key", key)
    return headers


def api(
    method: str = typer.Argument(..., help="HTTP method, e.g. GET or POST"),
    path: str = typer.Argument(..., help="Path such as /health or an absolute URL"),
    data: str | None = typer.Option(None, "--data", "-d", help="Request body (JSON by default)"),
    header: list[str] = typer.Option([], "--header", "-H", help="Header as 'Name: value'"),
    url: str = typer.Option(DEFAULT_URL, "--url", help="Server base URL"),
    api_key: str | None = typer.Option(
        None, "--api-key", help="API key (default: MACHINA_API_KEY)"
    ),
    timeout: float = typer.Option(30.0, "--timeout", min=1.0, max=300.0),
    raw: bool = typer.Option(False, "--raw", help="Print the body without JSON formatting"),
) -> None:
    """Make one request to a running Machina server."""
    target = path if path.startswith(("http://", "https://")) else urljoin(
        url.rstrip("/") + "/", path.lstrip("/")
    )
    try:
        headers = _headers(header, api_key)
    except ValueError as error:
        output.error(str(error))
        raise typer.Exit(EXIT_FAILED) from None
    if data is not None and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"
    try:
        response = httpx.request(
            method.upper(),
            target,
            content=data,
            headers=headers,
            timeout=timeout,
        )
    except httpx.HTTPError as error:
        output.error(f"API request failed: {type(error).__name__}: {error}")
        raise typer.Exit(EXIT_FAILED) from None
    output.muted(f"{response.status_code} {response.reason_phrase}  {target}")
    body = response.text
    if not raw:
        try:
            body = json.dumps(response.json(), indent=2, sort_keys=True, ensure_ascii=False)
        except ValueError:
            pass
    typer.echo(body)
    if response.status_code >= 400:
        raise typer.Exit(EXIT_FAILED)
