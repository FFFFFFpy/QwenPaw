"""Deterministic full-payload budgeting for Codex turns."""

from __future__ import annotations

from typing import Any

from .errors import CodexSubscriptionError
from .rpc_client import encode_json_message


class TurnPayloadBudget:
    """Budget the complete thread/turn plan with JSON-RPC wire encoding."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes

    @staticmethod
    def measure(
        thread_params: dict[str, Any],
        turn_params: dict[str, Any],
    ) -> int:
        plan = {
            "threadStart": {
                "method": "thread/start",
                "params": thread_params,
            },
            "turnStart": {
                "method": "turn/start",
                "params": turn_params,
            },
        }
        return len(encode_json_message(plan))

    def validate(
        self,
        thread_params: dict[str, Any],
        turn_params: dict[str, Any],
        *,
        attachment_count: int,
    ) -> int:
        payload_bytes = self.measure(thread_params, turn_params)
        if payload_bytes > self.limit_bytes:
            raise CodexSubscriptionError(
                "CODEX_REQUEST_TOO_LARGE",
                "The complete Codex request exceeds the configured limit",
                details={
                    "payload_bytes": payload_bytes,
                    "limit_bytes": self.limit_bytes,
                    "attachment_count": attachment_count,
                },
            )
        return payload_bytes
