# -*- coding: utf-8 -*-
from __future__ import annotations

from qwenpaw.providers.codex_subscription.model_catalog import ModelCatalog
from qwenpaw.providers.codex_subscription.rate_limits import RateLimitService
from qwenpaw.providers.provider import ModelInfo


async def test_model_catalog_maps_modalities_and_reasoning(stub_runtime):
    catalog = ModelCatalog(stub_runtime)
    models = await catalog.fetch()
    assert len(models) == 1
    model = models[0]
    assert model.id == "codex-test"
    assert model.supports_image is True
    assert model.reasoning_effort == "medium"
    assert model.reasoning_effort_options == ["low", "medium"]
    assert catalog.stale is False


async def test_model_catalog_uses_stale_cache(stub_runtime):
    catalog = ModelCatalog(stub_runtime)
    first = await catalog.fetch()
    stub_runtime.responses["model/list"] = RuntimeError("offline")
    second = await catalog.fetch()
    assert second == first
    assert catalog.stale is True


async def test_model_refresh_preserves_user_reasoning_effort(stub_runtime):
    catalog = ModelCatalog(stub_runtime)
    existing = (await catalog.fetch())[0]
    existing.reasoning_effort = "high"
    stub_runtime.responses["model/list"]["data"][0].update(
        {
            "defaultReasoningEffort": "medium",
            "supportedReasoningEfforts": [
                {"reasoningEffort": "low"},
                {"reasoningEffort": "medium"},
                {"reasoningEffort": "high"},
            ],
        },
    )

    refreshed = (await catalog.fetch([existing]))[0]

    assert refreshed.reasoning_effort == "high"
    assert refreshed.catalog_default_reasoning_effort == "medium"
    assert refreshed.reasoning_effort_config_invalid is False


async def test_model_refresh_marks_removed_user_effort_invalid(stub_runtime):
    catalog = ModelCatalog(stub_runtime)
    existing = (await catalog.fetch())[0]
    existing.reasoning_effort = "high"

    refreshed = (await catalog.fetch([existing]))[0]

    assert refreshed.reasoning_effort == "high"
    assert refreshed.reasoning_effort_config_invalid is True


async def test_model_refresh_uses_catalog_default_when_user_choice_is_none(
    stub_runtime,
):
    catalog = ModelCatalog(stub_runtime)
    existing = (await catalog.fetch())[0]
    existing.reasoning_effort = None
    stub_runtime.responses["model/list"]["data"][0][
        "defaultReasoningEffort"
    ] = "low"

    refreshed = (await catalog.fetch([existing]))[0]

    assert refreshed.reasoning_effort == "low"
    assert refreshed.catalog_default_reasoning_effort == "low"


async def test_failed_first_refresh_uses_persisted_models(stub_runtime):
    stub_runtime.responses["model/list"] = RuntimeError("offline")
    persisted = ModelInfo(id="saved", name="Saved")
    catalog = ModelCatalog(stub_runtime)

    models = await catalog.fetch([persisted])

    assert [model.id for model in models] == ["saved"]
    assert catalog.stale is True


async def test_rate_limit_mapping(stub_runtime):
    limits = await RateLimitService(stub_runtime).read()
    assert limits.primary is not None
    assert limits.primary.used_percent == 37
    assert limits.primary.resets_at == 1780000000
    assert limits.credits is not None
    assert limits.credits.balance == "5.00"


async def test_missing_modalities_are_conservatively_text_only(stub_runtime):
    stub_runtime.responses["model/list"] = {
        "data": [{"id": "unknown-capabilities", "contextWindow": 64000}],
    }
    model = (await ModelCatalog(stub_runtime).fetch())[0]
    assert model.supports_image is False
    assert model.max_input_length == 64000
