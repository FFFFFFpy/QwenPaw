# -*- coding: utf-8 -*-
"""Safe latency diagnostics and active-turn ownership for Codex calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import threading
import time
import traceback
from typing import Any
import uuid

from .errors import CodexSubscriptionError

logger = logging.getLogger(__name__)


@dataclass
class CodexCallDiagnostics:
    """One call's non-secret dimensions and monotonic timing markers."""

    session_id: str
    model_instance_id: str
    model_id: str
    reasoning_effort: str | None
    dynamic_tool_count: int
    message_count: int
    message_text_bytes: int
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.monotonic)
    thread_id: str = ""
    turn_id: str = ""
    _emitted: set[str] = field(default_factory=set, init=False, repr=False)

    def emit(
        self,
        event: str,
        *,
        once: bool = True,
        level: int = logging.INFO,
        **extra: Any,
    ) -> bool:
        if once and event in self._emitted:
            return False
        if once:
            self._emitted.add(event)
        task = asyncio.current_task()
        payload: dict[str, Any] = {
            "event": event,
            "call_id": self.call_id,
            "session_id": self.session_id,
            "model_instance_id": self.model_instance_id,
            "asyncio_task_name": task.get_name() if task else "",
            "model_id": self.model_id,
            "reasoning_effort": self.reasoning_effort,
            "dynamic_tool_count": self.dynamic_tool_count,
            "message_count": self.message_count,
            "message_text_bytes": self.message_text_bytes,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "elapsed_ms": round(
                (time.monotonic() - self.started_at) * 1000,
                3,
            ),
        }
        payload.update(extra)
        logger.log(
            level,
            "codex_latency %s",
            json.dumps(payload, ensure_ascii=True, sort_keys=True),
        )
        return True


class ActiveTurnRegistry:
    """Process-local ownership keyed by (session_id, model_id)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], CodexCallDiagnostics] = {}

    def acquire(self, diagnostics: CodexCallDiagnostics) -> None:
        key = (diagnostics.session_id, diagnostics.model_id)
        with self._lock:
            current = self._active.get(key)
            if current is None:
                self._active[key] = diagnostics
                return
        diagnostics.emit(
            "concurrent_turn_rejected",
            level=logging.ERROR,
            active_call_id=current.call_id,
            active_model_instance_id=current.model_instance_id,
            call_stack="".join(traceback.format_stack(limit=32)),
        )
        raise CodexSubscriptionError(
            "CODEX_CONCURRENT_TURN",
            "A Codex turn is already active for this session and model",
            details={"active_call_id": current.call_id},
        )

    def release(self, diagnostics: CodexCallDiagnostics) -> None:
        key = (diagnostics.session_id, diagnostics.model_id)
        with self._lock:
            if self._active.get(key) is diagnostics:
                self._active.pop(key, None)


active_turn_registry = ActiveTurnRegistry()
