# -*- coding: utf-8 -*-
"""Bundled ChatGPT subscription compatibility catalog."""

from __future__ import annotations

from typing import Any

from qwenpaw.providers.provider import ModelInfo

CATALOG_SOURCE = "bundled_compatibility_catalog"
WORK_CONTEXT_TOKENS = 262_144
COMPACTION_THRESHOLD = 0.90
COMPACTION_TRIGGER_TOKENS = 235_930
CATALOG_MAX_OUTPUT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = CATALOG_MAX_OUTPUT_TOKENS  # compatibility import

_MODEL_DATA: dict[str, dict[str, Any]] = {
    "gpt-5.6-sol": {
        "display_name": "GPT-5.6 Sol",
        "description": "旗舰订阅模型",
        "default": "low",
        "efforts": [
            "auto",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
            "ultra",
        ],
    },
    "gpt-5.6-terra": {
        "display_name": "GPT-5.6 Terra",
        "description": "均衡型订阅模型",
        "default": "medium",
        "efforts": [
            "auto",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
            "ultra",
        ],
    },
    "gpt-5.6-luna": {
        "display_name": "GPT-5.6 Luna",
        "description": "快速型订阅模型",
        "default": "medium",
        "efforts": [
            "auto",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ],
    },
}

_RESPONSES_LITE_MODELS = frozenset(_MODEL_DATA)
_AVAILABILITY = {
    **{model_id: "unknown" for model_id in _MODEL_DATA},
    "gpt-image-2": "unknown",
}


def subscription_models() -> list[ModelInfo]:
    return [
        ModelInfo(
            id=model_id,
            name=data["display_name"],
            supports_multimodal=True,
            supports_image=True,
            supports_video=False,
            probe_source="documentation",
            max_input_length=WORK_CONTEXT_TOKENS,
            reasoning_effort=None,
            reasoning_effort_options=list(data["efforts"]),
            thinking_param_style="effort",
            relay_reasoning=True,
        )
        for model_id, data in _MODEL_DATA.items()
    ]


def chat_catalog_entry(
    model_id: str,
    *,
    availability: str = "unknown",
    is_active: bool = False,
    reasoning_effort: str | None = None,
    relay_reasoning: bool = True,
) -> dict[str, Any]:
    data = _MODEL_DATA[model_id]
    return {
        "model_id": model_id,
        "display_name": data["display_name"],
        "description": data["description"],
        "kind": "chat",
        "availability": availability,
        "is_active": is_active,
        "capabilities": ["text", "image_input", "tools"],
        "reasoning_effort": reasoning_effort,
        "default_reasoning_effort": data["default"],
        "reasoning_effort_options": list(data["efforts"]),
        "relay_reasoning": relay_reasoning,
        "context_size": WORK_CONTEXT_TOKENS,
        "compact_threshold": COMPACTION_THRESHOLD,
        "compact_trigger": COMPACTION_TRIGGER_TOKENS,
        "catalog_max_output_tokens": CATALOG_MAX_OUTPUT_TOKENS,
    }


def image_catalog_entry(*, availability: str = "unknown") -> dict[str, Any]:
    return {
        "model_id": "gpt-image-2",
        "display_name": "GPT Image 2",
        "description": "用于真实图片生成与参考图编辑",
        "kind": "image_generation",
        "availability": availability,
        "is_default": True,
        "capabilities": [
            "image_generate",
            "image_edit",
            "multiple_references",
        ],
        "max_count": 4,
        "max_input_images": 5,
        "output_formats": ["png", "jpeg", "webp"],
    }


def reasoning_options(model_id: str) -> list[str]:
    data = _MODEL_DATA.get(model_id)
    return list(data["efforts"]) if data else ["auto", "low", "medium", "high"]


def default_reasoning_effort(model_id: str) -> str:
    data = _MODEL_DATA[model_id]
    return str(data["default"])


def uses_responses_lite(model_id: str) -> bool:
    return model_id in _RESPONSES_LITE_MODELS


def model_availability(model_id: str) -> str:
    return _AVAILABILITY.get(model_id, "unknown")


def record_model_availability(model_id: str, value: str) -> None:
    if model_id in _AVAILABILITY and value in {"available", "unavailable"}:
        _AVAILABILITY[model_id] = value
