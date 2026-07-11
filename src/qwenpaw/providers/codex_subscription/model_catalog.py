# -*- coding: utf-8 -*-
"""Dynamic model catalog backed by `model/list`."""

from __future__ import annotations

from typing import Any

from qwenpaw.providers.provider import ModelInfo

from .runtime import CodexAppServerRuntime, RuntimeState


class ModelCatalog:
    def __init__(self, runtime: CodexAppServerRuntime) -> None:
        self.runtime = runtime
        self._cache: list[ModelInfo] = []
        self.stale = False

    async def fetch(
        self,
        existing_models: list[ModelInfo] | None = None,
    ) -> list[ModelInfo]:
        try:
            if self.runtime.state is not RuntimeState.READY:
                await self.runtime.start()
            rows: list[dict[str, Any]] = []
            cursor: str | None = None
            for _ in range(100):
                response = await self.runtime.request(
                    "model/list",
                    {
                        "cursor": cursor,
                        "limit": 100,
                        "includeHidden": False,
                    },
                )
                data = response.get("data")
                if isinstance(data, list):
                    rows.extend(
                        item for item in data if isinstance(item, dict)
                    )
                next_cursor = response.get("nextCursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    break
                cursor = next_cursor
            mapped = [_map_model(row) for row in rows if not row.get("hidden")]
            models: list[ModelInfo] = [
                model for model in mapped if model is not None
            ]
            models = _merge_user_model_config(
                models,
                existing_models
                if existing_models is not None
                else self._cache,
            )
            self._cache = models
            self.stale = False
            return [model.model_copy(deep=True) for model in models]
        except Exception:
            fallback = self._cache or existing_models or []
            if fallback:
                self.stale = True
                return [model.model_copy(deep=True) for model in fallback]
            raise

    def cached(self) -> list[ModelInfo]:
        return [model.model_copy(deep=True) for model in self._cache]


def _map_model(row: dict[str, Any]) -> ModelInfo | None:
    model_id = row.get("id") or row.get("model")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    display_name = row.get("displayName")
    modalities = row.get("inputModalities")
    if not isinstance(modalities, list):
        modalities = ["text"]
    efforts = []
    options = row.get("supportedReasoningEfforts")
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict):
                effort = option.get("reasoningEffort")
                if isinstance(effort, str) and effort not in efforts:
                    efforts.append(effort)
    default_effort = row.get("defaultReasoningEffort")
    if not isinstance(default_effort, str):
        default_effort = None
    context_window = next(
        (
            int(value)
            for value in (
                row.get("contextWindow"),
                row.get("contextWindowSize"),
                row.get("maxInputTokens"),
            )
            if isinstance(value, (int, float)) and value >= 1000
        ),
        None,
    )
    model_kwargs: dict[str, Any] = {}
    if context_window is not None:
        model_kwargs["max_input_length"] = context_window
        model_kwargs["catalog_max_input_length"] = context_window
    return ModelInfo(
        id=model_id.strip(),
        name=(
            display_name.strip()
            if isinstance(display_name, str) and display_name.strip()
            else model_id.strip()
        ),
        supports_multimodal=any(item != "text" for item in modalities),
        supports_image="image" in modalities,
        supports_video=False,
        probe_source="documentation",
        reasoning_effort=default_effort,
        catalog_default_reasoning_effort=default_effort,
        reasoning_effort_options=efforts or None,
        thinking_param_style="effort",
        **model_kwargs,
    )


def _merge_user_model_config(
    catalog_models: list[ModelInfo],
    existing_models: list[ModelInfo],
) -> list[ModelInfo]:
    existing_by_id = {model.id: model for model in existing_models}
    for model in catalog_models:
        existing = existing_by_id.get(model.id)
        if existing is None:
            continue
        model.relay_reasoning = existing.relay_reasoning
        model.thinking_enabled = existing.thinking_enabled
        model.thinking_budget = existing.thinking_budget
        model.generate_kwargs = dict(existing.generate_kwargs)
        model.max_tokens = existing.max_tokens

        saved_effort_is_user_choice = (
            existing.reasoning_effort is not None
            and (
                existing.catalog_default_reasoning_effort is None
                or existing.reasoning_effort
                != existing.catalog_default_reasoning_effort
            )
        )
        if saved_effort_is_user_choice:
            model.reasoning_effort = existing.reasoning_effort

        saved_context_is_user_choice = (
            existing.catalog_max_input_length is None
            or existing.max_input_length != existing.catalog_max_input_length
        )
        if saved_context_is_user_choice:
            model.max_input_length = existing.max_input_length

        options = model.reasoning_effort_options or []
        model.reasoning_effort_config_invalid = bool(
            model.reasoning_effort is not None
            and options
            and model.reasoning_effort not in options,
        )
    return catalog_models
