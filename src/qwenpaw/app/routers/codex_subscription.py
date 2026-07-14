# -*- coding: utf-8 -*-
"""API for QwenPaw's ChatGPT/Codex subscription compatibility provider."""

from __future__ import annotations

from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from qwenpaw.providers.codex_subscription.catalog import (
    CATALOG_SOURCE,
    chat_catalog_entry,
    image_catalog_entry,
    reasoning_options,
)
from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.provider import (
    CodexSubscriptionProvider,
)
from qwenpaw.providers.codex_subscription.settings import (
    ChatModelSettings,
    ImageModelSettings,
    direct_transport_enabled,
)
from qwenpaw.providers.provider_manager import ProviderManager

router = APIRouter(
    prefix="/providers/openai-codex",
    tags=["openai-codex-subscription"],
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
    source: Literal["bundled_compatibility_catalog"] = CATALOG_SOURCE
    chat_models: list[dict[str, Any]]
    image_models: list[dict[str, Any]]


class ChatSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_effort: str | None = None
    relay_reasoning: bool


class ImageSettingsUpdate(ImageModelSettings):
    model_config = ConfigDict(extra="forbid")


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


def _active_model_id(manager: ProviderManager) -> str | None:
    getter = getattr(manager, "get_active_model", None)
    active = getter() if callable(getter) else None
    if active and getattr(active, "provider_id", None) == "openai-codex":
        return str(getattr(active, "model", "")) or None
    return None


def _models_response(
    manager: ProviderManager,
    provider: CodexSubscriptionProvider,
) -> ModelsResponse:
    active = _active_model_id(manager)
    return ModelsResponse(
        chat_models=[
            chat_catalog_entry(
                model.id,
                availability=provider.availability(model.id),
                is_active=model.id == active,
                reasoning_effort=provider.settings.chat_model(
                    model.id,
                ).reasoning_effort,
                relay_reasoning=provider.settings.chat_model(
                    model.id,
                ).relay_reasoning,
            )
            for model in provider.models
        ],
        image_models=[
            image_catalog_entry(
                availability=provider.availability("gpt-image-2"),
            ),
        ],
    )


@router.get("/account", response_model=AccountResponse)
async def account_read(
    manager: ProviderManager = Depends(_manager),
) -> AccountResponse:
    return AccountResponse.model_validate(
        _provider(manager).token_store.status(),
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
        _provider(manager).auth_service.start(),
    )


@router.post("/oauth/complete", response_model=AccountResponse)
async def oauth_complete(
    body: OAuthCompleteRequest,
    manager: ProviderManager = Depends(_manager),
) -> AccountResponse:
    try:
        value = await _provider(manager).auth_service.complete(
            callback_url=body.callback_url,
            code=body.code,
            state=body.state,
        )
        return AccountResponse.model_validate(value)
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.get("/oauth/status")
async def oauth_status(
    state: str,
    manager: ProviderManager = Depends(_manager),
) -> dict[str, object]:
    return _provider(manager).auth_service.login_status(state)


@router.post("/logout", status_code=204)
async def logout(manager: ProviderManager = Depends(_manager)) -> None:
    _provider(manager).token_store.delete()


@router.get("/models", response_model=ModelsResponse)
async def models(
    manager: ProviderManager = Depends(_manager),
) -> ModelsResponse:
    return _models_response(manager, _provider(manager))


@router.post("/models/refresh", response_model=ModelsResponse)
async def models_refresh(
    manager: ProviderManager = Depends(_manager),
) -> ModelsResponse:
    return _models_response(manager, _provider(manager))


@router.get("/models/{model_id}/settings", response_model=ChatModelSettings)
async def read_chat_settings(
    model_id: str,
    manager: ProviderManager = Depends(_manager),
) -> ChatModelSettings:
    try:
        return _provider(manager).settings.chat_model(model_id)
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.put("/models/{model_id}/settings", response_model=ChatModelSettings)
async def update_chat_settings(
    model_id: str,
    body: ChatSettingsUpdate,
    manager: ProviderManager = Depends(_manager),
) -> ChatModelSettings:
    provider = _provider(manager)
    try:
        current = provider.settings.chat_model(model_id)
    except CodexSubscriptionError as exc:
        _raise_http(exc)
    effort = body.reasoning_effort
    if effort == "auto":
        effort = None
    if effort is not None and effort not in reasoning_options(model_id):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CODEX_REASONING_INVALID",
                "message": "Unsupported reasoning effort for this model",
            },
        )
    current.reasoning_effort = effort
    current.relay_reasoning = body.relay_reasoning
    provider.save_settings()
    return current


@router.get(
    "/image-models/{model_id}/settings",
    response_model=ImageModelSettings,
)
async def read_image_settings(
    model_id: str,
    manager: ProviderManager = Depends(_manager),
) -> ImageModelSettings:
    try:
        return _provider(manager).settings.image_model(model_id)
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.put(
    "/image-models/{model_id}/settings",
    response_model=ImageModelSettings,
)
async def update_image_settings(
    model_id: str,
    body: ImageSettingsUpdate,
    manager: ProviderManager = Depends(_manager),
) -> ImageModelSettings:
    provider = _provider(manager)
    try:
        provider.settings.image_model(model_id)
    except CodexSubscriptionError as exc:
        _raise_http(exc)
    updated = ImageModelSettings.model_validate(body.model_dump())
    provider.settings.image_models[model_id] = updated
    provider.save_settings()
    return updated


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
