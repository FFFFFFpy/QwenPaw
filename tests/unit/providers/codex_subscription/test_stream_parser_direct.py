import pytest

from qwenpaw.providers.codex_subscription.stream_parser import (
    ResponsesStreamParser,
    iter_sse_events,
)


def test_incomplete_keeps_details_and_is_not_completed_successfully():
    parser = ResponsesStreamParser()
    parts = parser.feed(
        {
            "type": "response.incomplete",
            "response": {
                "incomplete_details": {"reason": "max_output_tokens"}
            },
        }
    )
    assert parts[0].incomplete is True
    assert parts[0].details == {"reason": "max_output_tokens"}


async def lines():
    for value in [
        ": keepalive",
        'data: {"type":"response.output_text.delta","delta":"PO"}',
        "",
        "data: [DONE]",
        "",
    ]:
        yield value


@pytest.mark.asyncio
async def test_sse_comments_empty_lines_and_done():
    values = [event async for event in iter_sse_events(lines())]
    assert values[0]["delta"] == "PO"


def test_text_reasoning_tools_usage_and_completed():
    parser = ResponsesStreamParser()
    assert (
        parser.feed({"type": "response.output_text.delta", "delta": "PONG"})[
            0
        ].text
        == "PONG"
    )
    assert (
        parser.feed(
            {"type": "response.reasoning_summary_text.delta", "delta": "think"}
        )[0].kind
        == "reasoning"
    )
    parser.feed(
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "id": "item-1",
                "call_id": "call-1",
                "name": "lookup",
                "arguments": "",
            },
        }
    )
    parser.feed(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item-1",
            "delta": '{"q":',
        }
    )
    tool = parser.feed(
        {
            "type": "response.function_call_arguments.done",
            "item_id": "item-1",
            "arguments": '{"q":"x"}',
        }
    )[0]
    assert (tool.kind, tool.call_id, tool.name, tool.arguments) == (
        "tool",
        "call-1",
        "lookup",
        '{"q":"x"}',
    )
    parts = parser.feed(
        {
            "type": "response.completed",
            "response": {
                "usage": {
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "total_tokens": 6,
                    "input_tokens_details": {"cached_tokens": 1},
                }
            },
        }
    )
    assert parts[-1].usage == {
        "input_tokens": 4,
        "output_tokens": 2,
        "total_tokens": 6,
        "cached_tokens": 1,
    }
