from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from agentscope.message import Base64Source, DataBlock, Msg, TextBlock
from agentscope.model import FinishedReason

from qwenpaw.providers.codex_subscription.chat_model import (
    CodexSubscriptionChatModel,
    _map_turn_error,
)
from qwenpaw.providers.codex_subscription.credential import (
    CodexSubscriptionCredential,
)
from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError


def make_model(stub_runtime, *, relay_reasoning: bool = True):
    stub_runtime.state = "ready"
    stub_runtime.responses.update(
        {
            "account/read": {
                "account": {
                    "type": "chatgpt",
                    "email": "person@example.test",
                    "planType": "plus",
                },
            },
            "model/list": {
                "data": [
                    {
                        "id": "codex-test",
                        "inputModalities": ["text", "image"],
                    },
                ],
                "nextCursor": None,
            },
            "thread/start": {"thread": {"id": "thread-1"}},
        },
    )
    return CodexSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test"),
        model="codex-test",
        parameters=CodexSubscriptionChatModel.Parameters(),
        runtime=stub_runtime,
        relay_reasoning=relay_reasoning,
    )


def schedule_completed_turn(stub_runtime, *, include_reasoning: bool = False):
    def turn_start(_params):
        loop = asyncio.get_running_loop()
        if include_reasoning:
            loop.call_soon(
                stub_runtime.emit,
                "item/reasoning/summaryTextDelta",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "reason-1",
                    "delta": "thinking",
                },
            )
        loop.call_soon(
            stub_runtime.emit,
            "item/agentMessage/delta",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "item-1",
                "delta": "hello",
            },
        )
        loop.call_soon(
            stub_runtime.emit,
            "turn/completed",
            {
                "threadId": "thread-1",
                "turn": {
                    "id": "turn-1",
                    "status": "completed",
                    "error": None,
                },
            },
        )
        return {"turn": {"id": "turn-1"}}

    stub_runtime.responses["turn/start"] = turn_start


async def test_streams_text_and_reasoning_and_cleans_up(stub_runtime):
    model = make_model(stub_runtime)
    schedule_completed_turn(stub_runtime, include_reasoning=True)
    response = await model(
        [Msg(name="user", role="user", content=[TextBlock(text="hi")])],
    )
    chunks = [chunk async for chunk in response]
    assert any(
        block.type == "text" and block.text == "hello"
        for chunk in chunks
        for block in chunk.content
    )
    assert any(
        block.type == "thinking" and block.thinking == "thinking"
        for chunk in chunks
        for block in chunk.content
    )
    assert ("thread/unsubscribe", {"threadId": "thread-1"}) in (
        stub_runtime.requests
    )
    thread_params = next(
        params
        for method, params in stub_runtime.requests
        if method == "thread/start"
    )
    assert thread_params["sandbox"] == {
        "type": "readOnly",
        "networkAccess": False,
        "access": {
            "type": "restricted",
            "readableRoots": [thread_params["cwd"]],
        },
    }
    assert thread_params["runtimeWorkspaceRoots"] == [thread_params["cwd"]]
    assert thread_params["environments"] == []


async def test_reasoning_can_be_suppressed(stub_runtime):
    model = make_model(stub_runtime, relay_reasoning=False)
    schedule_completed_turn(stub_runtime, include_reasoning=True)
    response = await model(
        [Msg(name="user", role="user", content=[TextBlock(text="hi")])],
    )
    chunks = [chunk async for chunk in response]
    assert not any(
        block.type == "thinking" for chunk in chunks for block in chunk.content
    )


async def test_tools_are_never_silently_discarded(stub_runtime):
    model = make_model(stub_runtime)
    stub_runtime.capabilities = replace(
        stub_runtime.capabilities,
        dynamic_tools=False,
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        response = await model(
            [Msg(name="user", role="user", content=[TextBlock(text="hi")])],
            tools=[{"type": "function", "function": {"name": "lookup"}}],
        )
        _ = [chunk async for chunk in response]
    assert caught.value.error_code == "CODEX_TOOL_BRIDGE_UNSUPPORTED"


async def test_cancellation_interrupts_remote_turn(stub_runtime):
    model = make_model(stub_runtime)
    stub_runtime.responses["turn/start"] = {"turn": {"id": "turn-1"}}
    response = await model(
        [Msg(name="user", role="user", content=[TextBlock(text="wait")])],
    )
    next_chunk = asyncio.create_task(anext(response))
    await asyncio.sleep(0.01)
    next_chunk.cancel()
    result = await next_chunk
    assert result["finished_reason"] is FinishedReason.INTERRUPTED
    await asyncio.sleep(0)
    assert (
        "turn/interrupt",
        {"threadId": "thread-1", "turnId": "turn-1"},
    ) in stub_runtime.requests


async def test_builtin_side_effect_event_is_blocked(stub_runtime):
    model = make_model(stub_runtime)

    def turn_start(_params):
        asyncio.get_running_loop().call_soon(
            stub_runtime.emit,
            "item/started",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "commandExecution"},
            },
        )
        return {"turn": {"id": "turn-1"}}

    stub_runtime.responses["turn/start"] = turn_start
    with pytest.raises(CodexSubscriptionError) as caught:
        response = await model(
            [Msg(name="user", role="user", content=[TextBlock(text="hi")])],
        )
        _ = [chunk async for chunk in response]
    assert caught.value.error_code == "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED"
    assert (
        "turn/interrupt",
        {"threadId": "thread-1", "turnId": "turn-1"},
    ) in stub_runtime.requests


