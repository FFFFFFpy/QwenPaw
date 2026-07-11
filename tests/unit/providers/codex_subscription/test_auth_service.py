# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

from qwenpaw.providers.codex_subscription.auth_service import AuthService


async def test_account_is_masked_and_contains_no_tokens(stub_runtime):
    service = AuthService(stub_runtime)
    account = await service.read_account()
    payload = account.model_dump()
    assert payload == {
        "connected": True,
        "email_masked": "p****n@example.test",
        "plan_type": "plus",
        "auth_type": "chatgpt",
    }
    assert "token" not in str(payload).lower()


async def test_account_reads_are_cached_and_update_invalidates(stub_runtime):
    service = AuthService(stub_runtime)
    await service.read_account()
    await service.read_account()
    assert [method for method, _ in stub_runtime.requests].count(
        "account/read",
    ) == 1

    stub_runtime.emit("account/updated", {})
    await service.read_account()
    assert [method for method, _ in stub_runtime.requests].count(
        "account/read",
    ) == 2


async def test_expired_ttl_preserves_last_known_connected_without_io(
    stub_runtime,
):
    service = AuthService(stub_runtime, account_cache_ttl_seconds=0)
    await service.read_account()
    reads_before = [method for method, _ in stub_runtime.requests].count(
        "account/read",
    )

    state, account = service.cached_account()

    assert state == "connected"
    assert account is not None and account.connected is True
    assert service.account_state_stale is True
    assert service.account_checked_at is not None
    assert [method for method, _ in stub_runtime.requests].count(
        "account/read",
    ) == reads_before


async def test_browser_and_device_login(stub_runtime):
    login_count = 0

    def login_response(params):
        nonlocal login_count
        login_count += 1
        if params["type"] == "chatgpt":
            return {
                "type": "chatgpt",
                "loginId": f"login-{login_count}",
                "authUrl": "https://chatgpt.example/authorize?secret=hidden",
            }
        return {
            "type": "chatgptDeviceCode",
            "loginId": f"login-{login_count}",
            "verificationUrl": "https://auth.example/device",
            "userCode": "ABCD-EFGH",
        }

    stub_runtime.responses["account/login/start"] = login_response
    service = AuthService(stub_runtime)
    browser = await service.start_login("browser")
    device = await service.start_login("device_code")
    assert browser.flow_type == "browser_redirect"
    assert browser.authorize_url is not None
    assert device.flow_type == "device_code"
    assert device.user_code == "ABCD-EFGH"
    assert browser.state != browser.login_id


async def test_login_completion_and_cancel(stub_runtime):
    stub_runtime.responses["account/login/start"] = {
        "type": "chatgpt",
        "loginId": "login-1",
        "authUrl": "https://chatgpt.example/authorize",
    }
    service = AuthService(stub_runtime)
    session = await service.start_login("browser")
    stub_runtime.emit(
        "account/login/completed",
        {"loginId": "login-1", "success": True, "error": None},
    )
    assert (await service.get_status(session.state)).status == "completed"

    second = await service.start_login("browser")
    await service.cancel_login(second.state)
    assert (await service.get_status(second.state)).status == "cancelled"
    assert ("account/login/cancel", {"loginId": "login-1"}) in (
        stub_runtime.requests
    )


async def test_failed_expired_concurrent_and_logout_states(stub_runtime):
    counter = 0

    def login_response(params):
        nonlocal counter
        counter += 1
        return {
            "type": params["type"],
            "loginId": f"login-{counter}",
            "authUrl": "https://chatgpt.example/authorize",
        }

    stub_runtime.responses["account/login/start"] = login_response
    service = AuthService(stub_runtime, session_ttl_seconds=0)
    first, second = await asyncio.gather(
        service.start_login("browser"),
        service.start_login("browser"),
    )
    assert first.state != second.state
    assert (await service.get_status(first.state)).status == "expired"

    active_service = AuthService(stub_runtime)
    active = await active_service.start_login("browser")
    stub_runtime.emit(
        "account/login/completed",
        {"loginId": active.login_id, "success": False},
    )
    failed = await active_service.get_status(active.state)
    assert failed.status == "failed"
    await active_service.logout()
    assert ("account/logout", None) in stub_runtime.requests
