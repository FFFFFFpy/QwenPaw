"""API for QwenPaw's ChatGPT/Codex subscription compatibility provider."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from qwenpaw.constant import SECRET_DIR
from qwenpaw.providers.codex_subscription.catalog import (
    COMPACTION_THRESHOLD,
    COMPACTION_TRIGGER_TOKENS,
    MAX_OUTPUT_TOKENS,
    WORK_CONTEXT_TOKENS,
)
from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.provider import (
    CodexSubscriptionProvider,
)
from qwenpaw.providers.codex_subscription.settings import (
    CodexSubscriptionSettings,
    direct_transport_enabled,
)
from qwenpaw.providers.provider import ModelInfo
from qwenpaw.providers.provider_manager import ProviderManager

router = APIRouter(
    prefix="/providers/openai-codex", tags=["openai-codex-subscription"]
)


class OAuthStartResponse(BaseModel):
    authorize_url: str
    state: str
    expires_in: int
    manual_callback_supported: bool
    redirect_uri: str


class OAuthCompleteRequest(BaseModel):
    callback_url: str | None = Field(default=None, max_length=8192)
    code: str | None = Field(default=None, max_length=4096)
    state: str | None = Field(default=None, max_length=512)


class AccountResponse(BaseModel):
    connected: bool
    status: str
    email_masked: str | None = None
    display_name: str | None = None
    expires_at: int | None = None


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    source: str = "subscription_catalog"
    availability: dict[str, str] = Field(
        default_factory=lambda: {
            "gpt-5.6-sol": "unknown_until_validated",
            "gpt-5.6-terra": "unknown_until_validated",
            "gpt-5.6-luna": "unknown_until_validated",
        }
    )
    context_size: int = WORK_CONTEXT_TOKENS
    compact_threshold: float = COMPACTION_THRESHOLD
    compact_trigger: int = COMPACTION_TRIGGER_TOKENS
    max_output_tokens: int = MAX_OUTPUT_TOKENS


class SettingsResponse(BaseModel):
    transport: str
    reasoning_effort: str | None
    relay_reasoning: bool
    context_size: int
    compact_threshold: float
    direct_enabled: bool


class SettingsUpdateRequest(BaseModel):
    reasoning_effort: str | None = None
    relay_reasoning: bool | None = None


async def _manager(request: Request) -> ProviderManager:
    return request.app.state.provider_manager


def _provider(manager: ProviderManager) -> CodexSubscriptionProvider:
    provider = manager.get_provider("openai-codex")
    if not isinstance(provider, CodexSubscriptionProvider):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CODEX_PROVIDER_DISABLED",
                "message": "ChatGPT subscription provider is disabled",
            },
        )
    return provider


def _raise_http(exc: CodexSubscriptionError) -> NoReturn:
    status = exc.status_code or (
        503
        if exc.error_code in {"CODEX_PROTOCOL_INCOMPATIBLE", "CODEX_NETWORK"}
        else 400
    )
    raise HTTPException(
        status_code=status,
        detail={
            "code": exc.error_code,
            "message": str(exc),
            "remediation": exc.remediation,
        },
    ) from exc


@router.get("/account", response_model=AccountResponse)
async def account_read(
    manager: ProviderManager = Depends(_manager),
) -> AccountResponse:
    return AccountResponse.model_validate(
        _provider(manager).token_store.status()
    )


@router.post("/oauth/start", response_model=OAuthStartResponse)
async def oauth_start(
    manager: ProviderManager = Depends(_manager),
) -> OAuthStartResponse:
    if not direct_transport_enabled():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CODEX_COMPATIBILITY_PAUSED",
                "message": "Subscription compatibility access is paused",
            },
        )
    return OAuthStartResponse.model_validate(
        _provider(manager).auth_service.start()
    )


@router.post("/oauth/complete", response_model=AccountResponse)
async def oauth_complete(
    body: OAuthCompleteRequest, manager: ProviderManager = Depends(_manager)
) -> AccountResponse:
    try:
        value = await _provider(manager).auth_service.complete(
            callback_url=body.callback_url, code=body.code, state=body.state
        )
        return AccountResponse.model_validate(value)
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.get("/oauth/status")
async def oauth_status(
    state: str, manager: ProviderManager = Depends(_manager)
) -> dict[str, object]:
    return _provider(manager).auth_service.login_status(state)


@router.post("/logout", status_code=204)
async def logout(manager: ProviderManager = Depends(_manager)) -> None:
    _provider(manager).token_store.delete()


@router.get("/models", response_model=ModelsResponse)
async def models(
    manager: ProviderManager = Depends(_manager),
) -> ModelsResponse:
    return ModelsResponse(
        models=[
            model.model_copy(deep=True) for model in _provider(manager).models
        ]
    )


@router.post("/models/refresh", response_model=ModelsResponse)
async def models_refresh(
    manager: ProviderManager = Depends(_manager),
) -> ModelsResponse:
    provider = _provider(manager)
    try:
        return ModelsResponse(models=await provider.fetch_models())
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.get("/rate-limits")
async def rate_limits(
    manager: ProviderManager = Depends(_manager),
) -> dict[str, object]:
    _provider(manager)
    return {
        "available": False,
        "message": "Provider did not return rate-limit details",
    }


@router.post("/validate")
async def validate(
    manager: ProviderManager = Depends(_manager),
) -> dict[str, object]:
    connected, message = await _provider(manager).check_connection()
    return {"valid": connected, "message": message}


@router.get("/settings", response_model=SettingsResponse)
async def read_settings(
    manager: ProviderManager = Depends(_manager),
) -> SettingsResponse:
    settings = _provider(manager).settings
    return SettingsResponse(
        **settings.model_dump(
            include={
                "transport",
                "reasoning_effort",
                "relay_reasoning",
                "context_size",
                "compact_threshold",
            }
        ),
        direct_enabled=direct_transport_enabled(),
    )


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsUpdateRequest, manager: ProviderManager = Depends(_manager)
) -> SettingsResponse:
    provider = _provider(manager)
    updates = provider.settings.model_dump()
    if body.reasoning_effort is not None:
        if body.reasoning_effort not in {
            "auto",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
            "ultra",
        }:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "CODEX_REASONING_INVALID",
                    "message": "Unsupported reasoning effort",
                },
            )
        updates["reasoning_effort"] = (
            None if body.reasoning_effort == "auto" else body.reasoning_effort
        )
    if body.relay_reasoning is not None:
        updates["relay_reasoning"] = body.relay_reasoning
    provider._settings = CodexSubscriptionSettings.model_validate(updates)
    provider.settings.save(SECRET_DIR / "codex_subscription" / "settings.json")
    return await read_settings(manager)
