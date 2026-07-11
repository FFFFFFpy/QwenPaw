"""Dedicated API for the built-in OpenAI Codex subscription provider."""

from __future__ import annotations

from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from qwenpaw.constant import SECRET_DIR
from qwenpaw.providers.codex_subscription.auth_service import (
    CodexAccountStatus,
    LoginStartResult,
    LoginStatus,
)
from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.provider import (
    CodexSubscriptionProvider,
)
from qwenpaw.providers.codex_subscription.rate_limits import CodexRateLimits
from qwenpaw.providers.codex_subscription.runtime import RuntimeState
from qwenpaw.providers.codex_subscription.settings import (
    CodexSubscriptionSettings,
    discover_codex_binary,
)
from qwenpaw.providers.provider import ModelInfo
from qwenpaw.providers.provider_manager import ProviderManager

router = APIRouter(
    prefix="/providers/openai-codex",
    tags=["openai-codex-subscription"],
)


class RuntimeStatusResponse(BaseModel):
    state: str
    installed: bool
    binary_path: str | None = None
    binary_version: str | None = None
    generation_id: str | None = None
    capabilities: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None
    remediation: str | None = None


class LoginStartRequest(BaseModel):
    flow: Literal["browser", "device_code"] = "browser"


class CancelLoginRequest(BaseModel):
    state: str = Field(min_length=1, max_length=256)


class ModelsRefreshResponse(BaseModel):
    models: list[ModelInfo]
    stale: bool


class SettingsResponse(BaseModel):
    binary_path: str
    preferred_login_flow: Literal["browser", "device_code"]
    tool_wait_timeout_seconds: float


class SettingsUpdateRequest(BaseModel):
    binary_path: str | None = None
    preferred_login_flow: Literal["browser", "device_code"] | None = None
    tool_wait_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        le=3600,
    )


async def _manager(request: Request) -> ProviderManager:
    return request.app.state.provider_manager


def _provider(manager: ProviderManager) -> CodexSubscriptionProvider:
    provider = manager.get_provider("openai-codex")
    if not isinstance(provider, CodexSubscriptionProvider):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CODEX_PROVIDER_DISABLED",
                "message": "OpenAI Codex subscription provider is disabled",
            },
        )
    return provider


def _raise_http(exc: CodexSubscriptionError) -> NoReturn:
    status = (
        503
        if exc.error_code
        in {
            "CODEX_NOT_INSTALLED",
            "CODEX_RUNTIME_START_FAILED",
            "CODEX_RUNTIME_CRASHED",
            "CODEX_PROTOCOL_INCOMPATIBLE",
        }
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


@router.get("/runtime", response_model=RuntimeStatusResponse)
async def runtime_status(
    manager: ProviderManager = Depends(_manager),
) -> RuntimeStatusResponse:
    runtime = _provider(manager).runtime
    return _runtime_status_response(runtime)


def _runtime_status_response(
    runtime: Any,
    error: CodexSubscriptionError | None = None,
) -> RuntimeStatusResponse:
    capabilities = runtime.capabilities
    return RuntimeStatusResponse(
        state=runtime.state.value,
        installed=runtime.state is not RuntimeState.NOT_INSTALLED,
        binary_path=runtime.binary_path,
        binary_version=runtime.binary_version,
        generation_id=runtime.generation_id or None,
        capabilities=capabilities.model_dump() if capabilities else None,
        error_code=error.error_code if error else None,
        message=str(error) if error else None,
        remediation=error.remediation if error else None,
    )


@router.post("/runtime/redetect", response_model=RuntimeStatusResponse)
async def runtime_redetect(
    manager: ProviderManager = Depends(_manager),
) -> RuntimeStatusResponse:
    provider = _provider(manager)
    error: CodexSubscriptionError | None = None
    try:
        await provider.runtime.redetect()
    except CodexSubscriptionError as exc:
        error = exc
    return _runtime_status_response(provider.runtime, error)


@router.get("/account", response_model=CodexAccountStatus)
async def account_read(
    manager: ProviderManager = Depends(_manager),
) -> CodexAccountStatus:
    try:
        return await _provider(manager).auth_service.read_account()
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.post("/oauth/start", response_model=LoginStartResult)
async def oauth_start(
    body: LoginStartRequest,
    manager: ProviderManager = Depends(_manager),
) -> LoginStartResult:
    try:
        return await _provider(manager).auth_service.start_login(body.flow)
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.get("/oauth/status", response_model=LoginStatus)
async def oauth_status(
    state: str,
    manager: ProviderManager = Depends(_manager),
) -> LoginStatus:
    return await _provider(manager).auth_service.get_status(state)


@router.post("/oauth/cancel", status_code=204)
async def oauth_cancel(
    body: CancelLoginRequest,
    manager: ProviderManager = Depends(_manager),
) -> None:
    try:
        await _provider(manager).auth_service.cancel_login(body.state)
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.post("/logout", status_code=204)
async def logout(
    manager: ProviderManager = Depends(_manager),
) -> None:
    try:
        await _provider(manager).auth_service.logout()
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.get("/rate-limits", response_model=CodexRateLimits)
async def rate_limits(
    manager: ProviderManager = Depends(_manager),
) -> CodexRateLimits:
    try:
        return await _provider(manager).rate_limit_service.read()
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.post("/models/refresh", response_model=ModelsRefreshResponse)
async def models_refresh(
    manager: ProviderManager = Depends(_manager),
) -> ModelsRefreshResponse:
    provider = _provider(manager)
    try:
        models = await provider.fetch_models()
        manager.save_provider_config(provider.id, provider)
        return ModelsRefreshResponse(
            models=models,
            stale=provider.model_catalog_stale,
        )
    except CodexSubscriptionError as exc:
        _raise_http(exc)


@router.get("/settings", response_model=SettingsResponse)
async def read_settings(
    manager: ProviderManager = Depends(_manager),
) -> SettingsResponse:
    settings = _provider(manager).runtime.settings
    return SettingsResponse(
        binary_path=settings.binary_path,
        preferred_login_flow=settings.preferred_login_flow,
        tool_wait_timeout_seconds=settings.tool_wait_timeout_seconds,
    )


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsUpdateRequest,
    manager: ProviderManager = Depends(_manager),
) -> SettingsResponse:
    provider = _provider(manager)
    settings = provider.runtime.settings
    updates = settings.model_dump()
    if body.binary_path is not None:
        path = body.binary_path.strip()
        if path:
            try:
                path = discover_codex_binary(path)
            except CodexSubscriptionError as exc:
                _raise_http(exc)
        updates["binary_path"] = path
    if body.preferred_login_flow is not None:
        updates["preferred_login_flow"] = body.preferred_login_flow
    if body.tool_wait_timeout_seconds is not None:
        updates["tool_wait_timeout_seconds"] = body.tool_wait_timeout_seconds
    provider.runtime.settings = CodexSubscriptionSettings.model_validate(
        updates
    )
    settings_path = SECRET_DIR / "codex_subscription" / "settings.json"
    provider.runtime.settings.save(settings_path)
    return await read_settings(manager)
