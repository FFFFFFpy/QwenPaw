from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
from typing import Any

import pytest

from qwenpaw.providers.codex_subscription.runtime import RuntimeState
from qwenpaw.providers.codex_subscription.schema_capabilities import (
    CodexCapabilities,
)


class StubRuntime:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.state = RuntimeState.STOPPED
        self.responses = responses or {}
        self.requests: list[tuple[str, dict | None]] = []
        self.handlers: dict[str, list] = defaultdict(list)
        self.server_handlers: dict[str, Any] = {}
        self.capabilities = CodexCapabilities.focused_contract()
        self.binary_path = "/usr/local/bin/codex"
        self.generation_id = "fixture"
        self.settings = SimpleNamespace(tool_wait_timeout_seconds=1.0)

    async def start(self) -> None:
        self.state = RuntimeState.READY

    async def stop(self) -> None:
        self.state = RuntimeState.STOPPED

    async def redetect(self) -> None:
        await self.stop()
        await self.start()

    async def request(
        self,
        method: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        del timeout
        self.requests.append((method, params))
        response = self.responses.get(method, {})
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(params)
        return response

    def subscribe(self, method: str, handler, *, thread_id=None):
        del thread_id
        self.handlers[method].append(handler)

        def unsubscribe() -> None:
            self.handlers[method].remove(handler)

        return unsubscribe

    def emit(self, method: str, params: dict) -> None:
        for handler in list(self.handlers[method]):
            handler(params)

    def register_server_request(self, method: str, handler) -> None:
        self.server_handlers[method] = handler

    async def server_request(self, method: str, params: dict) -> dict:
        return await self.server_handlers[method](params)


@pytest.fixture
def stub_runtime() -> StubRuntime:
    return StubRuntime(
        {
            "account/read": {
                "account": {
                    "type": "chatgpt",
                    "email": "person@example.test",
                    "planType": "plus",
                },
                "requiresOpenaiAuth": True,
            },
            "model/list": {
                "data": [
                    {
                        "id": "codex-test",
                        "displayName": "Codex Test",
                        "hidden": False,
                        "inputModalities": ["text", "image"],
                        "defaultReasoningEffort": "medium",
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "low"},
                            {"reasoningEffort": "medium"},
                        ],
                        "isDefault": True,
                    },
                ],
                "nextCursor": None,
            },
            "account/rateLimits/read": {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 37,
                        "windowDurationMins": 300,
                        "resetsAt": 1780000000,
                    },
                    "secondary": None,
                    "credits": {
                        "hasCredits": True,
                        "unlimited": False,
                        "balance": "5.00",
                    },
                    "planType": "plus",
                },
                "rateLimitsByLimitId": None,
                "rateLimitResetCredits": None,
            },
        },
    )
