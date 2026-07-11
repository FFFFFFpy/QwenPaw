# -*- coding: utf-8 -*-
from __future__ import annotations

import socket

import pytest

from qwenpaw.utils.http import (
    SSRFSafeRequestError,
    SSRFSafeResolver,
    download_ssrf_safe,
    is_loopback_host,
    is_loopback_url,
    trust_env_for_url,
)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost.",
        "127.0.0.1",
        "127.1.2.3",
        "::1",
        "[::1]",
    ],
)
def test_is_loopback_host_recognizes_loopback_targets(host: str) -> None:
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "",
        "0.0.0.0",
        "::",
        "192.168.1.10",
        "10.0.0.1",
        "example.com",
    ],
)
def test_is_loopback_host_keeps_non_loopback_targets(host: str) -> None:
    assert is_loopback_host(host) is False


# Local API calls should bypass env proxies for localhost/loopback targets.
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8088/api",
        "http://localhost.:8088/api",
        "http://127.0.0.1:8088/api",
        "http://127.1.2.3:8088/api",
        "http://[::1]:8088/api",
    ],
)
def test_is_loopback_url_recognizes_loopback_targets(url: str) -> None:
    assert is_loopback_url(url) is True
    assert trust_env_for_url(url) is False


# Non-loopback URLs keep httpx's normal env proxy behavior.
@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:8088/api",
        "https://example.com/api",
        "http://10.0.0.1:8088/api",
    ],
)
def test_is_loopback_url_keeps_non_loopback_targets(url: str) -> None:
    assert is_loopback_url(url) is False
    assert trust_env_for_url(url) is True


@pytest.mark.asyncio
async def test_ssrf_resolver_pins_first_dns_result_against_rebinding() -> None:
    calls = 0

    async def rebinding_getaddrinfo(*args, **kwargs):
        nonlocal calls
        calls += 1
        address = "93.184.216.34" if calls == 1 else "127.0.0.1"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, 443),
            )
        ]

    resolver = SSRFSafeResolver(rebinding_getaddrinfo)
    resolved = await resolver.resolve("example.test", 443)

    assert calls == 1
    assert resolved[0]["host"] == "93.184.216.34"


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "::1"])
async def test_ssrf_resolver_rejects_non_public_addresses(
    address: str,
) -> None:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET

    async def private_getaddrinfo(*args, **kwargs):
        return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]

    resolver = SSRFSafeResolver(private_getaddrinfo)
    with pytest.raises(SSRFSafeRequestError):
        await resolver.resolve("private.test", 443)


class _FakeContent:
    def __init__(self, body: bytes):
        self.body = body

    async def iter_chunked(self, size: int):
        del size
        yield self.body


class _FakeResponse:
    def __init__(self, status: int, headers=None, body: bytes = b""):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_ssrf_download_revalidates_redirect_and_disables_proxy(
    monkeypatch,
) -> None:
    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.responses = [
                _FakeResponse(302, {"Location": "http://127.0.0.1/secret"})
            ]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def get(self, url, **kwargs):
            captured.setdefault("requests", []).append((url, kwargs))
            return self.responses.pop(0)

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    monkeypatch.setattr(
        "qwenpaw.utils.http.aiohttp.ClientSession", FakeSession
    )

    with pytest.raises(SSRFSafeRequestError):
        await download_ssrf_safe(
            "https://public.example/image.png", max_bytes=1024
        )

    assert captured["trust_env"] is False
    assert captured["requests"][0][1]["allow_redirects"] is False
    assert captured["requests"][0][1]["proxy"] is None
