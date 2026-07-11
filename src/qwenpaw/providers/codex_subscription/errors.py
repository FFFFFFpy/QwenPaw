# -*- coding: utf-8 -*-
"""Stable, scrubbed errors for the Codex App Server integration."""

from __future__ import annotations

import re
from typing import Any

from qwenpaw.exceptions import ProviderError


class CodexSubscriptionError(ProviderError):
    """A user-safe provider error with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        remediation: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = _scrub_mapping(details or {})
        if remediation:
            safe_details["remediation"] = remediation
        safe_message = scrub_sensitive_text(message)
        super().__init__(safe_message, safe_details)
        self.error_code = code
        self.remediation = remediation


class CodexProtocolError(CodexSubscriptionError):
    def __init__(
        self,
        message: str = "Codex App Server protocol error",
    ) -> None:
        super().__init__("CODEX_PROTOCOL_INCOMPATIBLE", message)


class CodexConnectionClosedError(CodexSubscriptionError):
    def __init__(
        self,
        message: str = "Codex App Server connection closed",
    ) -> None:
        super().__init__(
            "CODEX_RUNTIME_CRASHED",
            message,
            remediation="Restart the Codex runtime and retry the request.",
        )


class CodexRpcError(CodexSubscriptionError):
    """A JSON-RPC error that intentionally excludes upstream error data."""

    def __init__(self, rpc_code: int, message: str) -> None:
        super().__init__(
            "CODEX_TURN_FAILED",
            message or "Codex App Server request failed",
            details={"rpc_code": rpc_code},
        )
        self.rpc_code = rpc_code


_EMAIL = re.compile(r"(?i)([a-z0-9.!#$%&'*+/=?^_`{|}~-])[^\s@]*@([a-z0-9.-]+)")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+[^\s,]+|sk-[a-z0-9_-]{8,}|"
    r"(?:access|refresh|id)[_-]?token[=:]\s*[^\s&,]+)",
)
_JWT = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")


def scrub_sensitive_text(value: str) -> str:
    """Remove common credential shapes and mask email addresses."""

    value = _JWT.sub("[REDACTED_TOKEN]", value)
    value = _SECRET.sub("[REDACTED]", value)
    return _EMAIL.sub(
        lambda match: f"{match.group(1)}***@{match.group(2)}",
        value,
    )


def _scrub_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _scrub_value(item) for key, item in value.items()}


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_sensitive_text(value)
    if isinstance(value, dict):
        return _scrub_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_scrub_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_sensitive_text(str(value))
