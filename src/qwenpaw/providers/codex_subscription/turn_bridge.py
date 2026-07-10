"""Per-generation turn state and cleanup for the Codex adapter."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import tempfile
from typing import Any, Callable

from .runtime import RuntimeState
from .tool_bridge import ToolTurnBridge, get_tool_registry


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
    cleaned: bool = False

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

    async def cleanup(self, *, interrupt: bool = False) -> None:
        if self.cleaned:
            return
        self.cleaned = True
        if interrupt and self.turn_id:
            try:
                await self.runtime.request(
                    "turn/interrupt",
                    {"threadId": self.thread_id, "turnId": self.turn_id},
                    timeout=5.0,
                )
            except Exception:
                pass
        if self.tool_bridge:
            self.tool_bridge.close()
            get_tool_registry(self.runtime).remove(self.thread_id)
        for unsubscribe in self.unsubscribers:
            unsubscribe()
        self.unsubscribers.clear()
        if self.runtime.state is RuntimeState.READY:
            try:
                await self.runtime.request(
                    "thread/unsubscribe",
                    {"threadId": self.thread_id},
                )
            except Exception:
                pass
        self.temporary.cleanup()
