"""Encrypted, account-scoped OAuth credential storage and refresh locking."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from qwenpaw.constant import SECRET_DIR
from qwenpaw.security.secret_store import decrypt, encrypt

from .errors import CodexSubscriptionError

TOKEN_FORMAT_VERSION = 1
REFRESH_SKEW_SECONDS = 300

# Refresh coordination must span TokenStore instances.  Chat transports and
# image generation are constructed independently in a few runtime paths, but
# they still point at the same credential file and account.
_REFRESH_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


class TokenRecord(BaseModel):
    model_config = ConfigDict(repr=False, validate_assignment=True)
    version: int = TOKEN_FORMAT_VERSION
    account_local_id: str
    display_name: str | None = None
    masked_email: str | None = None
    is_active: bool = True
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr = Field(repr=False)
    id_token: SecretStr = Field(default=SecretStr(""), repr=False)
    account_id: SecretStr = Field(repr=False)
    expires_at: float
    last_refresh_at: float
    needs_login: bool = False


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or SECRET_DIR / "codex_subscription" / "oauth.enc"

    def _refresh_lock(self, account_local_id: str) -> asyncio.Lock:
        key = (str(self.path.expanduser().resolve()), account_local_id)
        return _REFRESH_LOCKS.setdefault(key, asyncio.Lock())

    def load(self, account_local_id: str = "default") -> TokenRecord | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink():
            raise CodexSubscriptionError(
                "CODEX_CREDENTIALS_UNSAFE",
                "Credential file must not be a symbolic link",
            )
        try:
            payload = json.loads(
                decrypt(self.path.read_text(encoding="utf-8"))
            )
            record = TokenRecord.model_validate(payload)
        except Exception as exc:
            raise CodexSubscriptionError(
                "CODEX_CREDENTIALS_INVALID",
                "Stored ChatGPT credentials are invalid",
            ) from exc
        return record if record.account_local_id == account_local_id else None

    def save(self, record: TokenRecord) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or self.path.is_symlink():
            raise CodexSubscriptionError(
                "CODEX_CREDENTIALS_UNSAFE",
                "Credential path must not contain symbolic links",
            )
        if os.name == "posix":
            os.chmod(parent, 0o700)
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        payload = record.model_dump(mode="json")
        payload.update(
            {
                "access_token": record.access_token.get_secret_value(),
                "refresh_token": record.refresh_token.get_secret_value(),
                "id_token": record.id_token.get_secret_value(),
                "account_id": record.account_id.get_secret_value(),
            }
        )
        encoded = encrypt(json.dumps(payload, ensure_ascii=False))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def delete(self, account_local_id: str = "default") -> None:
        record = self.load(account_local_id)
        if record is not None:
            self.path.unlink(missing_ok=True)

    async def get_valid(
        self,
        refresher: Callable[[TokenRecord], Awaitable[TokenRecord]],
        *,
        force_refresh: bool = False,
        stale_access_token: str | None = None,
    ) -> TokenRecord:
        record = self.load()
        if record is None or record.needs_login:
            raise CodexSubscriptionError(
                "CODEX_NOT_LOGGED_IN",
                "Sign in to ChatGPT before using this provider",
            )
        if (
            not force_refresh
            and record.expires_at > time.time() + REFRESH_SKEW_SECONDS
        ):
            return record
        lock = self._refresh_lock(record.account_local_id)
        async with lock:
            current = self.load(record.account_local_id)
            if current is None:
                raise CodexSubscriptionError(
                    "CODEX_NOT_LOGGED_IN",
                    "Sign in to ChatGPT before using this provider",
                )
            if (
                not force_refresh
                and current.expires_at > time.time() + REFRESH_SKEW_SECONDS
            ):
                return current
            if (
                force_refresh
                and stale_access_token is not None
                and current.access_token.get_secret_value()
                != stale_access_token
            ):
                return current
            refreshed = await refresher(current)
            self.save(refreshed)
            return refreshed

    def status(self) -> dict[str, object]:
        record = self.load()
        if record is None:
            return {"connected": False, "status": "not_logged_in"}
        expiring = record.expires_at <= time.time() + REFRESH_SKEW_SECONDS
        return {
            "connected": not record.needs_login,
            "status": (
                "needs_login"
                if record.needs_login
                else ("expiring" if expiring else "connected")
            ),
            "email_masked": record.masked_email,
            "display_name": record.display_name,
            "expires_at": int(record.expires_at),
        }


def assert_private_file(path: Path) -> bool:
    return os.name != "posix" or stat.S_IMODE(path.stat().st_mode) == 0o600
