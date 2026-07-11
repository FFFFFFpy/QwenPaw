from types import SimpleNamespace
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwenpaw.app.routers.codex_subscription import router
from qwenpaw.app.routers import router as api_router
from qwenpaw.app.routers.provider_oauth import router as provider_oauth_router
from qwenpaw.providers.codex_subscription.oauth import OAuthService
from qwenpaw.providers.codex_subscription.provider import (
    CodexSubscriptionProvider,
)
from qwenpaw.providers.codex_subscription.token_store import (
    TokenRecord,
    TokenStore,
)
from qwenpaw.providers.codex_subscription.settings import (
    CodexSubscriptionSettings,
)


def application(tmp_path, *, connected=False):
    provider = CodexSubscriptionProvider(
        id="openai-codex",
        name="subscription",
        require_api_key=False,
        supports_oauth=True,
    )
    provider._token_store = TokenStore(tmp_path / "oauth.enc")
    provider._oauth = OAuthService(provider._token_store)
    provider._settings_path = tmp_path / "settings.json"
    provider._settings = CodexSubscriptionSettings()
    if connected:
        provider.token_store.save(
            TokenRecord(
                account_local_id="default",
                masked_email="p***n@example.test",
                access_token="access",
                refresh_token="refresh",
                account_id="account",
                expires_at=time.time() + 3600,
                last_refresh_at=time.time(),
            )
        )
    manager = SimpleNamespace(
        get_provider=lambda provider_id: (
            provider if provider_id == "openai-codex" else None
        )
    )
    app = FastAPI()
    app.state.provider_manager = manager
    app.include_router(router, prefix="/api")
    return app


def test_account_response_is_masked_and_token_free(tmp_path):
    response = TestClient(application(tmp_path, connected=True)).get(
        "/api/providers/openai-codex/account"
    )
    assert response.status_code == 200
    assert response.json()["email_masked"] == "p***n@example.test"
    assert "access" not in response.text.lower()
    assert "refresh" not in response.text.lower()


def test_oauth_start_returns_pkce_url_and_manual_callback_support(tmp_path):
    response = TestClient(application(tmp_path)).post(
        "/api/providers/openai-codex/oauth/start"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authorize_url"].startswith(
        "https://auth.openai.com/oauth/authorize?"
    )
    assert "code_challenge_method=S256" in body["authorize_url"]
    assert body["manual_callback_supported"] is True
    assert "access_token" not in body
    assert "refresh_token" not in body


def test_catalog_has_fixed_context_policy(tmp_path):
    response = TestClient(application(tmp_path)).get(
        "/api/providers/openai-codex/models"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "bundled_compatibility_catalog"
    assert [row["model_id"] for row in body["chat_models"]] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]
    assert body["chat_models"][0]["context_size"] == 262144
    assert body["chat_models"][0]["compact_trigger"] == 235930
    assert body["chat_models"][0]["catalog_max_output_tokens"] == 128000
    assert body["chat_models"][0]["capabilities"] == [
        "text",
        "image_input",
        "tools",
    ]
    assert body["image_models"][0]["model_id"] == "gpt-image-2"


def test_per_model_settings_validate_effort_and_forbid_kwargs(tmp_path):
    client = TestClient(application(tmp_path))
    path = "/api/providers/openai-codex/models/gpt-5.6-luna/settings"
    response = client.put(
        path,
        json={"reasoning_effort": "max", "relay_reasoning": False},
    )
    assert response.status_code == 200
    assert response.json() == {
        "reasoning_effort": "max",
        "relay_reasoning": False,
    }
    assert client.get(path).json() == response.json()
    assert (
        client.put(
            path,
            json={
                "reasoning_effort": "ultra",
                "relay_reasoning": True,
            },
        ).status_code
        == 400
    )
    assert (
        client.put(
            path,
            json={
                "reasoning_effort": "low",
                "relay_reasoning": True,
                "max_tokens": 1,
            },
        ).status_code
        == 422
    )


def test_image_model_settings_are_independent(tmp_path):
    client = TestClient(application(tmp_path))
    path = "/api/providers/openai-codex/image-models/gpt-image-2/settings"
    response = client.put(
        path,
        json={
            "size": "1024x1024",
            "quality": "high",
            "output_format": "webp",
            "background": "transparent",
            "count": 4,
        },
    )
    assert response.status_code == 200
    assert response.json()["output_format"] == "webp"
    assert response.json()["count"] == 4


def test_dedicated_oauth_route_precedes_generic_provider_route(tmp_path):
    """FastAPI resolves overlapping paths in registration order."""
    included = [
        route.original_router
        for route in api_router.routes
        if hasattr(route, "original_router")
    ]
    dedicated = included.index(router)
    generic = included.index(provider_oauth_router)
    assert dedicated < generic

    full_app = FastAPI()
    full_app.state.provider_manager = application(
        tmp_path
    ).state.provider_manager
    full_app.include_router(api_router, prefix="/api")
    response = TestClient(full_app).post(
        "/api/providers/openai-codex/oauth/start"
    )
    assert response.status_code == 200
    assert response.json()["manual_callback_supported"] is True
