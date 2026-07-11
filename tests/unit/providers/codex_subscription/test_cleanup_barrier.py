from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile

import pytest
from agentscope.message import Msg, TextBlock

from qwenpaw.providers.codex_subscription.chat_model import (
    CodexSubscriptionChatModel,
)
from qwenpaw.providers.codex_subscription.credential import (
    CodexSubscriptionCredential,
)
from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.runtime import (
    CodexAppServerRuntime,
    RuntimeState,
)
from qwenpaw.providers.codex_subscription.turn_bridge import (
    CleanupState,
    TurnBridge,
)
from qwenpaw.providers.codex_subscription.tool_bridge import get_tool_registry


def _model(stub_runtime) -> CodexSubscriptionChatModel:
    stub_runtime.state = RuntimeState.READY
    stub_runtime.responses.update(
        {
            "account/read": {"account": {"type": "chatgpt"}},
            "model/list": {
                "data": [{"id": "codex-test", "inputModalities": ["text"]}],
            },
            "thread/start": {"thread": {"id": "thread-new"}},
        },
    )

    def turn_start(_params):
        loop = asyncio.get_running_loop()
        loop.call_soon(
            stub_runtime.emit,
            "turn/completed",
            {
                "threadId": "thread-new",
                "turn": {
                    "id": "turn-new",
                    "status": "completed",
                    "error": None,
                },
            },
        )
        return {"turn": {"id": "turn-new"}}

    stub_runtime.responses["turn/start"] = turn_start
    return CodexSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test"),
        model="codex-test",
        parameters=CodexSubscriptionChatModel.Parameters(),
        runtime=stub_runtime,
    )


async def test_new_call_waits_for_cleanup_barrier(stub_runtime):
    model = _model(stub_runtime)
    gate = asyncio.Event()
    barrier = stub_runtime.create_background_task(
        gate.wait(),
        name="delayed-cleanup",
    )
    model._cleanup_barrier = barrier

    call = asyncio.create_task(
        model(
            [Msg(name="user", role="user", content=[TextBlock(text="next")])],
        ),
    )
    await asyncio.sleep(0)
    assert not call.done()
    assert not stub_runtime.requests

    gate.set()
    response = await call
    _ = [chunk async for chunk in response]
    assert any(method == "thread/start" for method, _ in stub_runtime.requests)


async def test_new_call_waits_for_delayed_interrupt_response(stub_runtime):
    model = _model(stub_runtime)
    interrupt_started = asyncio.Event()
    release_interrupt = asyncio.Event()

    async def delayed_interrupt(_params):
        interrupt_started.set()
        await release_interrupt.wait()
        return {"confirmed": True}

    stub_runtime.responses["turn/interrupt"] = delayed_interrupt
    bridge = TurnBridge(
        stub_runtime,
        "thread-old",
        tempfile.TemporaryDirectory(prefix="delayed-interrupt-"),
    )
    bridge.turn_id = "turn-old"
    model._cleanup_bridge = bridge
    model._cleanup_barrier = bridge.start_cleanup(interrupt=True)
    await interrupt_started.wait()

    call = asyncio.create_task(
        model(
            [Msg(name="user", role="user", content=[TextBlock(text="next")])]
        )
    )
    await asyncio.sleep(0)
    assert not call.done()
    assert not any(
        method == "thread/start" for method, _ in stub_runtime.requests
    )

    release_interrupt.set()
    response = await call
    _ = [chunk async for chunk in response]
    assert bridge.interrupt_result == {"confirmed": True}


async def test_interrupt_failure_is_visible_and_retryable(stub_runtime):
    stub_runtime.state = RuntimeState.READY
    stub_runtime.responses["turn/interrupt"] = CodexSubscriptionError(
        "CODEX_TURN_FAILED",
        "fixture interrupt failure",
    )
    temporary = tempfile.TemporaryDirectory(prefix="cleanup-failure-")
    path = Path(temporary.name)
    bridge = TurnBridge(stub_runtime, "thread-old", temporary)
    bridge.turn_id = "turn-old"

    with pytest.raises(CodexSubscriptionError) as caught:
        await bridge.cleanup(interrupt=True)
    assert caught.value.error_code == "CODEX_INTERRUPT_FAILED"
    assert bridge.cleanup_state is CleanupState.FAILED
    assert bridge.cleanup_error is caught.value
    assert bridge.cleanup_complete.is_set()
    assert not path.exists()
    assert stub_runtime.turn_cleanup_error is caught.value

    with pytest.raises(CodexSubscriptionError) as repeated:
        await bridge.cleanup(interrupt=True)
    assert repeated.value is caught.value

    stub_runtime.responses["turn/interrupt"] = {}
    await bridge.cleanup(interrupt=True, retry=True)
    assert bridge.cleanup_state is CleanupState.COMPLETED
    assert bridge.interrupt_result == {}
    assert stub_runtime.turn_cleanup_error is None


