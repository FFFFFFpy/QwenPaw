"""Lifecycle manager for the official local Codex App Server process."""

from __future__ import annotations

import asyncio
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
                                self.capabilities.dynamic_tools,
                            ),
                            "requestAttestation": False,
                        },
                    },
                )
                await self._rpc.notify("initialized", {})
                self._state = RuntimeState.READY
                logger.info(
                    "Codex runtime ready generation=%s version=%s schema=%s "
                    "platform=%s/%s",
                    self.generation_id,
                    self.binary_version or "unknown",
                    self.capabilities.schema_fingerprint[:12],
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
                elif exc.error_code == "CODEX_PROTOCOL_INCOMPATIBLE":
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
