# -*- coding: utf-8 -*-
# pylint: disable=pointless-statement
"""End-to-end direct transport test against a local fake SSE server."""

import asyncio
import json
import time

import httpx
import pytest
from agentscope.message import TextBlock, UserMsg

from qwenpaw.providers.codex_subscription.chat_model import (
    ChatGPTSubscriptionChatModel,
)
from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.credential import (
    CodexSubscriptionCredential,
)
from qwenpaw.providers.codex_subscription.http_client import (
    CodexResponsesHTTPClient,
)
from qwenpaw.providers.codex_subscription.oauth import OAuthService
from qwenpaw.providers.codex_subscription.token_store import (
    TokenRecord,
    TokenStore,
)


@pytest.mark.asyncio
async def test_direct_transport_with_local_sse_server(tmp_path):
    received = {}

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        request_line = (await reader.readline()).decode()
        headers = {}
        while True:
            line = (await reader.readline()).decode()
            if line in {"\r\n", "\n", ""}:
                break
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
        body = await reader.readexactly(
            int(headers.get("content-length", "0")),
        )
        received.update(
            {
                "request_line": request_line,
                "headers": headers,
                "body": json.loads(body),
            },
        )
        sse = (
            b'data: {"type":"response.output_text.delta",'
            b'"delta":"PONG"}\n\n'
            b'data: {"type":"response.completed","response":'
            b'{"usage":{"input_tokens":1,"output_tokens":1,'
            b'"total_tokens":2}}}\n\n'
        )
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Content-Length: "
            + str(len(sse)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + sse,
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="account",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        ),
    )
    async with server, httpx.AsyncClient() as client:
        model = ChatGPTSubscriptionChatModel(
            credential=CodexSubscriptionCredential(id="test", name="test"),
            model="gpt-5.6-luna",
            parameters=ChatGPTSubscriptionChatModel.Parameters(),
            token_store=store,
            oauth_service=OAuthService(store),
            http_client=CodexResponsesHTTPClient(
                client=client,
                endpoint=f"http://127.0.0.1:{port}/responses",
                allow_development_endpoint=True,
            ),
        )
        response = await model(
            [UserMsg(name="user", content=[TextBlock(text="ping")])],
        )
        chunks = [chunk async for chunk in response]
    assert chunks[0].content[0].text == "PONG"
    assert chunks[-1].is_last
    assert chunks[-1].content[0].text == "PONG"
    assert chunks[-1].usage.input_tokens == 1
    assert received["body"]["store"] is False
    assert received["headers"]["authorization"] == "Bearer access"
    assert received["headers"]["chatgpt-account-id"] == "account"
    assert received["headers"]["originator"] == "codex_cli_rs"
    assert (
        received["headers"]["x-openai-internal-codex-responses-lite"] == "true"
    )
    assert received["body"]["reasoning"]["context"] == "all_turns"


@pytest.mark.asyncio
async def test_real_httpx_partial_body_disconnect_is_not_replayable(tmp_path):
    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        while await reader.readline() not in {b"\r\n", b"\n", b""}:
            pass
        sse = b'data: {"type":"response.output_text.delta","delta":"part"}\n\n'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Content-Length: 9999\r\nConnection: close\r\n\r\n" + sse,
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="account",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        ),
    )
    async with server, httpx.AsyncClient() as client:
        model = ChatGPTSubscriptionChatModel(
            credential=CodexSubscriptionCredential(id="test", name="test"),
            model="gpt-5.6-luna",
            parameters=ChatGPTSubscriptionChatModel.Parameters(),
            token_store=store,
            oauth_service=OAuthService(store),
            http_client=CodexResponsesHTTPClient(
                client=client,
                endpoint=f"http://127.0.0.1:{port}/responses",
                allow_development_endpoint=True,
            ),
        )
        response = await model(
            [UserMsg(name="user", content=[TextBlock(text="ping")])],
        )
        with pytest.raises(CodexSubscriptionError) as caught:
            [chunk async for chunk in response]
    assert caught.value.error_code == "CODEX_STREAM_REPLAY_UNSAFE"
