from __future__ import annotations

import asyncio
from collections import defaultdict
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.runtime import RuntimeState
from qwenpaw.providers.codex_subscription.schema_capabilities import (
    CodexCapabilities,
)


class StubRuntime:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.state = RuntimeState.STOPPED
        self.responses = responses or {}
        self.requests: list[tuple[str, dict | None]] = []
        self.handlers: dict[str, list[tuple[Any, str | None]]] = defaultdict(
            list
        )
        self.server_handlers: dict[str, Any] = {}
        self.capabilities = CodexCapabilities.focused_contract()
        self.binary_path = "/usr/local/bin/codex"
        self.binary_version: str | None = None
        self.generation_id = "fixture"
        self.settings = SimpleNamespace(
            tool_wait_timeout_seconds=1.0,
            max_message_bytes=4 * 1024 * 1024,
        )
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._background_errors: list[BaseException] = []
        self.turn_cleanup_error: CodexSubscriptionError | None = None

    async def start(self) -> None:
        self.state = RuntimeState.READY
        self.turn_cleanup_error = None

    async def stop(self) -> None:
        await self.wait_background_tasks()
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
        if inspect.isawaitable(response):
            response = await response
        return response

    @property
    def background_task_count(self) -> int:
        return len(self._background_tasks)

    @property
    def background_errors(self) -> tuple[BaseException, ...]:
        return tuple(self._background_errors)

    def create_background_task(self, coroutine, *, name: str):
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)

        def completed(done):
            self._background_tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self._background_errors.append(done.exception())

        task.add_done_callback(completed)
        return task

    async def wait_background_tasks(self) -> None:
        while self._background_tasks:
            await asyncio.gather(
                *tuple(self._background_tasks),
                return_exceptions=True,
            )

    def mark_turn_cleanup_failed(self, error: CodexSubscriptionError) -> None:
        self.turn_cleanup_error = error

    def clear_turn_cleanup_error(
        self,
        error: CodexSubscriptionError | None = None,
    ) -> None:
        if error is None or self.turn_cleanup_error is error:
            self.turn_cleanup_error = None

    def assert_turn_start_allowed(self) -> None:
        if self.turn_cleanup_error is not None:
            raise self.turn_cleanup_error

    def subscribe(self, method: str, handler, *, thread_id=None):
        entry = (handler, thread_id)
        self.handlers[method].append(entry)

        def unsubscribe() -> None:
            self.handlers[method].remove(entry)

        return unsubscribe

    def emit(self, method: str, params: dict) -> None:
        for handler, thread_id in list(self.handlers[method]):
            if thread_id is None or params.get("threadId") == thread_id:
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
