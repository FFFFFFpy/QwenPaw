"""Per-generation turn state and cleanup for the Codex adapter."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import tempfile
from typing import Any, Callable

from .errors import CodexSubscriptionError
from .runtime import RuntimeState
from .tool_bridge import ToolTurnBridge, get_tool_registry


class CleanupState(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TurnBridge:
    runtime: Any
    thread_id: str
    temporary: tempfile.TemporaryDirectory[str]
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = field(
        default_factory=asyncio.Queue,
    )
    turn_id: str | None = None
    unsubscribers: list[Callable[[], None]] = field(default_factory=list)
    backlog: deque[tuple[str, dict[str, Any]]] = field(default_factory=deque)
    tool_bridge: ToolTurnBridge | None = None
    cleanup_state: CleanupState = CleanupState.NOT_STARTED
    cleanup_task: asyncio.Task[None] | None = None
    interrupt_result: dict[str, Any] | None = None
    cleanup_error: CodexSubscriptionError | None = None
    cleanup_complete: asyncio.Event = field(default_factory=asyncio.Event)

    def subscribe(self, methods: tuple[str, ...]) -> None:
        for method in methods:
            self.unsubscribers.append(
                self.runtime.subscribe(
                    method,
                    self._handler(method),
                    thread_id=self.thread_id,
                ),
            )

    def _handler(self, method: str):
        def handler(params: dict[str, Any]) -> None:
            self.queue.put_nowait((method, params))

        return handler

    def enable_tools(
        self,
        names: set[str],
        timeout_seconds: float,
        timeout_callback: Callable[[], None],
    ) -> None:
        if self.turn_id is None:
            raise RuntimeError("turn id must be set before enabling tools")
        self.tool_bridge = ToolTurnBridge(
            thread_id=self.thread_id,
            turn_id=self.turn_id,
            tool_names=names,
            event_queue=self.queue,
            timeout_seconds=timeout_seconds,
            timeout_callback=timeout_callback,
        )
        get_tool_registry(self.runtime).add(self.tool_bridge)

    async def next_event(
        self, timeout: float = 600.0
    ) -> tuple[str, dict[str, Any]]:
        if self.backlog:
            return self.backlog.popleft()
        return await asyncio.wait_for(self.queue.get(), timeout)

    def start_cleanup(
        self,
        *,
        interrupt: bool = False,
        retry: bool = False,
    ) -> asyncio.Task[None]:
        if self.cleanup_task is not None:
            if self.cleanup_state in {
                CleanupState.RUNNING,
                CleanupState.COMPLETED,
            }:
                return self.cleanup_task
            if self.cleanup_state is CleanupState.FAILED and not retry:
                return self.cleanup_task

        recovery_error = self.cleanup_error
        self.cleanup_state = CleanupState.RUNNING
        self.cleanup_complete.clear()
        self.cleanup_task = self.runtime.create_background_task(
            self._run_cleanup(
                interrupt=interrupt,
                recovery_error=recovery_error,
            ),
            name=f"codex-turn-cleanup-{self.thread_id}",
        )
        return self.cleanup_task

    async def cleanup(
        self,
        *,
        interrupt: bool = False,
        retry: bool = False,
    ) -> None:
        task = self.start_cleanup(interrupt=interrupt, retry=retry)
        await asyncio.shield(task)

    async def _run_cleanup(
        self,
        *,
        interrupt: bool,
        recovery_error: CodexSubscriptionError | None,
    ) -> None:
        failure: CodexSubscriptionError | None = None
        if interrupt and self.turn_id:
            try:
                self.interrupt_result = await self.runtime.request(
                    "turn/interrupt",
                    {"threadId": self.thread_id, "turnId": self.turn_id},
                    timeout=5.0,
                )
            except Exception as exc:
                failure = CodexSubscriptionError(
                    "CODEX_INTERRUPT_FAILED",
                    "Codex did not confirm cancellation of the previous turn",
                    remediation=(
                        "Retry cleanup or restart the Codex runtime before "
                        "starting another turn."
                    ),
                )
                failure.__cause__ = exc

        try:
            if self.tool_bridge:
                self.tool_bridge.close()
                get_tool_registry(self.runtime).remove(self.thread_id)
                self.tool_bridge = None
            for unsubscribe in self.unsubscribers:
                try:
                    unsubscribe()
                except Exception as exc:
                    if failure is None:
                        failure = _cleanup_failure(
                            "Codex notification cleanup failed",
                            exc,
                        )
            self.unsubscribers.clear()
            if self.runtime.state is RuntimeState.READY:
                try:
                    await self.runtime.request(
                        "thread/unsubscribe",
                        {"threadId": self.thread_id},
                    )
                except Exception as exc:
                    if failure is None:
                        failure = _cleanup_failure(
                            "Codex thread cleanup was not confirmed",
                            exc,
                        )
        except Exception as exc:
            if failure is None:
                failure = _cleanup_failure(
                    "Codex local turn cleanup failed",
                    exc,
                )
        finally:
            try:
                self.temporary.cleanup()
            except Exception as exc:
                if failure is None:
                    failure = _cleanup_failure(
                        "Codex temporary workspace cleanup failed",
                        exc,
                    )

        if failure is not None:
            self.cleanup_error = failure
            self.cleanup_state = CleanupState.FAILED
            self.runtime.mark_turn_cleanup_failed(failure)
            self.cleanup_complete.set()
            raise failure

        self.cleanup_state = CleanupState.COMPLETED
        self.cleanup_error = None
        if recovery_error is not None:
            self.runtime.clear_turn_cleanup_error(recovery_error)
        self.cleanup_complete.set()


def _cleanup_failure(
    message: str,
    cause: Exception,
) -> CodexSubscriptionError:
    error = CodexSubscriptionError(
        "CODEX_CLEANUP_FAILED",
        message,
        remediation=(
            "Retry cleanup or restart the Codex runtime before starting "
            "another turn."
        ),
    )
    error.__cause__ = cause
    return error