@pytest.mark.parametrize(
    "upstream_error",
    [
        TimeoutError("fixture interrupt timeout"),
        CodexSubscriptionError("CODEX_TURN_FAILED", "fixture RPC error"),
    ],
)
async def test_interrupt_timeout_and_rpc_error_have_stable_code(
    stub_runtime,
    upstream_error,
):
    stub_runtime.state = RuntimeState.READY
    stub_runtime.responses["turn/interrupt"] = upstream_error
    bridge = TurnBridge(
        stub_runtime,
        "thread-old",
        tempfile.TemporaryDirectory(prefix="interrupt-error-"),
    )
    bridge.turn_id = "turn-old"

    with pytest.raises(CodexSubscriptionError) as caught:
        await bridge.cleanup(interrupt=True)
    assert caught.value.error_code == "CODEX_INTERRUPT_FAILED"
    assert caught.value.__cause__ is upstream_error


async def test_interrupt_failure_blocks_new_turn(stub_runtime):
    model = _model(stub_runtime)
    stub_runtime.responses["turn/interrupt"] = CodexSubscriptionError(
        "CODEX_TURN_FAILED",
        "fixture interrupt failure",
    )
    bridge = TurnBridge(
        stub_runtime,
        "thread-old",
        tempfile.TemporaryDirectory(prefix="blocked-turn-"),
    )
    bridge.turn_id = "turn-old"
    model._cleanup_bridge = bridge
    model._cleanup_barrier = bridge.start_cleanup(interrupt=True)
    with pytest.raises(CodexSubscriptionError) as cleanup:
        await asyncio.shield(model._cleanup_barrier)
    request_count = len(stub_runtime.requests)

    with pytest.raises(CodexSubscriptionError) as blocked:
        await model(
            [Msg(name="user", role="user", content=[TextBlock(text="next")])]
        )
    assert blocked.value is cleanup.value
    assert len(stub_runtime.requests) == request_count


async def test_runtime_stop_drains_tracked_background_tasks(
    fake_server_command: tuple[str, ...],
):
    runtime = CodexAppServerRuntime(command=fake_server_command)
    await runtime.start()
    gate = asyncio.Event()
    runtime.create_background_task(gate.wait(), name="cleanup-gate")
    stop = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0.01)
    assert not stop.done()
    assert runtime.background_task_count == 1

    gate.set()
    await stop
    assert runtime.background_task_count == 0
    assert runtime.state is RuntimeState.STOPPED


async def test_runtime_records_background_task_failure(
    fake_server_command: tuple[str, ...],
):
    runtime = CodexAppServerRuntime(command=fake_server_command)
    await runtime.start()

    async def fail() -> None:
        raise RuntimeError("fixture background failure")

    task = runtime.create_background_task(fail(), name="failing-cleanup")
    with pytest.raises(RuntimeError, match="fixture background failure"):
        await task
    await asyncio.sleep(0)
    assert len(runtime.background_errors) == 1
    await runtime.stop()


async def test_cleanup_task_records_local_exception(stub_runtime):
    stub_runtime.state = RuntimeState.READY
    bridge = TurnBridge(
        stub_runtime,
        "thread-old",
        tempfile.TemporaryDirectory(prefix="local-cleanup-error-"),
    )

    def fail_unsubscribe() -> None:
        raise RuntimeError("fixture unsubscribe failure")

    bridge.unsubscribers.append(fail_unsubscribe)
    with pytest.raises(CodexSubscriptionError) as caught:
        await bridge.cleanup()
    await asyncio.sleep(0)
    assert caught.value.error_code == "CODEX_CLEANUP_FAILED"
    assert bridge.cleanup_state is CleanupState.FAILED
    assert bridge.cleanup_complete.is_set()
    assert stub_runtime.background_errors == (caught.value,)


