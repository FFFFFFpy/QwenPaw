"""Real-httpx image generation against a local fake Responses SSE server."""

import asyncio
import base64
import io
import json
import time

import httpx
import pytest
from PIL import Image

from qwenpaw.providers.codex_subscription.http_client import (
    CodexResponsesHTTPClient,
)
from qwenpaw.providers.codex_subscription.image_generation import (
    ImageGenerationService,
)
from qwenpaw.providers.codex_subscription.oauth import OAuthService
from qwenpaw.providers.codex_subscription.token_store import (
    TokenRecord,
    TokenStore,
)


@pytest.mark.asyncio
async def test_image_generation_over_real_sse_transport(tmp_path):
    output = io.BytesIO()
    Image.new("RGBA", (3, 3), (0, 128, 255, 255)).save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode()
    received = {}

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        request_line = (await reader.readline()).decode()
        headers = {}
        while True:
            line = (await reader.readline()).decode()
            if line in {"\r\n", "\n", ""}:
                break
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
        body = await reader.readexactly(int(headers["content-length"]))
        received.update(
            request_line=request_line,
            headers=headers,
            body=json.loads(body),
        )
        event = json.dumps(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "result": encoded,
                    "revised_prompt": "blue square",
                },
            },
            separators=(",", ":"),
        )
        completed = json.dumps(
            {"type": "response.completed", "response": {"output": []}},
            separators=(",", ":"),
        )
        sse = f"data: {event}\n\ndata: {completed}\n\n".encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            + b"Content-Length: "
            + str(len(sse)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + sse
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
        )
    )
    async with server, httpx.AsyncClient() as client:
        service = ImageGenerationService(
            token_store=store,
            oauth_service=OAuthService(store),
            http_client=CodexResponsesHTTPClient(
                client=client,
                endpoint=f"http://127.0.0.1:{port}/responses",
                allow_development_endpoint=True,
            ),
        )
        images = await service.generate(
            prompt="draw a blue square",
            references=[],
            workspace=tmp_path,
            size="1024x1024",
            quality="auto",
            output_format="png",
            background="transparent",
            count=1,
        )

    assert images[0].data == output.getvalue()
    assert images[0].mime_type == "image/png"
    assert received["headers"]["authorization"] == "Bearer access"
    assert received["headers"]["chatgpt-account-id"] == "account"
    assert "x-openai-internal-codex-responses-lite" not in received["headers"]
    assert received["body"]["tools"][0]["model"] == "gpt-image-2"
    assert received["body"]["tools"][0]["background"] == "transparent"
    assert received["body"]["store"] is False
