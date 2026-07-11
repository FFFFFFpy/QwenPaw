from __future__ import annotations

from qwenpaw.providers.codex_subscription.provider import (
    CodexSubscriptionProvider,
)
from qwenpaw.providers.codex_subscription.runtime import RuntimeState
from qwenpaw.providers.provider import Provider


def make_provider(stub_runtime) -> CodexSubscriptionProvider:
    provider = CodexSubscriptionProvider(
        id="openai-codex",
        name="OpenAI Codex",
        base_url="codex-app-server://local",
        require_api_key=False,
        supports_oauth=True,
    )
    provider.set_runtime(stub_runtime)
    return provider


async def test_provider_directly_extends_provider_and_reports_oauth(
    stub_runtime,
):
    provider = make_provider(stub_runtime)
    stub_runtime.state = RuntimeState.READY
    assert isinstance(provider, Provider)
    info = await provider.get_info(mock_secret=False)
    assert info.api_key == ""
    assert info.oauth_connected is False
    assert info.meta["account_state"] == "unknown"
    assert not stub_runtime.requests
    assert info.meta["provider_kind"] == "cloud_subscription"
    assert info.meta["runtime_state"] == "ready"

    await provider.auth_service.read_account()
    info = await provider.get_info(mock_secret=False)
    assert info.oauth_connected is True
    assert info.meta["account_state"] == "connected"
    assert [method for method, _ in stub_runtime.requests].count(
        "account/read"
    ) == 1


async def test_provider_info_does_not_start_runtime_or_read_account(
    stub_runtime,
):
    provider = make_provider(stub_runtime)
    info = await provider.get_info(mock_secret=False)
    assert info.oauth_connected is False
    assert info.meta["account_state"] == "unknown"
    assert stub_runtime.state == "stopped"
    assert not stub_runtime.requests


async def test_provider_fetches_models_without_generation(stub_runtime):
    provider = make_provider(stub_runtime)
    models = await provider.fetch_models()
    assert [model.id for model in models] == ["codex-test"]
    assert not any(
        method == "turn/start" for method, _ in stub_runtime.requests
    )
    chat_model = provider.get_chat_model_instance("codex-test")
    assert chat_model.auth_service is provider.auth_service
    assert chat_model.parameters.reasoning_effort == "medium"


async def test_disconnected_account_cache_is_reported_without_auth_io(
    stub_runtime,
):
    provider = make_provider(stub_runtime)
    stub_runtime.responses["account/read"] = {"account": None}
    account = await provider.auth_service.read_account()
    assert account.connected is False
    request_count = len(stub_runtime.requests)

    info = await provider.get_info(mock_secret=False)
    assert info.oauth_connected is False
    assert info.meta["account_state"] == "disconnected"
    assert len(stub_runtime.requests) == request_count


async def test_provider_ignores_api_key_configuration(stub_runtime):
    provider = make_provider(stub_runtime)
    provider.update_config({"api_key": "must-not-persist"})
    assert provider.api_key == ""
    assert "must-not-persist" not in provider.model_dump_json()
