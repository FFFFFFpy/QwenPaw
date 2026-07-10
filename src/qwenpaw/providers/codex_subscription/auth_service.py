"""ChatGPT subscription authentication delegated to Codex App Server."""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Literal

from pydantic import BaseModel, Field

from .errors import CodexSubscriptionError
from .runtime import CodexAppServerRuntime, RuntimeState


class CodexAccountStatus(BaseModel):
    connected: bool
    email_masked: str | None = None
    plan_type: str | None = None
    auth_type: str | None = None


class LoginStartResult(BaseModel):
    state: str
    flow_type: Literal["browser_redirect", "device_code"]
    authorize_url: str | None = None
    verification_url: str | None = None
    user_code: str | None = None
    expires_at: int
    login_id: str = Field(exclude=True)


class LoginStatus(BaseModel):
    status: Literal[
        "pending",
        "completed",
        "failed",
        "expired",
        "cancelled",
    ]
    error: str | None = None
    account: CodexAccountStatus | None = None


class _LoginSession:
    def __init__(self, result: LoginStartResult) -> None:
        self.result = result
        self.status: Literal[
            "pending",
            "completed",
            "failed",
            "expired",
            "cancelled",
        ] = "pending"
        self.error: str | None = None


class AuthService:
    def __init__(
        self,
        runtime: CodexAppServerRuntime,
        *,
        session_ttl_seconds: int = 15 * 60,
        account_cache_ttl_seconds: float = 30.0,
    ) -> None:
        self.runtime = runtime
        self.session_ttl_seconds = session_ttl_seconds
        self.account_cache_ttl_seconds = account_cache_ttl_seconds
        self._login_lock = asyncio.Lock()
        self._account_lock = asyncio.Lock()
        self._sessions: dict[str, _LoginSession] = {}
        self._subscribed_generation = ""
        self._account_cache: CodexAccountStatus | None = None
        self._account_cache_generation = ""
        self._account_cache_time = 0.0

    async def ensure_runtime(self) -> None:
        if self.runtime.state is not RuntimeState.READY:
            await self.runtime.start()
        self._ensure_subscriptions()

    async def read_account(self, *, force: bool = False) -> CodexAccountStatus:
        await self.ensure_runtime()
        cached = self._get_cached_account(force=force)
        if cached is not None:
            return cached

        async with self._account_lock:
            cached = self._get_cached_account(force=force)
            if cached is not None:
                return cached
            result = await self.runtime.request(
                "account/read",
                {"refreshToken": False},
            )
            account = result.get("account")
            if (
                not isinstance(account, dict)
                or account.get("type") != "chatgpt"
            ):
                status = CodexAccountStatus(connected=False)
            else:
                email = account.get("email")
                plan_type = account.get("planType")
                status = CodexAccountStatus(
                    connected=True,
                    email_masked=_mask_email(
                        email if isinstance(email, str) else None
                    ),
                    plan_type=(
                        str(plan_type) if plan_type is not None else None
                    ),
                    auth_type="chatgpt",
                )
            self._account_cache = status
            self._account_cache_generation = self.runtime.generation_id
            self._account_cache_time = time.monotonic()
            return status.model_copy()

    async def start_login(
        self,
        flow: Literal["browser", "device_code"],
    ) -> LoginStartResult:
        async with self._login_lock:
            await self.ensure_runtime()
            params = (
                {
                    "type": "chatgpt",
                    "useHostedLoginSuccessPage": True,
                    "appBrand": "chatgpt",
                }
                if flow == "browser"
                else {"type": "chatgptDeviceCode"}
            )
            response = await self.runtime.request(
                "account/login/start", params
            )
            login_id = response.get("loginId")
            if not isinstance(login_id, str) or not login_id:
                raise CodexSubscriptionError(
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    "Codex did not return a login session identifier",
                )
            state = secrets.token_urlsafe(24)
            expires_at = int(time.time()) + self.session_ttl_seconds
            if flow == "browser":
                auth_url = response.get("authUrl")
                if not isinstance(auth_url, str) or not auth_url:
                    raise CodexSubscriptionError(
                        "CODEX_PROTOCOL_INCOMPATIBLE",
                        "Codex did not return a browser authorization URL",
                    )
                result = LoginStartResult(
                    state=state,
                    flow_type="browser_redirect",
                    authorize_url=auth_url,
                    expires_at=expires_at,
                    login_id=login_id,
                )
            else:
                verification_url = response.get("verificationUrl")
                user_code = response.get("userCode")
                if not isinstance(verification_url, str) or not isinstance(
                    user_code,
                    str,
                ):
                    raise CodexSubscriptionError(
                        "CODEX_PROTOCOL_INCOMPATIBLE",
                        "Codex did not return device-code login details",
                    )
                result = LoginStartResult(
                    state=state,
                    flow_type="device_code",
                    verification_url=verification_url,
                    user_code=user_code,
                    expires_at=expires_at,
                    login_id=login_id,
                )
            self._sessions[state] = _LoginSession(result)
            return result

    async def get_status(self, state: str) -> LoginStatus:
        session = self._sessions.get(state)
        if session is None:
            return LoginStatus(
                status="failed", error="Login session not found"
            )
        if (
            session.status == "pending"
            and time.time() >= session.result.expires_at
        ):
            session.status = "expired"
            session.error = "Login session expired"
        account = None
        if session.status == "completed":
            account = await self.read_account()
        return LoginStatus(
            status=session.status,
            error=session.error,
            account=account,
        )

    async def cancel_login(self, state: str) -> None:
        session = self._sessions.get(state)
        if session is None:
            raise CodexSubscriptionError(
                "CODEX_LOGIN_EXPIRED",
                "Login session not found or expired",
            )
        if session.status != "pending":
            return
        await self.ensure_runtime()
        await self.runtime.request(
            "account/login/cancel",
            {"loginId": session.result.login_id},
        )
        session.status = "cancelled"

    async def logout(self) -> None:
        await self.ensure_runtime()
        await self.runtime.request("account/logout", None)
        self._invalidate_account_cache()
        for session in self._sessions.values():
            if session.status == "pending":
                session.status = "cancelled"

    def _ensure_subscriptions(self) -> None:
        generation = self.runtime.generation_id
        if self._subscribed_generation == generation:
            return
        self._invalidate_account_cache()
        self.runtime.subscribe(
            "account/login/completed",
            self._on_login_completed,
        )
        self.runtime.subscribe("account/updated", self._on_account_updated)
        self._subscribed_generation = generation

    def _on_login_completed(self, params: dict) -> None:
        self._invalidate_account_cache()
        login_id = params.get("loginId")
        for session in self._sessions.values():
            if session.result.login_id != login_id:
                continue
            if params.get("success") is True:
                session.status = "completed"
                session.error = None
            else:
                session.status = "failed"
                session.error = "ChatGPT login failed"

    def _on_account_updated(self, params: dict) -> None:
        del params
        self._invalidate_account_cache()

    def _get_cached_account(
        self,
        *,
        force: bool,
    ) -> CodexAccountStatus | None:
        if force or self._account_cache is None:
            return None
        if self._account_cache_generation != self.runtime.generation_id:
            return None
        age = time.monotonic() - self._account_cache_time
        if age >= self.account_cache_ttl_seconds:
            return None
        return self._account_cache.model_copy()

    def _invalidate_account_cache(self) -> None:
        self._account_cache = None
        self._account_cache_generation = ""
        self._account_cache_time = 0.0


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    if not local or not domain:
        return None
    if len(local) == 1:
        masked = local[0] + "***"
    else:
        masked = local[0] + "*" * max(3, len(local) - 2) + local[-1]
    return f"{masked}@{domain}"
