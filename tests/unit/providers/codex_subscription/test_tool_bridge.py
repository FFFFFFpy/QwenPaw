from __future__ import annotations

import asyncio

import pytest
from agentscope.message import ToolResultBlock, ToolResultState

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.tool_bridge import (
    ToolTurnBridge,
    format_dynamic_tools,
    get_tool_registry,
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look up a value",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    },
]


def test_formats_and_sanitizes_dynamic_tools():
    result = format_dynamic_tools(TOOLS)
    assert result == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Look up a value",
            "inputSchema": TOOLS[0]["function"]["parameters"],
        },
    ]


async def test_tool_request_result_round_trip():
    events: asyncio.Queue = asyncio.Queue()
    bridge = ToolTurnBridge(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_names={"lookup"},
        event_queue=events,
        timeout_seconds=1,
    )
    request = asyncio.create_task(
        bridge.handle_server_request(
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "callId": "call-1",
                "namespace": None,
                "tool": "lookup",
                "arguments": {"q": "value"},
            },
        ),
    )
    method, payload = await events.get()
    assert method == "dynamic_tool_call"
    assert payload["qwenpawCallId"].startswith("codex:")
    bridge.submit_results(
        [
            ToolResultBlock(
                id=payload["qwenpawCallId"],
                name="lookup",
                output="found",
                state=ToolResultState.SUCCESS,
            ),
        ],
    )
    assert await request == {
        "contentItems": [{"type": "inputText", "text": "found"}],
        "success": True,
    }


async def test_cross_thread_and_unknown_tool_are_rejected():
    bridge = ToolTurnBridge(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_names={"lookup"},
        event_queue=asyncio.Queue(),
    )
    with pytest.raises(CodexSubscriptionError):
        await bridge.handle_server_request(
            {
                "threadId": "other",
                "turnId": "turn-1",
                "callId": "call-1",
                "tool": "lookup",
                "arguments": {},
            },
        )
    with pytest.raises(CodexSubscriptionError):
        await bridge.handle_server_request(
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "callId": "call-1",
                "tool": "shell",
                "arguments": {},
            },
        )


async def test_denied_result_is_not_reported_as_success():
    events: asyncio.Queue = asyncio.Queue()
    bridge = ToolTurnBridge(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_names={"lookup"},
        event_queue=events,
    )
    request = asyncio.create_task(
        bridge.handle_server_request(
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "callId": "call-1",
                "tool": "lookup",
                "arguments": {},
            },
        ),
    )
    _, payload = await events.get()
    bridge.submit_results(
        [
            ToolResultBlock(
                id=payload["qwenpawCallId"],
                name="lookup",
                output="User denied this action",
                state=ToolResultState.DENIED,
            ),
        ],
    )
    result = await request
    assert result["success"] is False
    assert "denied" in result["contentItems"][0]["text"].lower()


async def test_tool_timeout_fails_pending_request():
    bridge = ToolTurnBridge(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_names={"lookup"},
        event_queue=asyncio.Queue(),
        timeout_seconds=0.01,
    )
    result = await bridge.handle_server_request(
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "callId": "call-1",
            "tool": "lookup",
            "arguments": {},
        },
    )
    assert result["success"] is False
    assert bridge.terminal_error is not None
    assert bridge.terminal_error.error_code == "CODEX_TOOL_WAIT_TIMEOUT"


async def test_concurrent_threads_route_tool_results_without_cross_talk(
    stub_runtime,
):
    stub_runtime.state = "ready"
    first_events: asyncio.Queue = asyncio.Queue()
    second_events: asyncio.Queue = asyncio.Queue()
    first = ToolTurnBridge(
        thread_id="thread-1",
        turn_id="turn-1",
        tool_names={"lookup"},
        event_queue=first_events,
    )
    second = ToolTurnBridge(
        thread_id="thread-2",
        turn_id="turn-2",
        tool_names={"lookup"},
        event_queue=second_events,
    )
    registry = get_tool_registry(stub_runtime)
    registry.add(first)
    registry.add(second)
    one = asyncio.create_task(
        stub_runtime.server_request(
            "item/tool/call",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "callId": "same-upstream-id",
                "tool": "lookup",
                "arguments": {"thread": 1},
            },
        ),
    )
    two = asyncio.create_task(
        stub_runtime.server_request(
            "item/tool/call",
            {
                "threadId": "thread-2",
                "turnId": "turn-2",
                "callId": "same-upstream-id",
                "tool": "lookup",
                "arguments": {"thread": 2},
            },
        ),
    )
    _, first_event = await first_events.get()
    _, second_event = await second_events.get()
    assert first_event["qwenpawCallId"] != second_event["qwenpawCallId"]
    first.submit_results(
        [
            ToolResultBlock(
                id=first_event["qwenpawCallId"],
                name="lookup",
                output="one",
                state=ToolResultState.SUCCESS,
            ),
        ],
    )
    second.submit_results(
        [
            ToolResultBlock(
                id=second_event["qwenpawCallId"],
                name="lookup",
                output="two",
                state=ToolResultState.SUCCESS,
            ),
        ],
    )
    one_result, two_result = await asyncio.gather(one, two)
    assert one_result["contentItems"][0]["text"] == "one"
    assert two_result["contentItems"][0]["text"] == "two"
