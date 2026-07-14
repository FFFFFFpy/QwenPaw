# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp

_LOOPBACK_HOSTNAMES = {"localhost"}


class SSRFSafeRequestError(ValueError):
    """Raised when an outbound URL violates QwenPaw's network policy."""


class SSRFSafeResolver(aiohttp.abc.AbstractResolver):
    """Resolve and validate in the connector's actual connection path.

    The connector consumes the vetted IPs returned here directly, avoiding a
    vulnerable "check DNS, then let the HTTP client resolve again" sequence.
    """

    def __init__(
        self,
        getaddrinfo: Callable[..., Awaitable[list[Any]]] | None = None,
    ) -> None:
        self._getaddrinfo = getaddrinfo

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_UNSPEC,
    ) -> list[dict[str, Any]]:
        resolver = self._getaddrinfo
        if resolver is None:
            resolver = asyncio.get_running_loop().getaddrinfo
        try:
            addresses = await resolver(
                host,
                port,
                family=family,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise SSRFSafeRequestError(
                "Remote host could not be resolved",
            ) from exc

        results: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for item in addresses:
            address = str(item[4][0]).split("%", 1)[0]
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise SSRFSafeRequestError(
                    "Remote host resolved to an invalid address",
                ) from exc
            if not ip.is_global:
                raise SSRFSafeRequestError(
                    "Remote host resolved to a non-public address",
                )
            key = (str(ip), port)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "hostname": host,
                    "host": str(ip),
                    "port": port,
                    "family": (
                        socket.AF_INET6 if ip.version == 6 else socket.AF_INET
                    ),
                    "proto": 0,
                    "flags": 0,
                },
            )
        if not results:
            raise SSRFSafeRequestError("Remote host did not resolve")
        return results

    async def close(self) -> None:
        return None


def _validate_ssrf_url(url: str, allowed_schemes: frozenset[str]) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme.lower() not in allowed_schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SSRFSafeRequestError("Remote URL is not allowed")


async def download_ssrf_safe(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 30,
    max_redirects: int = 3,
    allowed_schemes: frozenset[str] = frozenset({"https"}),
) -> bytes:
    """Download bytes through QwenPaw's DNS-pinned SSRF-safe request layer."""
    _validate_ssrf_url(url, allowed_schemes)
    connector = aiohttp.TCPConnector(
        resolver=SSRFSafeResolver(),
        use_dns_cache=True,
        ttl_dns_cache=timeout,
    )
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        # Proxies are deliberately disabled for arbitrary user-provided URLs:
        # a proxy could resolve the target independently and bypass pinning.
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=client_timeout,
            trust_env=False,
        ) as session:
            current = url
            for redirect_count in range(max_redirects + 1):
                _validate_ssrf_url(current, allowed_schemes)
                async with session.get(
                    current,
                    allow_redirects=False,
                    proxy=None,
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location or redirect_count >= max_redirects:
                            raise SSRFSafeRequestError(
                                "Remote redirect is invalid or too deep",
                            )
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        raise SSRFSafeRequestError(
                            "Remote resource could not be downloaded",
                        )
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                raise SSRFSafeRequestError(
                                    "Remote resource is too large",
                                )
                        except ValueError as exc:
                            raise SSRFSafeRequestError(
                                "Remote content length is invalid",
                            ) from exc
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.content.iter_chunked(
                        64 * 1024,
                    ):
                        size += len(chunk)
                        if size > max_bytes:
                            raise SSRFSafeRequestError(
                                "Remote resource is too large",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise SSRFSafeRequestError(
            "Remote resource could not be downloaded",
        ) from exc
    finally:
        if not connector.closed:
            await connector.close()
    raise SSRFSafeRequestError("Remote redirect could not be downloaded")


def is_loopback_host(host: str) -> bool:
    """Return True when *host* is localhost or a loopback IP address."""
    normalized = host.strip().strip("[]").lower().rstrip(".")
    if normalized in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    """Return True when *url* targets a localhost or loopback address."""
    return is_loopback_host(urlparse(url).hostname or "")


def trust_env_for_url(url: str) -> bool:
    """Return whether httpx should trust proxy/cert env vars for *url*."""
    return not is_loopback_url(url)
