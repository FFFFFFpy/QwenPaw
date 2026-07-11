"""Versioned non-secret settings for the subscription compatibility route."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .catalog import reasoning_options
from .errors import CodexSubscriptionError

SETTINGS_VERSION = 3
CHAT_MODEL_IDS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
)


class ChatModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_effort: str | None = None
    relay_reasoning: bool = True


class ImageModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: str = Field(default="1024x1024", pattern=r"^\d+x\d+$")
    quality: str = Field(default="auto", pattern="^(auto|low|medium|high)$")
    output_format: str = Field(default="png", pattern="^(png|jpeg|webp)$")
    background: str = Field(
        default="auto", pattern="^(auto|opaque|transparent)$"
    )
    count: int = Field(default=1, ge=1, le=4)


def _default_chat_models() -> dict[str, ChatModelSettings]:
    return {model_id: ChatModelSettings() for model_id in CHAT_MODEL_IDS}


def _default_image_models() -> dict[str, ImageModelSettings]:
    return {"gpt-image-2": ImageModelSettings()}


class CodexSubscriptionSettings(BaseModel):
    """Persisted settings. Credentials and request kwargs are forbidden."""

    model_config = ConfigDict(extra="ignore")

    version: int = SETTINGS_VERSION
    transport: str = "direct"
    chat_models: dict[str, ChatModelSettings] = Field(
        default_factory=_default_chat_models
    )
    image_models: dict[str, ImageModelSettings] = Field(
        default_factory=_default_image_models
    )

    @classmethod
    def load(cls, path: Path) -> "CodexSubscriptionSettings":
        if not path.exists():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("settings root must be an object")
            return cls.model_validate(cls._migrate(payload))
        except (OSError, ValueError) as exc:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "ChatGPT subscription settings are invalid",
            ) from exc

    @classmethod
    def _migrate(cls, payload: dict) -> dict:
        if payload.get("version") == SETTINGS_VERSION:
            migrated = dict(payload)
        else:
            effort = payload.get("reasoning_effort")
            if effort == "auto":
                effort = None
            relay = bool(payload.get("relay_reasoning", True))
            migrated = {
                "version": SETTINGS_VERSION,
                "transport": "direct",
                "chat_models": {},
                "image_models": {},
            }
            for model_id in CHAT_MODEL_IDS:
                supported = reasoning_options(model_id)
                migrated["chat_models"][model_id] = {
                    "reasoning_effort": (
                        effort
                        if effort in supported and effort != "auto"
                        else None
                    ),
                    "relay_reasoning": relay,
                }

        chat_models = migrated.setdefault("chat_models", {})
        for model_id, default in _default_chat_models().items():
            chat_models.setdefault(model_id, default.model_dump())
        image_models = migrated.setdefault("image_models", {})
        for model_id, default in _default_image_models().items():
            image_models.setdefault(model_id, default.model_dump())
        migrated["version"] = SETTINGS_VERSION
        migrated["transport"] = "direct"
        return migrated

    def chat_model(self, model_id: str) -> ChatModelSettings:
        try:
            return self.chat_models[model_id]
        except KeyError as exc:
            raise CodexSubscriptionError(
                "CODEX_MODEL_UNAVAILABLE",
                f"Subscription model '{model_id}' is not available",
                status_code=404,
            ) from exc

    def image_model(self, model_id: str) -> ImageModelSettings:
        try:
            return self.image_models[model_id]
        except KeyError as exc:
            raise CodexSubscriptionError(
                "CODEX_MODEL_UNAVAILABLE",
                f"Image model '{model_id}' is not available",
                status_code=404,
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
