# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

from agentscope.message import Msg, TextBlock, ToolResultBlock, ToolResultState

from qwenpaw.providers.codex_subscription.chat_model import (
    CodexSubscriptionChatModel,
)
from qwenpaw.providers.codex_subscription.credential import (
    CodexSubscriptionCredential,
)
from qwenpaw.providers.codex_subscription.runtime import RuntimeState

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look up a value",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def test_dynamic_tool_round_trip_continues_same_turn(stub_runtime):
    stub_runtime.state = RuntimeState.READY
    stub_runtime.responses.update(
        {
            "account/read": {"account": {"type": "chatgpt"}},
            "model/list": {
                "data": [{"id": "codex-test", "inputModalities": ["text"]}],
            },
            "thread/start": {"thread": {"id": "thread-1"}},
        },
    )
    server_result: asyncio.Future[
        dict
    ] = asyncio.get_running_loop().create_future()

    def turn_start(_params):
        async def request_tool():
            result = await stub_runtime.server_request(
                "item/tool/call",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "callId": "call-1",
                    "namespace": None,
                    "tool": "lookup",
                    "arguments": {"q": "value"},
                },
            )
            server_result.set_result(result)
            stub_runtime.emit(
                "item/agentMessage/delta",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "answer-1",
                    "delta": "done",
                },
            )
            stub_runtime.emit(
                "turn/completed",
                {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "error": None,
                    },
                },
            )

        asyncio.create_task(request_tool())
        return {"turn": {"id": "turn-1"}}

    stub_runtime.responses["turn/start"] = turn_start
    model = CodexSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test"),
        model="codex-test",
        parameters=CodexSubscriptionChatModel.Parameters(),
        runtime=stub_runtime,
    )

    first = await model(
        [Msg(name="user", role="user", content=[TextBlock(text="use tool")])],
        tools=TOOLS,
    )
    first_chunks = [chunk async for chunk in first]
    tool_call = next(
        block
        for chunk in first_chunks
        for block in chunk.content
        if block.type == "tool_call"
    )
    assert tool_call.name == "lookup"

    second = await model(
        [
            Msg(
                name="assistant",
                role="assistant",
                content=[
                    ToolResultBlock(
                        id=tool_call.id,
                        name="lookup",
                        output="found",
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            ),
        ],
        tools=TOOLS,
    )
    second_chunks = [chunk async for chunk in second]
    assert any(
        block.type == "text" and block.text == "done"
        for chunk in second_chunks
        for block in chunk.content
    )
    assert (await server_result)["success"] is True
    thread_starts = [
        request
        for request in stub_runtime.requests
        if request[0] == "thread/start"
    ]
    assert len(thread_starts) == 1
