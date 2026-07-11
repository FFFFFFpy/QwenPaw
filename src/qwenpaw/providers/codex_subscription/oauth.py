"""Authorization Code + PKCE flow for the ChatGPT/Codex compatibility route."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .errors import CodexSubscriptionError
from .token_store import TokenRecord, TokenStore

OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_SCOPE = "openid profile email offline_access"
STATE_TTL_SECONDS = 15 * 60


@dataclass(slots=True)
class _PendingState:
    verifier: str
    created_at: float
    consumed: bool = False
    error: str | None = None


def create_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def create_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        value = json.loads(base64.urlsafe_b64decode(part))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def account_id_from_tokens(id_token: str, access_token: str) -> str:
    for token in (id_token, access_token):
        payload = decode_jwt_payload(token)
        auth = payload.get("https://api.openai.com/auth")
        if isinstance(auth, dict) and auth.get("chatgpt_account_id"):
            return str(auth["chatgpt_account_id"])
        for key in ("chatgpt_account_id", "account_id"):
            if payload.get(key):
                return str(payload[key])
    return ""


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    return f"{local[:1]}***{local[-1:] if len(local) > 1 else ''}@{domain}"


class OAuthService:
    def __init__(
        self, store: TokenStore, client: httpx.AsyncClient | None = None
    ) -> None:
        self.store = store
        self.client = client
        self._pending: dict[str, _PendingState] = {}
        self._results: dict[str, str] = {}
        self._listener_task: asyncio.Task[None] | None = None

    def start(self) -> dict[str, Any]:
        self._sweep()
        verifier = create_code_verifier()
        state = secrets.token_urlsafe(32)
        self._pending[state] = _PendingState(
            verifier=verifier, created_at=time.time()
        )
        self._results[state] = "pending"
        try:
            loop = asyncio.get_running_loop()
            if self._listener_task is None or self._listener_task.done():
                self._listener_task = loop.create_task(self._listen_once())
        except RuntimeError:
            pass
        query = urlencode(
            {
                "response_type": "code",
                "client_id": OAUTH_CLIENT_ID,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "scope": OAUTH_SCOPE,
                "code_challenge": create_code_challenge(verifier),
                "code_challenge_method": "S256",
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
                "state": state,
                "originator": "codex_cli_rs",
            }
        )
        return {
            "authorize_url": f"{OAUTH_AUTHORIZE_URL}?{query}",
            "state": state,
            "expires_in": STATE_TTL_SECONDS,
            "manual_callback_supported": True,
            "redirect_uri": OAUTH_REDIRECT_URI,
        }

    async def complete(
        self,
        *,
        callback_url: str | None = None,
        code: str | None = None,
        state: str | None = None,
    ) -> dict[str, object]:
        if callback_url:
            parsed = urlparse(callback_url.strip())
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"localhost", "127.0.0.1"}
                or parsed.path != "/auth/callback"
            ):
                raise CodexSubscriptionError(
                    "CODEX_OAUTH_INVALID_CALLBACK",
                    "The OAuth callback URL is invalid",
                )
            query = parse_qs(parsed.query)
            code = code or (query.get("code") or [""])[0]
            state = state or (query.get("state") or [""])[0]
            oauth_error = (query.get("error") or [""])[0]
            if oauth_error and state:
                self._consume(state)
                self._results[state] = "failed"
                raise CodexSubscriptionError(
                    "CODEX_OAUTH_FAILED",
                    "ChatGPT login was rejected",
                )
        if not code or not state:
            raise CodexSubscriptionError(
                "CODEX_OAUTH_STATE_INVALID",
                "OAuth completion requires code and state",
            )
        pending = self._consume(state)
        try:
            token_data = await self._token_request(
                {
                    "grant_type": "authorization_code",
                    "client_id": OAUTH_CLIENT_ID,
                    "code": code,
                    "redirect_uri": OAUTH_REDIRECT_URI,
                    "code_verifier": pending.verifier,
                },
                form=True,
            )
            record = self._record_from_response(token_data)
            self.store.save(record)
        except CodexSubscriptionError as exc:
            self._results[state] = "failed"
            pending.error = str(exc)
            raise
        self._results[state] = "completed"
        return self.store.status()

    def login_status(self, state: str) -> dict[str, object]:
        self._sweep()
        status = self._results.get(state, "expired")
        return {
            "status": status,
            "account": self.store.status() if status == "completed" else None,
            "error": ("ChatGPT login failed" if status == "failed" else None),
        }

    async def _listen_once(self) -> None:
        async def callback(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            success = False
            try:
                request_line = (
                    await asyncio.wait_for(reader.readline(), 5)
                ).decode("ascii", "replace")
                parts = request_line.split()
                target = parts[1] if len(parts) >= 2 else ""
                while True:
                    line = await asyncio.wait_for(reader.readline(), 5)
                    if line in {b"\r\n", b"\n", b""}:
                        break
                if target.startswith("/auth/callback?"):
                    await self.complete(
                        callback_url=f"http://localhost:1455{target}"
                    )
                    success = True
            except Exception:
                success = False
            body = (
                "<h1>ChatGPT login complete</h1>"
                "<p>You can return to QwenPaw.</p>"
                if success
                else "<h1>Login could not be completed</h1>"
                "<p>Copy this full URL and paste it into QwenPaw.</p>"
            ).encode()
            writer.write(
                b"HTTP/1.1 "
                + (b"200 OK" if success else b"400 Bad Request")
                + b"\r\nContent-Type: text/html; charset=utf-8\r\n"
                + b"Content-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        try:
            server = await asyncio.start_server(callback, "127.0.0.1", 1455)
        except OSError:
            return
        async with server:
            try:
                await asyncio.wait_for(
                    server.serve_forever(), STATE_TTL_SECONDS
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def refresh(self, record: TokenRecord) -> TokenRecord:
        try:
            data = await self._token_request(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": record.refresh_token.get_secret_value(),
                    "client_id": OAUTH_CLIENT_ID,
                    "scope": OAUTH_SCOPE,
                },
                form=False,
            )
        except CodexSubscriptionError as exc:
            if exc.error_code == "CODEX_OAUTH_INVALID_GRANT":
                record.needs_login = True
                self.store.save(record)
            raise
        now = time.time()
        access = str(data.get("access_token") or "")
        if not access:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "OAuth refresh did not return an access token",
            )
        record.access_token = access
        if data.get("refresh_token"):
            record.refresh_token = str(data["refresh_token"])
        if data.get("id_token"):
            record.id_token = str(data["id_token"])
        account_id = account_id_from_tokens(
            record.id_token.get_secret_value(), access
        )
        if account_id:
            record.account_id = account_id
        record.expires_at = now + int(data.get("expires_in") or 3600)
        record.last_refresh_at = now
        record.needs_login = False
        return record

    async def _token_request(
        self, payload: dict[str, str], *, form: bool
    ) -> dict[str, Any]:
        own = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=30, trust_env=True, follow_redirects=False
        )
        try:
            response = await client.post(
                OAUTH_TOKEN_URL,
                data=payload if form else None,
                json=None if form else payload,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise CodexSubscriptionError(
                "CODEX_NETWORK", "Unable to connect to ChatGPT authentication"
            ) from exc
        finally:
            if own:
                await client.aclose()
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            error = (
                str(body.get("error") or "") if isinstance(body, dict) else ""
            )
            code = (
                "CODEX_OAUTH_INVALID_GRANT"
                if error == "invalid_grant"
                else "CODEX_OAUTH_FAILED"
            )
            raise CodexSubscriptionError(
                code,
                (
                    "Login credentials have expired"
                    if code.endswith("INVALID_GRANT")
                    else "ChatGPT login was rejected"
                ),
                status_code=response.status_code,
            )
        if not isinstance(body, dict):
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "OAuth token response is invalid",
            )
        return body

    def _record_from_response(self, data: dict[str, Any]) -> TokenRecord:
        access = str(data.get("access_token") or "")
        refresh = str(data.get("refresh_token") or "")
        id_token = str(data.get("id_token") or "")
        account_id = account_id_from_tokens(id_token, access)
        if not access or not refresh or not account_id:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "OAuth response is missing required credentials",
            )
        payload = decode_jwt_payload(id_token)
        now = time.time()
        return TokenRecord(
            account_local_id="default",
            display_name=payload.get("name"),
            masked_email=mask_email(payload.get("email")),
            access_token=access,
            refresh_token=refresh,
            id_token=id_token,
            account_id=account_id,
            expires_at=now + int(data.get("expires_in") or 3600),
            last_refresh_at=now,
        )

    def _consume(self, state: str) -> _PendingState:
        pending = self._pending.pop(state, None)
        if (
            pending is None
            or pending.consumed
            or time.time() - pending.created_at > STATE_TTL_SECONDS
        ):
            raise CodexSubscriptionError(
                "CODEX_OAUTH_STATE_INVALID",
                "OAuth state is unknown, expired, or already used",
            )
        pending.consumed = True
        return pending

    def _sweep(self) -> None:
        cutoff = time.time() - STATE_TTL_SECONDS
        for key, value in self._pending.items():
            if (
                value.created_at <= cutoff
                and self._results.get(key) == "pending"
            ):
                self._results[key] = "expired"
        self._pending = {
            key: value
            for key, value in self._pending.items()
            if value.created_at > cutoff and not value.consumed
        }
