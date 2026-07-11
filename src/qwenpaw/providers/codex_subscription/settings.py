"""Non-secret settings for the ChatGPT/Codex compatibility transport."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .errors import CodexSubscriptionError


class CodexSubscriptionSettings(BaseModel):
    """Persisted settings. Credentials must never be added to this model."""

    model_config = ConfigDict(extra="ignore")

    transport: str = "direct"
    reasoning_effort: str | None = None
    relay_reasoning: bool = True
    context_size: int = Field(default=262_144, ge=1_000)
    compact_threshold: float = Field(default=0.90, gt=0, le=1)
    request_timeout_seconds: float = Field(default=600.0, gt=0, le=3600)
    max_request_bytes: int = Field(default=16 * 1024 * 1024, ge=65_536)

    @classmethod
    def load(cls, path: Path) -> "CodexSubscriptionSettings":
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return cls.model_validate(payload)
        except (OSError, ValueError) as exc:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "ChatGPT subscription settings are invalid",
            ) from exc

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.model_dump(), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)


def direct_transport_enabled() -> bool:
    return os.getenv(
        "QWENPAW_OPENAI_CODEX_DIRECT_ENABLED", "true"
    ).lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
