# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging

import pytest
from agentscope.message import Msg, TextBlock

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError

from .test_chat_model_stream import make_model, schedule_completed_turn


def _message(text: str = "hi") -> list[Msg]:
    return [Msg(name="user", role="user", content=[TextBlock(text=text)])]


def _diagnostic_rows(caplog) -> list[dict]:
    rows = []
    for record in caplog.records:
        marker = "codex_latency "
        message = record.getMessage()
        if marker in message:
            rows.append(json.loads(message.split(marker, 1)[1]))
    return rows


async def test_normal_request_starts_exactly_one_turn(stub_runtime):
    model = make_model(stub_runtime)
    schedule_completed_turn(stub_runtime)

    response = await model(_message(), session_id="normal-session")
    _ = [chunk async for chunk in response]

    assert (
        sum(method == "turn/start" for method, _ in stub_runtime.requests) == 1
    )


async def test_concurrent_second_turn_same_session_is_rejected(
    stub_runtime,
    caplog,
):
    caplog.set_level(
        logging.INFO,
        logger="qwenpaw.providers.codex_subscription.diagnostics",
    )
    first = make_model(stub_runtime)
    second = make_model(stub_runtime)
    stub_runtime.responses["turn/start"] = {"turn": {"id": "turn-1"}}

    response = await first(_message("first"), session_id="same-session")
    pending = asyncio.create_task(anext(response), name="first-codex-turn")
    await asyncio.sleep(0)

    with pytest.raises(CodexSubscriptionError) as caught:
        await second(_message("second"), session_id="same-session")
    assert caught.value.error_code == "CODEX_CONCURRENT_TURN"
    assert (
        sum(method == "turn/start" for method, _ in stub_runtime.requests) == 1
    )
    rejected = next(
        row
        for row in _diagnostic_rows(caplog)
        if row["event"] == "concurrent_turn_rejected"
    )
    assert rejected["asyncio_task_name"]
    assert rejected["model_instance_id"] == second.model_instance_id
    assert rejected["call_id"]
    assert "test_concurrent_second_turn" in rejected["call_stack"]

    stub_runtime.emit(
        "turn/completed",
        {
            "threadId": "thread-1",
            "turn": {"id": "turn-1", "status": "completed", "error": None},
        },
    )
    assert (await pending).is_last


async def test_different_sessions_can_run_in_parallel(stub_runtime):
    first = make_model(stub_runtime)
    second = make_model(stub_runtime)
    schedule_completed_turn(stub_runtime)

    async def run(model, session_id):
        response = await model(_message(), session_id=session_id)
        return [chunk async for chunk in response]

    await asyncio.gather(run(first, "session-a"), run(second, "session-b"))
    assert (
        sum(method == "turn/start" for method, _ in stub_runtime.requests) == 2
    )


async def test_failed_turn_releases_session_registry(stub_runtime):
    model = make_model(stub_runtime)

    def failed_turn(_params):
        asyncio.get_running_loop().call_soon(
            stub_runtime.emit,
            "turn/completed",
            {
                "threadId": "thread-1",
                "turn": {
                    "id": "turn-1",
                    "status": "failed",
                    "error": {"message": "fixture failure"},
                },
            },
        )
        return {"turn": {"id": "turn-1"}}

    stub_runtime.responses["turn/start"] = failed_turn
    with pytest.raises(CodexSubscriptionError):
        response = await model(_message(), session_id="retry-session")
        _ = [chunk async for chunk in response]

    schedule_completed_turn(stub_runtime)
    response = await model(_message(), session_id="retry-session")
    _ = [chunk async for chunk in response]
    assert (
        sum(method == "turn/start" for method, _ in stub_runtime.requests) == 2
    )


@pytest.mark.parametrize(
    ("mode", "has_tools"),
    [("off", False), ("all", True)],
)
async def test_dynamic_tool_mode_controls_thread_payload(
    stub_runtime,
    mode,
    has_tools,
):
    stub_runtime.settings.codex_dynamic_tool_mode = mode
    model = make_model(stub_runtime)
    schedule_completed_turn(stub_runtime)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "safe lookup",
                "parameters": {"type": "object"},
            },
        },
    ]

    response = await model(
        _message(),
        tools=tools,
        session_id=f"tools-{mode}",
    )
    _ = [chunk async for chunk in response]
    params = next(
        params
        for method, params in stub_runtime.requests
        if method == "thread/start"
    )
    assert ("dynamicTools" in params) is has_tools


async def test_terminal_timings_are_logged_once_and_are_secret_safe(
    stub_runtime,
    caplog,
):
    caplog.set_level(
        logging.INFO,
        logger="qwenpaw.providers.codex_subscription.diagnostics",
    )
    user_canary = "USER_TEXT_MUST_NOT_APPEAR"
    credential_canary = "CREDENTIAL_MUST_NOT_APPEAR"
    model = make_model(stub_runtime)
    model.credential.id = credential_canary

    def turn_start(_params):
        loop = asyncio.get_running_loop()
        for delta in ("think-1", "think-2"):
            loop.call_soon(
                stub_runtime.emit,
                "item/reasoning/summaryTextDelta",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "reason",
                    "delta": delta,
                },
            )
        for delta in ("text-1", "text-2"):
            loop.call_soon(
                stub_runtime.emit,
                "item/agentMessage/delta",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "text",
                    "delta": delta,
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
    response = await model(
        _message(user_canary),
        session_id="timing-session",
    )
    _ = [chunk async for chunk in response]
    rows = _diagnostic_rows(caplog)
    events = [row["event"] for row in rows]
    assert {
        "call_enter",
        "runtime_ready",
        "account_ready",
        "model_list_ready",
        "thread_start_sent",
        "thread_start_ack",
        "turn_start_sent",
        "turn_start_ack",
        "first_reasoning_delta",
        "first_text_delta",
        "turn_completed",
        "cleanup_completed",
    } <= set(events)
    for event in (
        "first_reasoning_delta",
        "first_text_delta",
        "turn_completed",
        "cleanup_completed",
    ):
        assert events.count(event) == 1
    required = {
        "call_id",
        "session_id",
        "model_instance_id",
        "asyncio_task_name",
        "model_id",
        "reasoning_effort",
        "dynamic_tool_count",
        "message_count",
        "message_text_bytes",
        "thread_id",
        "turn_id",
        "elapsed_ms",
    }
    assert rows and all(required <= row.keys() for row in rows)
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert user_canary not in rendered
    assert credential_canary not in rendered
