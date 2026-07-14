# -*- coding: utf-8 -*-
# pylint: disable=protected-access
import base64
import hashlib
import time

import pytest

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.oauth import (
    OAuthService,
    create_code_challenge,
    create_code_verifier,
)
from qwenpaw.providers.codex_subscription.token_store import TokenStore


def test_pkce_uses_sha256_base64url():
    verifier = create_code_verifier()
    assert 43 <= len(verifier) <= 128
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert create_code_challenge(verifier) == expected


def test_state_is_random_single_use_and_expires(tmp_path):
    service = OAuthService(TokenStore(tmp_path / "token.enc"))
    first = service.start()
    second = service.start()
    assert first["state"] != second["state"]
    assert len(first["state"]) >= 22
    service._consume(first["state"])
    with pytest.raises(CodexSubscriptionError):
        service._consume(first["state"])
    service._pending[second["state"]].created_at = time.time() - 901
    assert service.login_status(second["state"])["status"] == "expired"


@pytest.mark.asyncio
async def test_manual_callback_rejects_wrong_host(tmp_path):
    service = OAuthService(TokenStore(tmp_path / "token.enc"))
    state = service.start()["state"]
    with pytest.raises(CodexSubscriptionError):
        await service.complete(
            callback_url=(
                "https://evil.example/auth/callback?code=x&state=" + state
            ),
        )


@pytest.mark.asyncio
async def test_user_denial_records_failed_terminal_state(tmp_path):
    service = OAuthService(TokenStore(tmp_path / "token.enc"))
    state = service.start()["state"]
    with pytest.raises(CodexSubscriptionError):
        await service.complete(
            callback_url=(
                "http://localhost:1455/auth/callback"
                "?error=access_denied&state=" + state
            ),
        )
    status = service.login_status(state)
    assert status["status"] == "failed"
    assert status["error"]