async def test_cleanup_is_idempotent(stub_runtime):
    stub_runtime.state = RuntimeState.READY
    temporary = tempfile.TemporaryDirectory(prefix="cleanup-idempotent-")
    bridge = TurnBridge(stub_runtime, "thread-old", temporary)
    bridge.turn_id = "turn-old"
    first = bridge.start_cleanup(interrupt=True)
    second = bridge.start_cleanup(interrupt=True)
    assert first is second
    await asyncio.shield(first)
    assert bridge.cleanup_state is CleanupState.COMPLETED
    assert [method for method, _ in stub_runtime.requests].count(
        "turn/interrupt"
    ) == 1


async def test_double_cancel_has_one_cleanup_and_no_subscription(stub_runtime):
    model = _model(stub_runtime)
    stub_runtime.responses["turn/start"] = {"turn": {"id": "turn-new"}}
    response = await model(
        [Msg(name="user", role="user", content=[TextBlock(text="wait")])]
    )
    pending = asyncio.create_task(anext(response))
    await asyncio.sleep(0.01)
    pending.cancel()
    pending.cancel()
    await pending
    await stub_runtime.wait_background_tasks()

    assert [method for method, _ in stub_runtime.requests].count(
        "turn/interrupt"
    ) == 1
    assert model._active_turn is None
    assert stub_runtime.background_task_count == 0
    assert all(
        not stub_runtime.handlers[method]
        for method in (
            "item/agentMessage/delta",
            "item/reasoning/summaryTextDelta",
            "error",
            "item/started",
            "turn/completed",
        )
    )


async def test_tool_timeout_removes_bridge_pending_calls_and_subscriptions(
    stub_runtime,
):
    stub_runtime.state = RuntimeState.READY
    bridge = TurnBridge(
        stub_runtime,
        "thread-tool",
        tempfile.TemporaryDirectory(prefix="tool-timeout-cleanup-"),
    )
    bridge.turn_id = "turn-tool"
    bridge.subscribe(("turn/completed",))

    def timeout_cleanup() -> None:
        bridge.start_cleanup(interrupt=True)

    bridge.enable_tools({"lookup"}, 0.01, timeout_cleanup)
    tool_bridge = bridge.tool_bridge
    assert tool_bridge is not None
    request = asyncio.create_task(
        stub_runtime.server_request(
            "item/tool/call",
            {
                "threadId": "thread-tool",
                "turnId": "turn-tool",
                "callId": "call-1",
                "tool": "lookup",
                "arguments": {},
            },
        )
    )
    method, _ = await bridge.next_event()
    assert method == "dynamic_tool_call"
    result = await request
    assert result["success"] is False
    await bridge.cleanup_complete.wait()
    await stub_runtime.wait_background_tasks()

    assert bridge.cleanup_state is CleanupState.COMPLETED
    assert bridge.tool_bridge is None
    assert not tool_bridge.pending_ids
    assert get_tool_registry(stub_runtime).active_count == 0
    assert all(not entries for entries in stub_runtime.handlers.values())


async def test_late_old_turn_notification_does_not_enter_new_bridge(
    stub_runtime,
):
    stub_runtime.state = RuntimeState.READY
    old = TurnBridge(
        stub_runtime,
        "thread-old",
        tempfile.TemporaryDirectory(prefix="old-turn-"),
    )
    old.subscribe(("item/agentMessage/delta",))
    await old.cleanup()

    new = TurnBridge(
        stub_runtime,
        "thread-new",
        tempfile.TemporaryDirectory(prefix="new-turn-"),
    )
    new.subscribe(("item/agentMessage/delta",))
    stub_runtime.emit(
        "item/agentMessage/delta",
        {"threadId": "thread-old", "turnId": "turn-old", "delta": "late"},
    )
    await asyncio.sleep(0)
    assert new.queue.empty()

    stub_runtime.emit(
        "item/agentMessage/delta",
        {"threadId": "thread-new", "turnId": "turn-new", "delta": "new"},
    )
    method, params = await new.next_event(timeout=0.1)
    assert method == "item/agentMessage/delta"
    assert params["delta"] == "new"
    await new.cleanup()


@pytest.fixture
def fake_server_command() -> tuple[str, ...]:
    script = Path(__file__).with_name("fake_app_server.py")
    return (sys.executable, str(script))
