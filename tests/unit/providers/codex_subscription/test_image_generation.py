# -*- coding: utf-8 -*-
# pylint: disable=protected-access
import base64
import io
import time

import pytest
from PIL import Image

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.image_generation import (
    ImageGenerationService,
)
from qwenpaw.providers.codex_subscription.oauth import OAuthService
from qwenpaw.providers.codex_subscription.token_store import (
    TokenRecord,
    TokenStore,
)


def encoded_image(image_format: str) -> str:
    output = io.BytesIO()
    mode = "RGB" if image_format == "JPEG" else "RGBA"
    Image.new(mode, (2, 2), (255, 0, 0)).save(output, format=image_format)
    return base64.b64encode(output.getvalue()).decode()


class FakeResponse:
    def __init__(self, encoded: str):
        self.encoded = encoded

    async def aiter_lines(self):
        yield (
            'data: {"type":"response.output_item.done","item":'
            '{"type":"image_generation_call","result":"'
            + self.encoded
            + '","revised_prompt":"refined"}}'
        )
        yield ""
        yield (
            'data: {"type":"response.completed","response":' '{"output":[]}}'
        )
        yield ""


class FakeHTTP:
    def __init__(self, encoded: str):
        self.encoded = encoded
        self.body = None

    async def stream(self, **kwargs):
        self.body = kwargs["body"]
        yield FakeResponse(self.encoded)


def token_store(tmp_path):
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
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image_format", "extension", "mime"),
    [
        ("PNG", "png", "image/png"),
        ("JPEG", "jpeg", "image/jpeg"),
        ("WEBP", "webp", "image/webp"),
    ],
)
async def test_generates_valid_raster_formats(
    tmp_path,
    image_format,
    extension,
    mime,
):
    store = token_store(tmp_path)
    http = FakeHTTP(encoded_image(image_format))
    service = ImageGenerationService(
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=http,
    )
    images = await service.generate(
        prompt="draw a red square",
        references=[],
        workspace=tmp_path,
        size="1024x1024",
        quality="auto",
        output_format=extension,
        background="auto",
        count=1,
    )
    assert images[0].extension == extension
    assert images[0].mime_type == mime
    assert images[0].revised_prompt == "refined"
    assert http.body["model"] == "gpt-5.6-sol"
    assert http.body["tools"][0]["model"] == "gpt-image-2"
    assert "n" not in http.body["tools"][0]
    assert http.body["tool_choice"] == {"type": "image_generation"}
    assert http.body["store"] is False


@pytest.mark.asyncio
async def test_reference_edit_supports_multiple_validated_images(tmp_path):
    reference = tmp_path / "reference.png"
    reference.write_bytes(base64.b64decode(encoded_image("PNG")))
    store = token_store(tmp_path)
    http = FakeHTTP(encoded_image("PNG"))
    service = ImageGenerationService(
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=http,
    )
    await service.generate(
        prompt="make it blue",
        references=[str(reference), str(reference)],
        workspace=tmp_path,
        size="1024x1024",
        quality="auto",
        output_format="png",
        background="auto",
        count=1,
    )
    content = http.body["input"][0]["content"]
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert content[2]["type"] == "input_image"


def test_rejects_svg_or_non_raster_result():
    encoded = base64.b64encode(b"<svg></svg>").decode()
    with pytest.raises(CodexSubscriptionError) as caught:
        ImageGenerationService._decode_image(encoded, revised_prompt=None)
    assert caught.value.error_code == "CODEX_IMAGE_INVALID"


def test_reference_outside_workspace_is_denied(tmp_path):
    service = ImageGenerationService()
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(base64.b64decode(encoded_image("PNG")))
    with pytest.raises(CodexSubscriptionError) as caught:
        # The coroutine performs validation before its first real await.
        import asyncio

        asyncio.run(service._reference_data_url(str(outside), tmp_path))
    assert caught.value.error_code == "CODEX_IMAGE_REFERENCE_DENIED"


@pytest.mark.asyncio
async def test_service_rejects_jpeg_with_transparent_background(tmp_path):
    store = token_store(tmp_path)
    service = ImageGenerationService(
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=FakeHTTP(encoded_image("JPEG")),
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        await service.generate(
            prompt="draw",
            references=[],
            workspace=tmp_path,
            size="1024x1024",
            quality="auto",
            output_format="jpeg",
            background="transparent",
            count=1,
        )
    assert caught.value.error_code == "CODEX_IMAGE_OPTIONS_INVALID"
