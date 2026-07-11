"""Known subscription model catalog and account availability state."""

from __future__ import annotations

from qwenpaw.providers.provider import ModelInfo

WORK_CONTEXT_TOKENS = 262_144
COMPACTION_THRESHOLD = 0.90
COMPACTION_TRIGGER_TOKENS = 235_930
MAX_OUTPUT_TOKENS = 131_072

_EFFORTS = {
    "gpt-5.6-sol": ["auto", "low", "medium", "high", "xhigh"],
    "gpt-5.6-terra": ["auto", "low", "medium", "high", "xhigh", "max"],
    "gpt-5.6-luna": ["auto", "low", "medium", "high", "xhigh", "max", "ultra"],
}

# GPT-5.6 is served through Codex's Responses Lite compatibility mode.  The
# backend intentionally hides these model slugs unless both the Lite request
# header and its matching request shape are present.
_RESPONSES_LITE_MODELS = frozenset(_EFFORTS)


def subscription_models() -> list[ModelInfo]:
    return [
        ModelInfo(
            id=model_id,
            name=model_id,
            supports_multimodal=True,
            supports_image=True,
            supports_video=False,
            probe_source="documentation",
            max_tokens=MAX_OUTPUT_TOKENS,
            max_input_length=WORK_CONTEXT_TOKENS,
            reasoning_effort=None,
            reasoning_effort_options=efforts,
            thinking_param_style="effort",
            relay_reasoning=True,
        )
        for model_id, efforts in _EFFORTS.items()
    ]


def reasoning_options(model_id: str) -> list[str]:
    return list(_EFFORTS.get(model_id, ["auto", "low", "medium", "high"]))


def uses_responses_lite(model_id: str) -> bool:
    return model_id in _RESPONSES_LITE_MODELS
