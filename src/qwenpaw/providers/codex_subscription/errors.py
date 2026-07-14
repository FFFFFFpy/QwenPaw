# -*- coding: utf-8 -*-
"""Stable, scrubbed errors for the ChatGPT subscription transport."""

from __future__ import annotations

import re
from typing import Any

from qwenpaw.exceptions import ProviderError


class CodexSubscriptionError(ProviderError):
    retryable: bool | None = None

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        remediation: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = _scrub_mapping(details or {})
        if status_code is not None:
            safe_details["status_code"] = status_code
        if remediation:
            safe_details["remediation"] = remediation
        super().__init__(scrub_sensitive_text(message), safe_details)
        self.error_code = code
        self.status_code = status_code
        self.remediation = remediation


class CodexProtocolError(CodexSubscriptionError):
    def __init__(
        self,
        message: str = "ChatGPT subscription protocol changed",
    ) -> None:
        super().__init__("CODEX_PROTOCOL_INCOMPATIBLE", message)


class CodexTransportError(CodexSubscriptionError):
    retryable = True


def error_for_status(status: int) -> CodexSubscriptionError:
    if status == 401:
        return CodexSubscriptionError(
            "CODEX_NOT_LOGGED_IN",
            "Login expired; sign in again",
            status_code=status,
        )
    if status in {403, 404}:
        return CodexSubscriptionError(
            "CODEX_MODEL_UNAVAILABLE",
            "This subscription cannot use the selected model",
            status_code=status,
        )
    if status == 429:
        return CodexSubscriptionError(
            "CODEX_RATE_LIMITED",
            "The subscription is rate limited",
            status_code=status,
        )
    if status >= 500:
        return CodexTransportError(
            "CODEX_UPSTREAM_ERROR",
            "ChatGPT is temporarily unavailable",
            status_code=status,
        )
    return CodexSubscriptionError(
        "CODEX_REQUEST_FAILED",
        "ChatGPT rejected the request",
        status_code=status,
    )


_EMAIL = re.compile(r"(?i)([a-z0-9.!#$%&'*+/=?^_`{|}~-])[^\s@]*@([a-z0-9.-]+)")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+[^\s,]+|sk-[a-z0-9_-]{8,}|"
    r"(?:access|refresh|id)[_-]?token[=:]\s*[^\s&,]+|"
    r"(?:code|authorization)[=:]\s*[^\s&,]+)",
)
_JWT = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")


def scrub_sensitive_text(value: str) -> str:
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
