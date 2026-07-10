from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qwenpaw.app.routers.codex_subscription import router
from qwenpaw.providers.codex_subscription.provider import (
    CodexSubscriptionProvider,
)
from tests.unit.providers.codex_subscription.conftest import StubRuntime


def _application() -> tuple[FastAPI, StubRuntime]:
    runtime = StubRuntime(
        {
            "account/read": {
                "account": {
                    "type": "chatgpt",
                    "email": "person@example.test",
                    "planType": "plus",
                },
                "requiresOpenaiAuth": True,
            },
            "account/login/start": {
                "type": "chatgpt",
                "loginId": "login-1",
                "authUrl": "https://chatgpt.example/authorize",
            },
        },
    )
    provider = CodexSubscriptionProvider(
        id="openai-codex",
        name="OpenAI Codex",
        base_url="codex-app-server://local",
        require_api_key=False,
        supports_oauth=True,
    )
    provider.set_runtime(runtime)
    manager = SimpleNamespace(
        get_provider=lambda provider_id: (
            provider if provider_id == "openai-codex" else None
        ),
    )
    app = FastAPI()
    app.state.provider_manager = manager
    app.include_router(router, prefix="/api")
    return app, runtime


def test_account_response_is_masked_and_token_free() -> None:
    app, _ = _application()
    response = TestClient(app).get("/api/providers/openai-codex/account")
    assert response.status_code == 200
    assert response.json()["email_masked"] == "p****n@example.test"
    assert "token" not in response.text.lower()


def test_browser_login_uses_dedicated_app_server_flow() -> None:
    app, runtime = _application()
    response = TestClient(app).post(
        "/api/providers/openai-codex/oauth/start",
        json={"flow": "browser"},
    )
    assert response.status_code == 200
    assert response.json()["flow_type"] == "browser_redirect"
    assert "login_id" not in response.json()
    assert (
        "account/login/start",
        {
            "type": "chatgpt",
            "useHostedLoginSuccessPage": True,
            "appBrand": "chatgpt",
        },
    ) in runtime.requests
