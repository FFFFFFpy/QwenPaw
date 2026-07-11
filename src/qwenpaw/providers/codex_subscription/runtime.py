"""Lifecycle manager for the official local Codex App Server process."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from enum import Enum
import logging
from pathlib import Path
import secrets
import signal
import subprocess
import sys
from typing import Any, Callable, Sequence

from qwenpaw.__version__ import __version__

from .errors import CodexSubscriptionError
from .rpc_client import (
    JsonRpcClient,
    NotificationHandler,
    ServerRequestHandler,
    Unsubscribe,
)
from .schema_capabilities import (
    CodexCapabilities,
    detect_binary_capabilities,
)
from .settings import CodexSubscriptionSettings, discover_codex_binary

logger = logging.getLogger(__name__)

_shared_runtime: "CodexAppServerRuntime | None" = None


class RuntimeState(str, Enum):
    NOT_INSTALLED = "not_installed"
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    CRASHED = "crashed"
    INCOMPATIBLE = "incompatible"
    STOPPING = "stopping"


class CodexAppServerRuntime:
    """One process-safe App Server runtime, shared by provider operations."""

    def __init__(
        self,
        settings: CodexSubscriptionSettings | None = None,
        *,
        command: Sequence[str] | None = None,
        capabilities: CodexCapabilities | None = None,
        client_version: str = __version__,
    ) -> None:
        self.settings = settings or CodexSubscriptionSettings()
        self._command = tuple(command) if command else None
        self._provided_capabilities = capabilities
        self._client_version = client_version
        self._state = RuntimeState.STOPPED
        self._state_lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._rpc: JsonRpcClient | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._background_errors: list[BaseException] = []
        self._turn_cleanup_error: CodexSubscriptionError | None = None
        self._stopping = False
        self.initialize_result: dict[str, Any] = {}
        self.capabilities: CodexCapabilities | None = None
        self.binary_path: str | None = None
        self.binary_version: str | None = None
        self.generation_id = ""

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def rpc(self) -> JsonRpcClient:
        if self._rpc is None:
            raise CodexSubscriptionError(
                "CODEX_NOT_INITIALIZED",
                "Codex App Server is not initialized",
            )
        return self._rpc

    @property
    def background_task_count(self) -> int:
        return len(self._background_tasks)

    @property
    def background_errors(self) -> tuple[BaseException, ...]:
        return tuple(self._background_errors)

    @property
    def turn_cleanup_error(self) -> CodexSubscriptionError | None:
        return self._turn_cleanup_error

    def create_background_task(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str,
    ) -> asyncio.Task[Any]:
        """Create a runtime-owned task whose completion is always observed."""

        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)

        def completed(done: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(done)
            if done.cancelled():
                return
            error = done.exception()
            if error is not None:
                self._background_errors.append(error)
                logger.warning(
                    "Codex background task failed generation=%s task=%s",
                    self.generation_id,
                    done.get_name(),
                )

        task.add_done_callback(completed)
        return task

    async def wait_background_tasks(self) -> None:
        """Wait for runtime-owned tasks, including spawned cleanup, to end."""

        while self._background_tasks:
            tasks = tuple(self._background_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)

    def mark_turn_cleanup_failed(
        self,
        error: CodexSubscriptionError,
    ) -> None:
        self._turn_cleanup_error = error

    def clear_turn_cleanup_error(
        self,
        error: CodexSubscriptionError | None = None,
    ) -> None:
        if error is None or self._turn_cleanup_error is error:
            self._turn_cleanup_error = None

    def assert_turn_start_allowed(self) -> None:
        if self._turn_cleanup_error is not None:
            raise self._turn_cleanup_error

    async def start(self) -> None:
        async with self._state_lock:
            if self._state is RuntimeState.READY:
                return
            self._state = RuntimeState.STARTING
            self._stopping = False
            try:
                if self._process is not None or self._rpc is not None:
                    await self._cleanup_process()
                if self._command is None:
                    binary = discover_codex_binary(
                        self.settings.binary_path or None,
                    )
                    self.binary_path = binary
                    self.binary_version = await self._read_binary_version(
                        binary
                    )
                    self.capabilities = (
                        self._provided_capabilities
                        or await detect_binary_capabilities(binary)
                    )
                    command: tuple[str, ...] = (
                        binary,
                        "app-server",
                        "--stdio",
                    )
                else:
                    command = self._command
                    self.binary_path = str(Path(command[0]).resolve())
                    self.capabilities = (
                        self._provided_capabilities
                        or CodexCapabilities.focused_contract()
                    )
                capabilities = self.capabilities
                if capabilities is None:
                    raise CodexSubscriptionError(
                        "CODEX_PROTOCOL_INCOMPATIBLE",
                        "Codex capabilities were not detected",
                    )
                capabilities.validate_required_surface()
                self.generation_id = secrets.token_hex(4)
                subprocess_kwargs: dict[str, Any] = {}
                if sys.platform == "win32":
                    subprocess_kwargs["creationflags"] = (
                        subprocess.CREATE_NEW_PROCESS_GROUP
                    )
                self._process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=self.settings.max_message_bytes + 1,
                    **subprocess_kwargs,
                )
                if self._process.stdout is None or self._process.stdin is None:
                    raise RuntimeError("stdio pipes unavailable")
                self._rpc = JsonRpcClient(
                    self._process.stdout,
                    self._process.stdin,
                    max_message_bytes=self.settings.max_message_bytes,
                    default_timeout=self.settings.request_timeout_seconds,
                )
                self._register_blocked_server_requests()
                self._rpc.start()
                if self._process.stderr is not None:
                    self._stderr_task = asyncio.create_task(
                        self._drain_stderr(self._process.stderr),
                        name="codex-stderr-drain",
                    )
                self.initialize_result = await self._rpc.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": self.settings.client_name,
                            "title": "QwenPaw",
                            "version": self._client_version,
                        },
                        "capabilities": {
                            "experimentalApi": bool(
                                capabilities.dynamic_tools,
                            ),
                            "requestAttestation": False,
                        },
                    },
                )
                await self._rpc.notify("initialized", {})
                self._turn_cleanup_error = None
                self._background_errors.clear()
                self._state = RuntimeState.READY
                logger.info(
                    "Codex runtime ready generation=%s version=%s schema=%s "
                    "platform=%s/%s",
                    self.generation_id,
                    self.binary_version or "unknown",
                    capabilities.schema_fingerprint[:12],
                    self.initialize_result.get("platformFamily", "unknown"),
                    self.initialize_result.get("platformOs", "unknown"),
                )
                self._monitor_task = asyncio.create_task(
                    self._monitor_process(self._process),
                    name="codex-process-monitor",
                )
            except CodexSubscriptionError as exc:
                await self._cleanup_process()
                if exc.error_code == "CODEX_NOT_INSTALLED":
                    self._state = RuntimeState.NOT_INSTALLED
                elif exc.error_code in {
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    "CODEX_SANDBOX_UNSUPPORTED",
                }:
                    self._state = RuntimeState.INCOMPATIBLE
                else:
                    self._state = RuntimeState.CRASHED
                raise
            except Exception as exc:
                await self._cleanup_process()
                self._state = RuntimeState.CRASHED
                raise CodexSubscriptionError(
                    "CODEX_RUNTIME_START_FAILED",
                    "Failed to start the Codex App Server",
                    remediation="Check the configured Codex binary and retry.",
                ) from exc

    async def stop(self) -> None:
        async with self._state_lock:
            if (
                self._state is RuntimeState.STOPPED
                and not self._background_tasks
            ):
                return
            # Cleanup requests need a READY RPC connection. Drain them before
            # transitioning the runtime to STOPPING or closing stdio.
            await self.wait_background_tasks()
            if self._state is RuntimeState.STOPPED:
                return
            self._state = RuntimeState.STOPPING
            self._stopping = True
            await self._cleanup_process()
            self._state = RuntimeState.STOPPED

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._state is not RuntimeState.READY or self._rpc is None:
            raise CodexSubscriptionError(
                "CODEX_NOT_INITIALIZED",
                "Codex App Server is not initialized",
            )
        try:
            return await self._rpc.request(method, params, timeout)
        except TimeoutError as exc:
            raise CodexSubscriptionError(
                "CODEX_TURN_FAILED",
                f"Codex App Server request timed out: {method}",
            ) from exc

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        if self._state is not RuntimeState.READY or self._rpc is None:
            raise CodexSubscriptionError(
                "CODEX_NOT_INITIALIZED",
                "Codex App Server is not initialized",
            )
        await self._rpc.notify(method, params)

    def subscribe(
        self,
        method: str,
        handler: NotificationHandler,
        *,
        thread_id: str | None = None,
    ) -> Unsubscribe:
        return self.rpc.subscribe(method, handler, thread_id=thread_id)

    def register_server_request(
        self,
        method: str,
        handler: ServerRequestHandler,
    ) -> None:
        self.rpc.register_server_request(method, handler)

    def _register_blocked_server_requests(self) -> None:
        if self._rpc is None:
            return

        async def reject_side_effect(
            params: dict[str, Any],
        ) -> dict[str, Any]:
            del params
            raise CodexSubscriptionError(
                "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED",
                "QwenPaw rejected a Codex built-in side-effect request",
            )

        for method in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
            "mcpServer/elicitation/request",
            "execCommandApproval",
            "applyPatchApproval",
        ):
            self._rpc.register_server_request(method, reject_side_effect)

    async def redetect(self) -> None:
        await self.stop()
        await self.start()

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while await stream.readline():
                # Never log raw stderr: it may include account or prompt data.
                logger.debug(
                    "Codex App Server diagnostic received (generation=%s)",
                    self.generation_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "Codex stderr drain stopped (generation=%s)",
                self.generation_id,
            )

    async def _monitor_process(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        await process.wait()
        if not self._stopping and process is self._process:
            self._state = RuntimeState.CRASHED

    async def _cleanup_process(self) -> None:
        current = asyncio.current_task()
        if self._rpc is not None:
            await self._rpc.close()
            self._rpc = None
        process = self._process
        self._process = None
        if process is not None:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 3.0)
            except asyncio.TimeoutError:
                if sys.platform == "win32":
                    try:
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    except (AttributeError, ProcessLookupError):
                        process.terminate()
                else:
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        for task in (self._stderr_task, self._monitor_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._stderr_task = None
        self._monitor_task = None

    @staticmethod
    async def _read_binary_version(binary: str) -> str | None:
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                5.0,
            )
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()
            return None
        if process.returncode != 0:
            return None
        version = stdout.decode("utf-8", errors="replace").strip()
        return version[:200] or None


def get_codex_runtime() -> CodexAppServerRuntime:
    """Return the process-wide App Server runtime with non-secret settings."""

    global _shared_runtime
    if _shared_runtime is None:
        from qwenpaw.constant import SECRET_DIR

        settings_path = SECRET_DIR / "codex_subscription" / "settings.json"
        settings = CodexSubscriptionSettings.load(settings_path)
        _shared_runtime = CodexAppServerRuntime(settings)
    return _shared_runtime


def set_codex_runtime_for_testing(
    runtime: CodexAppServerRuntime | None,
) -> None:
    global _shared_runtime
    _shared_runtime = runtime
