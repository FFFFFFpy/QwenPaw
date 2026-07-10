from __future__ import annotations

import asyncio

import pytest

from qwenpaw.providers.codex_subscription.errors import (
    CodexConnectionClosedError,
    CodexProtocolError,
    CodexRpcError,
    CodexSubscriptionError,
)
from qwenpaw.providers.codex_subscription.runtime import CodexAppServerRuntime


@pytest.fixture
async def runtime(fake_server_command: tuple[str, ...]):
    instance = CodexAppServerRuntime(command=fake_server_command)
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.fixture
def fake_server_command() -> tuple[str, ...]:
    import sys
    from pathlib import Path

    script = Path(__file__).with_name("fake_app_server.py")
    return (sys.executable, str(script))


async def test_request_and_out_of_order_responses(runtime):
    one, two = await asyncio.gather(
        runtime.request("test/reverse", {"value": 1}),
        runtime.request("test/reverse", {"value": 2}),
    )
    assert one == {"value": 1}
    assert two == {"value": 2}


async def test_notification_and_thread_filter(runtime):
    received: list[dict] = []
    unsubscribe = runtime.subscribe(
        "event/test",
        received.append,
        thread_id="expected",
    )
    await runtime.request("test/notify", {"threadId": "other"})
    await runtime.request("test/notify", {"threadId": "expected"})
    await asyncio.sleep(0)
    unsubscribe()
    assert received == [{"threadId": "expected"}]


async def test_server_request_and_unknown_method(runtime):
    calls: list[dict] = []

    async def handler(params: dict) -> dict:
        calls.append(params)
        return {"contentItems": [], "success": True}

    runtime.register_server_request("item/tool/call", handler)
    await runtime.request("test/serverRequest", {"tool": "allowed"})
    await runtime.request("test/unknownServerRequest", {})
    await asyncio.sleep(0.05)
    assert calls == [{"tool": "allowed"}]


async def test_remote_error(runtime):
    with pytest.raises(CodexRpcError) as caught:
        await runtime.request("test/error", {})
    assert caught.value.rpc_code == -32000


async def test_timeout_cleans_pending(runtime):
    with pytest.raises(CodexSubscriptionError) as caught:
        await runtime.request("test/never", {}, timeout=0.01)
    assert caught.value.error_code == "CODEX_TURN_FAILED"
    assert runtime.rpc.pending_count == 0


async def test_cancel_cleans_pending(runtime):
    task = asyncio.create_task(runtime.request("test/never", {}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.rpc.pending_count == 0


async def test_malformed_json_fails_connection(runtime):
    with pytest.raises(CodexProtocolError):
        await runtime.request("test/malformed", {})
    assert runtime.rpc.pending_count == 0


async def test_eof_fails_all_pending(runtime):
    pending = asyncio.create_task(runtime.request("test/never", {}))
    crashing = asyncio.create_task(runtime.request("test/crash", {}))
    results = await asyncio.gather(pending, crashing, return_exceptions=True)
    assert all(
        isinstance(item, CodexConnectionClosedError) for item in results
    )
    assert runtime.rpc.pending_count == 0


async def test_stderr_backpressure_does_not_block(runtime):
    assert await runtime.request("test/stderr", {}, timeout=5) == {}
