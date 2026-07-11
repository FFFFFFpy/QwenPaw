import pytest
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    Msg,
    SystemMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)

from qwenpaw.providers.codex_subscription.responses_mapper import (
    ResponsesMapper,
)
from qwenpaw.providers.capping_formatter import (
    _CappingOpenAIResponseFormatter,
)


@pytest.mark.asyncio
async def test_maps_messages_tools_images_without_xml():
    messages = [
        SystemMsg(name="system", content=[TextBlock(text="Be exact")]),
        UserMsg(
            name="user",
            content=[
                TextBlock(text="hello"),
                DataBlock(
                    source=Base64Source(
                        data="aGVsbG8=", media_type="image/png"
                    )
                ),
            ],
        ),
        AssistantMsg(
            name="assistant",
            content=[
                TextBlock(text="checking"),
                ToolCallBlock(id="call-1", name="lookup", input='{"q":"x"}'),
            ],
        ),
        Msg(
            name="tool",
            role="assistant",
            content=[ToolResultBlock(id="call-1", name="lookup", output="ok")],
        ),
    ]
    formatted = await _CappingOpenAIResponseFormatter().format(messages)
    body = ResponsesMapper().build_request(
        model="gpt-5.6-luna",
        input_items=formatted,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
        reasoning_effort="low",
    )
    assert body["instructions"] == "Be exact"
    assert body["input"][0]["content"][1]["type"] == "input_image"
    assert any(item.get("type") == "function_call" for item in body["input"])
    assert any(
        item.get("type") == "function_call_output" for item in body["input"]
    )
    assert body["tools"][0]["name"] == "lookup"
    assert body["reasoning"]["effort"] == "low"
    assert body["reasoning"]["context"] == "all_turns"
    assert body["parallel_tool_calls"] is False
    assert "QWENPAW_CONTEXT" not in str(body)


def test_structured_format_and_auto_effort():
    body = ResponsesMapper().build_request(
        model="gpt-5.6-luna",
        input_items=[],
        reasoning_effort="auto",
        structured_format={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
        },
    )
    assert "effort" not in body.get("reasoning", {})
    assert body["reasoning"]["context"] == "all_turns"
    assert body["parallel_tool_calls"] is False
    assert body["text"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_formatter_preserves_tool_text_order_and_assistant_output_text():
    formatted = await _CappingOpenAIResponseFormatter().format(
        [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(id="one", name="first", input="{}"),
                    TextBlock(text="after"),
                    ToolCallBlock(id="two", name="second", input="{}"),
                ],
            )
        ]
    )
    assert [item.get("type", item.get("role")) for item in formatted] == [
        "function_call",
        "assistant",
        "function_call",
    ]
    assert formatted[1]["content"][0] == {
        "type": "output_text",
        "text": "after",
    }
