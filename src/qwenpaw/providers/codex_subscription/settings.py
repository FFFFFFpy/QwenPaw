# -*- coding: utf-8 -*-
"""Non-secret Codex subscription settings and binary discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import CodexSubscriptionError

_SCHEMA_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class CodexSubscriptionSettings(BaseModel):
    """Persisted settings. This model must never gain credential fields."""

    model_config = ConfigDict(extra="forbid")

    binary_path: str = ""
    preferred_login_flow: Literal["browser", "device_code"] = "browser"
    codex_dynamic_tool_mode: Literal["off", "all"] = "all"
    client_name: str = "qwenpaw"
    schema_cache_version: int = 1
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    tool_wait_timeout_seconds: float = Field(default=600.0, gt=0, le=3600)
    max_message_bytes: int = Field(default=4 * 1024 * 1024, ge=65536)
    max_attachment_bytes: int = Field(default=8 * 1024 * 1024, ge=65536)
    tool_isolation_verified_fingerprints: list[str] = Field(
        default_factory=list,
    )

    @classmethod
    def load(cls, path: Path) -> "CodexSubscriptionSettings":
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, ValueError) as exc:
            raise CodexSubscriptionError(
                "CODEX_BINARY_INVALID",
                "Codex subscription settings are invalid",
            ) from exc

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)

    def record_tool_isolation_verification(
        self,
        fingerprint: str,
        path: Path,
    ) -> None:
        """Persist a successful account-backed isolation probe result."""

        if not _SCHEMA_FINGERPRINT.fullmatch(fingerprint):
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "Codex schema fingerprint is invalid",
            )
        self.tool_isolation_verified_fingerprints = sorted(
            {*self.tool_isolation_verified_fingerprints, fingerprint},
        )
        self.save(path)


def discover_codex_binary(custom_path: str | None = None) -> str:
    """Resolve a user-selected or PATH Codex binary to an executable path."""

    if custom_path:
        candidate = Path(custom_path).expanduser()
        if not candidate.is_absolute():
            raise CodexSubscriptionError(
                "CODEX_BINARY_INVALID",
                "The custom Codex binary path must be absolute",
            )
    else:
        located = shutil.which("codex")
        if not located:
            raise CodexSubscriptionError(
                "CODEX_NOT_INSTALLED",
                "Codex CLI is not installed or is not on PATH",
                remediation=(
                    "Install the official Codex CLI, then retry detection."
                ),
            )
        candidate = Path(located)

    resolved = candidate.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise CodexSubscriptionError(
            "CODEX_BINARY_INVALID",
            "The configured Codex binary is not an executable file",
        )
    return str(resolved)
