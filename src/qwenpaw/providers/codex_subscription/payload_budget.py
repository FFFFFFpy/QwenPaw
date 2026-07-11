# -*- coding: utf-8 -*-
"""Deterministic full-payload budgeting for Codex turns."""

from __future__ import annotations

from typing import Any

from .errors import CodexSubscriptionError
from .rpc_client import encode_json_message


MAX_REQUEST_ID_PLACEHOLDER = 9_223_372_036_854_775_807


class TurnPayloadBudget:
    """Budget each actual JSON-RPC request with wire encoding."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes

    @staticmethod
    def measure(
        method: str,
        params: dict[str, Any],
    ) -> int:
        request = {
            "id": MAX_REQUEST_ID_PLACEHOLDER,
            "method": method,
            "params": params,
        }
        return len(encode_json_message(request))

    def validate_rpc_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        attachment_count: int,
    ) -> int:
        payload_bytes = self.measure(method, params)
        if payload_bytes > self.limit_bytes:
            raise CodexSubscriptionError(
                "CODEX_REQUEST_TOO_LARGE",
                f"The Codex {method} request exceeds the configured limit",
                details={
                    "method": method,
                    "payload_bytes": payload_bytes,
                    "limit_bytes": self.limit_bytes,
                    "attachment_count": attachment_count,
                },
            )
        return payload_bytes
