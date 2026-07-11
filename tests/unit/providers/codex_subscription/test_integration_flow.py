# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from agentscope.message import (
    Msg,
    TextBlock,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.model import FinishedReason

from qwenpaw.providers.codex_subscription.provider import (
    CodexSubscriptionProvider,
)
from qwenpaw.providers.codex_subscription.runtime import (
    CodexAppServerRuntime,
)
from qwenpaw.providers.provider_manager import ProviderManager

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look up a fixture value",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    },
]


async def test_complete_fake_app_server_subscription_flow() -> None:
    script = Path(__file__).with_name("fake_app_server.py")
    runtime = CodexAppServerRuntime(command=(sys.executable, str(script)))
    provider = CodexSubscriptionProvider(
        id="openai-codex",
        name="OpenAI Codex",
        base_url="codex-app-server://local",
        require_api_key=False,
        supports_oauth=True,
        support_model_discovery=True,
    )
    provider.set_runtime(runtime)

    manager = ProviderManager.__new__(ProviderManager)
    manager.builtin_providers = {"openai-codex": provider}
    manager.custom_providers = {}
    manager.plugin_providers = {}
    resolved = manager.get_provider("openai-codex")
    assert resolved is provider

    try:
        await runtime.start()
        assert not (await provider.get_info()).oauth_connected

        login = await provider.auth_service.start_login("browser")
        await runtime.request("test/completeLogin", {})
        await asyncio.sleep(0)
        status = await provider.auth_service.get_status(login.state)
        assert status.status == "completed"
        assert status.account is not None and status.account.connected

        models = await provider.fetch_models()
        assert [model.id for model in models] == ["codex-fake"]
        limits = await provider.rate_limit_service.read()
        assert limits.primary is not None
        assert limits.primary.used_percent == 25

        model = provider.get_chat_model_instance("codex-fake")
        text_stream = await model(
            [
                Msg(
                    name="user",
                    role="user",
                    content=[TextBlock(text="hello")],
                ),
            ],
        )
        text_chunks = [chunk async for chunk in text_stream]
        assert any(
            block.type == "text" and block.text == "fake response"
            for chunk in text_chunks
            for block in chunk.content
        )

        tool_stream = await model(
            [
                Msg(
                    name="user",
                    role="user",
                    content=[TextBlock(text="use a tool")],
                ),
            ],
            tools=TOOLS,
        )
        tool_chunks = [chunk async for chunk in tool_stream]
        tool_call = next(
            block
            for chunk in tool_chunks
            for block in chunk.content
            if block.type == "tool_call"
        )
        final_stream = await model(
            [
                Msg(
                    name="assistant",
                    role="assistant",
                    content=[
                        ToolResultBlock(
                            id=tool_call.id,
                            name="lookup",
                            output="fixture result",
                            state=ToolResultState.SUCCESS,
                        ),
                    ],
                ),
            ],
            tools=TOOLS,
        )
        final_chunks = [chunk async for chunk in final_stream]
        assert any(
            block.type == "text" and block.text == "tool complete"
            for chunk in final_chunks
            for block in chunk.content
        )

        waiting_stream = await model(
            [
                Msg(
                    name="user",
                    role="user",
                    content=[TextBlock(text="WAIT_FOREVER")],
                ),
            ],
        )
        pending = asyncio.create_task(anext(waiting_stream))
        await asyncio.sleep(0.02)
        pending.cancel()
        interrupted = await pending
        assert interrupted["finished_reason"] is FinishedReason.INTERRUPTED
        await asyncio.sleep(0)

        await provider.auth_service.logout()
        assert not (await provider.auth_service.read_account()).connected
    finally:
        await runtime.stop()
