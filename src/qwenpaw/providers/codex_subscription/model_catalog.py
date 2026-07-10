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

    async def fetch(self) -> list[ModelInfo]:
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
            self._cache = models
            self.stale = False
            return [model.model_copy(deep=True) for model in models]
        except Exception:
            if self._cache:
                self.stale = True
                return [model.model_copy(deep=True) for model in self._cache]
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
        reasoning_effort_options=efforts or None,
        thinking_param_style="effort",
        **model_kwargs,
    )
