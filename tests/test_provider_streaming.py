import asyncio

from machina.core.provider.streaming import OpenAIStreamState, openai_stream_events


class _Response:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def test_length_finish_is_marked_truncated_on_the_stream_state() -> None:
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
        '"function":{"name":"write_file","arguments":"{\\"path\\":\\"a.css\\",'
        '\\"content\\":\\"body {\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
        "data: [DONE]",
    ]

    async def scenario():
        state = OpenAIStreamState(resolved_model="test-model")
        events = [event async for event in openai_stream_events(_Response(lines), state)]
        return state, events

    state, events = asyncio.run(scenario())
    assert state.saw_terminal is True
    assert state.finish_reason == "length"
    assert state.truncated is True
    assert events == []


def test_normal_finish_is_not_truncated() -> None:
    lines = [
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]

    async def scenario():
        state = OpenAIStreamState(resolved_model="test-model")
        events = [event async for event in openai_stream_events(_Response(lines), state)]
        return state, events

    state, events = asyncio.run(scenario())
    assert state.finish_reason == "stop"
    assert state.truncated is False
    assert [event.content for event in events] == ["ok"]
