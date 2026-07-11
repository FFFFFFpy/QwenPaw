import asyncio
import time

import pytest

from qwenpaw.providers.codex_subscription.token_store import (
    TokenRecord,
    TokenStore,
    assert_private_file,
)


def record(**updates):
    values = dict(
        account_local_id="default",
        access_token="access-secret",
        refresh_token="refresh-secret",
        id_token="id-secret",
        account_id="account-secret",
        expires_at=time.time() + 3600,
        last_refresh_at=time.time(),
    )
    values.update(updates)
    return TokenRecord(**values)


def test_encrypted_atomic_store_and_repr(tmp_path):
    store = TokenStore(tmp_path / "secrets" / "oauth.enc")
    value = record()
    store.save(value)
    raw = store.path.read_text()
    assert "access-secret" not in raw
    assert "access-secret" not in repr(value)
    assert store.load().access_token.get_secret_value() == "access-secret"
    assert assert_private_file(store.path)


@pytest.mark.asyncio
async def test_refresh_is_single_flight(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(record(expires_at=time.time() - 1))
    calls = 0

    async def refresh(current):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        current.expires_at = time.time() + 3600
        return current

    await asyncio.gather(*(store.get_valid(refresh) for _ in range(8)))
    assert calls == 1


@pytest.mark.asyncio
async def test_concurrent_401_force_refresh_is_generation_aware(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(record(access_token="stale"))
    calls = 0

    async def refresh(current):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        current.access_token = "fresh"
        return current

    await asyncio.gather(
        *(
            store.get_valid(
                refresh,
                force_refresh=True,
                stale_access_token="stale",
            )
            for _ in range(8)
        )
    )
    assert calls == 1


@pytest.mark.asyncio
async def test_concurrent_401_across_store_instances_refreshes_once(tmp_path):
    path = tmp_path / "oauth.enc"
    first = TokenStore(path)
    second = TokenStore(path)
    first.save(record(access_token="stale"))
    calls = 0

    async def refresh(current):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        current.access_token = "fresh"
        current.expires_at = time.time() + 3600
        return current

    results = await asyncio.gather(
        first.get_valid(
            refresh,
            force_refresh=True,
            stale_access_token="stale",
        ),
        second.get_valid(
            refresh,
            force_refresh=True,
            stale_access_token="stale",
        ),
    )

    assert calls == 1
    assert {item.access_token.get_secret_value() for item in results} == {
        "fresh"
    }
