from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.runtime import (
    CodexAppServerRuntime,
    RuntimeState,
)
from qwenpaw.providers.codex_subscription.schema_capabilities import (
    CodexCapabilities,
)
from qwenpaw.providers.codex_subscription.settings import (
    CodexSubscriptionSettings,
    discover_codex_binary,
)


def test_binary_discovery_requires_absolute_custom_path(tmp_path: Path):
    with pytest.raises(CodexSubscriptionError, match="absolute"):
        discover_codex_binary("relative/codex")
    with pytest.raises(CodexSubscriptionError, match="executable"):
        discover_codex_binary(str(tmp_path / "missing"))


def test_binary_discovery_accepts_executable(tmp_path: Path):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | 0o111)
    assert discover_codex_binary(str(binary)) == str(binary.resolve())


async def test_runtime_initializes_and_stops():
    script = Path(__file__).with_name("fake_app_server.py")
    runtime = CodexAppServerRuntime(command=(sys.executable, str(script)))
    assert runtime.state is RuntimeState.STOPPED
    await runtime.start()
    assert runtime.state is RuntimeState.READY
    assert runtime.initialize_result["platformOs"] == "linux"
    await runtime.stop()
    assert runtime.state is RuntimeState.STOPPED


async def test_runtime_rejects_missing_restricted_sandbox_before_spawn():
    capabilities = CodexCapabilities.focused_contract(
        restricted_read_sandbox=False,
    )
    runtime = CodexAppServerRuntime(
        command=(sys.executable, "must-not-spawn"),
        capabilities=capabilities,
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        await runtime.start()
    assert caught.value.error_code == "CODEX_SANDBOX_UNSUPPORTED"
    assert runtime.state is RuntimeState.INCOMPATIBLE


async def test_runtime_rejects_requests_before_initialize():
    runtime = CodexAppServerRuntime(command=(sys.executable, "missing"))
    with pytest.raises(CodexSubscriptionError) as caught:
        await runtime.request("model/list", {})
    assert caught.value.error_code == "CODEX_NOT_INITIALIZED"


async def test_runtime_start_failure_has_stable_error(tmp_path: Path):
    binary = tmp_path / "bad"
    binary.write_text("not executable", encoding="utf-8")
    os.chmod(binary, 0o755)
    runtime = CodexAppServerRuntime(command=(str(binary),))
    with pytest.raises(CodexSubscriptionError) as caught:
        await runtime.start()
    assert caught.value.error_code == "CODEX_RUNTIME_START_FAILED"
    assert runtime.state is RuntimeState.CRASHED


async def test_oversized_server_line_closes_runtime_connection():
    script = Path(__file__).with_name("fake_app_server.py")
    runtime = CodexAppServerRuntime(
        CodexSubscriptionSettings(max_message_bytes=65536),
        command=(sys.executable, str(script)),
    )
    await runtime.start()
    try:
        with pytest.raises(CodexSubscriptionError) as caught:
            await runtime.request("test/oversize", {})
        assert caught.value.error_code == "CODEX_PROTOCOL_INCOMPATIBLE"
    finally:
        await runtime.stop()


async def test_windows_cleanup_falls_back_to_terminate(monkeypatch):
    class Stdin:
        closed = False

        def is_closing(self) -> bool:
            return self.closed

        def close(self) -> None:
            self.closed = True

    class Process:
        def __init__(self) -> None:
            self.stdin = Stdin()
            self.wait_count = 0
            self.terminated = False

        async def wait(self) -> int:
            self.wait_count += 1
            if self.wait_count == 1:
                raise TimeoutError
            return 0

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            raise AssertionError("terminate should be sufficient")

    runtime = CodexAppServerRuntime(command=(sys.executable, "unused"))
    process = Process()
    runtime._process = process  # type: ignore[assignment]
    monkeypatch.setattr(sys, "platform", "win32")
    await runtime._cleanup_process()
    assert process.stdin.closed
    assert process.terminated
