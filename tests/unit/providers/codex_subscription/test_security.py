from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from qwenpaw.providers.codex_subscription.errors import (
    CodexSubscriptionError,
    scrub_sensitive_text,
)
from qwenpaw.providers.codex_subscription.provider import (
    CodexSubscriptionProvider,
)
from qwenpaw.providers.codex_subscription.settings import (
    CodexSubscriptionSettings,
)


def test_secret_scrubber_masks_tokens_and_email():
    value = scrub_sensitive_text(
        "Bearer abc-secret sk-123456789 person@example.test "
        "access_token=oauth-secret",
    )
    assert "abc-secret" not in value
    assert "sk-123456789" not in value
    assert "oauth-secret" not in value
    assert "person@example.test" not in value
    assert "p***@example.test" in value

    error = CodexSubscriptionError(
        "CODEX_TURN_FAILED",
        "safe",
        details={
            "nested": {
                "authorization": "Bearer nested-secret",
                "account": "person@example.test",
            },
        },
    )
    serialized = str(error.details)
    assert "nested-secret" not in serialized
    assert "person@example.test" not in serialized


def test_settings_reject_credential_fields():
    with pytest.raises(ValidationError):
        CodexSubscriptionSettings.model_validate(
            {"access_token": "must-not-be-accepted"},
        )


def test_provider_serialization_contains_no_subscription_secret(stub_runtime):
    provider = CodexSubscriptionProvider(
        id="openai-codex",
        name="OpenAI Codex",
        require_api_key=False,
        api_key="injected-storage-secret",
    )
    provider.set_runtime(stub_runtime)
    provider.update_config({"api_key": "must-not-persist"})
    serialized = provider.model_dump_json()
    assert "injected-storage-secret" not in serialized
    assert "must-not-persist" not in serialized
    assert '"api_key":""' in serialized


def test_production_integration_never_reads_codex_auth_cache():
    root = (
        Path(__file__).parents[4]
        / "src"
        / "qwenpaw"
        / "providers"
        / "codex_subscription"
    )
    production = "\n".join(
        path.read_text(encoding="utf-8") for path in root.glob("*.py")
    )
    assert "auth.json" not in production
    assert "shell=True" not in production
