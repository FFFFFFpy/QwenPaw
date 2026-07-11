"""Hardened HTTP transport for the Codex Responses compatibility endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx

from qwenpaw.__version__ import __version__

from .catalog import uses_responses_lite
from .errors import (
    CodexSubscriptionError,
    CodexTransportError,
    error_for_status,
)

CODEX_RESPONSES_COMPATIBILITY_ROUTE = True
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
ALLOWED_INFERENCE_HOSTS = {"chatgpt.com"}


class CodexResponsesHTTPClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        endpoint: str = CODEX_RESPONSES_URL,
        timeout: float = 600.0,
        allow_development_endpoint: bool = False,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_INFERENCE_HOSTS
        ):
            if not allow_development_endpoint:
                raise CodexSubscriptionError(
                    "CODEX_UNSAFE_ENDPOINT",
                    "The ChatGPT endpoint is not allowed",
                )
        self.endpoint = endpoint
        self._client = client
        self.timeout = timeout

    async def stream(
        self, *, body: dict[str, Any], access_token: str, account_id: str
    ) -> AsyncIterator[httpx.Response]:
        own = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=30),
            trust_env=True,
            follow_redirects=False,
            verify=True,
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            # The compatibility endpoint gates newer models on the Codex
            # protocol generation. Keep QwenPaw identifiable in the suffix.
            "originator": "codex_cli_rs",
            "User-Agent": (
                "codex_cli_rs/0.144.1 "
                f"(QwenPaw compatibility/{__version__})"
            ),
        }
        model = body.get("model")
        if isinstance(model, str) and uses_responses_lite(model):
            headers["x-openai-internal-codex-responses-lite"] = "true"
        try:
            async with client.stream(
                "POST", self.endpoint, headers=headers, json=body
            ) as response:
                if response.status_code >= 300:
                    raise error_for_status(response.status_code)
                yield response
        except httpx.HTTPError as exc:
            raise CodexTransportError(
                "CODEX_NETWORK", "Unable to connect to ChatGPT"
            ) from exc
        finally:
            if own:
                await client.aclose()