async def test_temporary_working_directory_is_removed(stub_runtime):
    model = make_model(stub_runtime)
    captured: Path | None = None

    def thread_start(params):
        nonlocal captured
        captured = Path(params["cwd"])
        assert captured.is_dir()
        return {"thread": {"id": "thread-1"}}

    stub_runtime.responses["thread/start"] = thread_start
    schedule_completed_turn(stub_runtime)
    response = await model(
        [Msg(name="user", role="user", content=[TextBlock(text="hi")])],
    )
    _ = [chunk async for chunk in response]
    assert captured is not None
    assert not captured.exists()


@pytest.mark.parametrize(
    ("call_effort", "saved_effort", "default_effort", "expected"),
    [
        ("high", "low", "medium", "high"),
        (None, "low", "medium", "low"),
        (None, None, "medium", "medium"),
        (None, None, None, None),
    ],
)
async def test_reasoning_effort_priority_reaches_turn_start(
    stub_runtime,
    call_effort,
    saved_effort,
    default_effort,
    expected,
):
    model = make_model(stub_runtime)
    model.parameters.reasoning_effort = saved_effort
    model_row = stub_runtime.responses["model/list"]["data"][0]
    model_row["defaultReasoningEffort"] = default_effort
    model_row["supportedReasoningEfforts"] = [
        {"reasoningEffort": value} for value in ("low", "medium", "high")
    ]
    schedule_completed_turn(stub_runtime)
    kwargs = (
        {"reasoning_effort": call_effort} if call_effort is not None else {}
    )
    response = await model(
        [Msg(name="user", role="user", content=[TextBlock(text="hi")])],
        **kwargs,
    )
    _ = [chunk async for chunk in response]
    turn_params = next(
        params
        for method, params in stub_runtime.requests
        if method == "turn/start"
    )
    if expected is None:
        assert "effort" not in turn_params
    else:
        assert turn_params["effort"] == expected


async def test_invalid_reasoning_effort_is_rejected_before_turn(stub_runtime):
    model = make_model(stub_runtime)
    model_row = stub_runtime.responses["model/list"]["data"][0]
    model_row["defaultReasoningEffort"] = "medium"
    model_row["supportedReasoningEfforts"] = [
        {"reasoningEffort": "low"},
        {"reasoningEffort": "medium"},
    ]
    with pytest.raises(CodexSubscriptionError) as caught:
        response = await model(
            [Msg(name="user", role="user", content=[TextBlock(text="hi")])],
            reasoning_effort="impossible",
        )
        _ = [chunk async for chunk in response]
    assert caught.value.error_code == "CODEX_REASONING_EFFORT_UNSUPPORTED"
    assert not any(
        method == "thread/start" for method, _ in stub_runtime.requests
    )


async def test_complete_payload_budget_blocks_before_thread_start(
    stub_runtime,
):
    model = make_model(stub_runtime)
    stub_runtime.settings.max_message_bytes = 100
    message = Msg(
        name="user",
        role="user",
        content=[
            TextBlock(text="history" * 20),
            DataBlock(
                source=Base64Source(
                    data="a" * 80,
                    media_type="image/png",
                )
            ),
        ],
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "tool schema" * 20,
                "parameters": {"type": "object"},
            },
        }
    ]

    with pytest.raises(CodexSubscriptionError) as caught:
        response = await model([message], tools=tools)
        _ = [chunk async for chunk in response]
    assert caught.value.error_code == "CODEX_REQUEST_TOO_LARGE"
    assert caught.value.details["attachment_count"] == 1
    assert set(caught.value.details) == {
        "payload_bytes",
        "limit_bytes",
        "attachment_count",
    }
    assert not any(
        method == "thread/start" for method, _ in stub_runtime.requests
    )


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("usage limit reached", "CODEX_USAGE_LIMIT_EXCEEDED"),
        ("authentication expired", "CODEX_NOT_LOGGED_IN"),
        ("context window too large", "CODEX_CONTEXT_WINDOW_EXCEEDED"),
        ("model was removed", "CODEX_MODEL_UNAVAILABLE"),
    ],
)
def test_turn_errors_have_stable_codes(message: str, code: str):
    assert (
        _map_turn_error({"message": message}, "codex-test").error_code == code
    )
