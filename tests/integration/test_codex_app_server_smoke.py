# -*- coding: utf-8 -*-
"""Opt-in smoke test for an explicitly installed real Codex App Server."""

from __future__ import annotations

import os

import pytest

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.runtime import CodexAppServerRuntime

pytestmark = [
    pytest.mark.codex_smoke,
    pytest.mark.skipif(
        os.getenv("QWENPAW_RUN_CODEX_SMOKE") != "1",
        reason="set QWENPAW_RUN_CODEX_SMOKE=1 to use a real Codex binary",
    ),
]


async def test_real_codex_app_server_protocol_smoke() -> None:
    """Validate schema, initialize, unknown request, and clean shutdown."""

    runtime = CodexAppServerRuntime()
    try:
        # start() validates the binary/version and generated schema before it
        # initializes the stdio connection.
        await runtime.start()
        assert runtime.binary_path
        assert runtime.binary_version
        assert runtime.capabilities is not None
        runtime.capabilities.validate_required_surface()

        with pytest.raises(CodexSubscriptionError) as unknown:
            await runtime.request("qwenpaw/unknown-smoke-request", {})
        assert unknown.value.error_code == "CODEX_TURN_FAILED"
    finally:
        await runtime.stop()


@pytest.mark.skipif(
    os.getenv("QWENPAW_RUN_CODEX_ACCOUNT_SMOKE") != "1",
    reason="account smoke requires explicit human authorization",
)
async def test_real_codex_account_smoke() -> None:
    """Separately verify account and model IO when explicitly authorized."""

    runtime = CodexAppServerRuntime()
    try:
        await runtime.start()
        account = await runtime.request(
            "account/read",
            {"refreshToken": False},
        )
        account_row = account.get("account")
        assert isinstance(account_row, dict)
        assert account_row.get("type") == "chatgpt"
        models = await runtime.request(
            "model/list",
            {"limit": 1, "includeHidden": False},
        )
        assert isinstance(models.get("data"), list)
        limits = await runtime.request("account/rateLimits/read", None)
        assert isinstance(limits, dict)
    finally:
        await runtime.stop()
