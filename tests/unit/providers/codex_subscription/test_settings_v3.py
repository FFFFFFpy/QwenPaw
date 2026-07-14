# -*- coding: utf-8 -*-
import json

import pytest
from pydantic import ValidationError

from qwenpaw.providers.codex_subscription.catalog import subscription_models
from qwenpaw.providers.codex_subscription.settings import (
    CodexSubscriptionSettings,
    ImageModelSettings,
)


def test_defaults_are_version_three_and_per_model(tmp_path):
    path = tmp_path / "settings.json"
    settings = CodexSubscriptionSettings()
    settings.chat_models["gpt-5.6-sol"].reasoning_effort = "ultra"
    settings.image_models["gpt-image-2"].output_format = "jpeg"
    settings.save(path)
    payload = json.loads(path.read_text())
    assert payload["version"] == 3
    assert payload["transport"] == "direct"
    assert payload["chat_models"]["gpt-5.6-sol"]["reasoning_effort"] == "ultra"
    assert payload["image_models"]["gpt-image-2"]["output_format"] == "jpeg"
    assert "max_tokens" not in payload
    assert "base_url" not in payload


def test_v1_v2_global_settings_migrate_only_supported_efforts(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "reasoning_effort": "ultra",
                "relay_reasoning": False,
            },
        ),
    )
    settings = CodexSubscriptionSettings.load(path)
    assert settings.version == 3
    assert settings.chat_models["gpt-5.6-sol"].reasoning_effort == "ultra"
    assert settings.chat_models["gpt-5.6-terra"].reasoning_effort == "ultra"
    assert settings.chat_models["gpt-5.6-luna"].reasoning_effort is None
    assert all(
        not value.relay_reasoning for value in settings.chat_models.values()
    )
    assert settings.image_models["gpt-image-2"].count == 1


def test_jpeg_transparent_combination_is_rejected():
    with pytest.raises(ValidationError):
        ImageModelSettings(output_format="jpeg", background="transparent")


def test_subscription_model_info_does_not_use_catalog_output_as_max_tokens():
    models = subscription_models()
    assert all(model.max_tokens != 128_000 for model in models)
