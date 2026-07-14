# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""GPT Image generation over the ChatGPT/Codex compatibility route."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from qwenpaw.utils.http import SSRFSafeRequestError, download_ssrf_safe

from .catalog import record_model_availability
from .errors import CodexSubscriptionError
from .http_client import CodexResponsesHTTPClient
from .oauth import OAuthService
from .stream_parser import iter_sse_events
from .token_store import TokenStore

MAX_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BASE64_CHARS = 40 * 1024 * 1024
MAX_SSE_BYTES = 180 * 1024 * 1024
MAX_SSE_EVENTS = 100_000
ALLOWED_FORMATS = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}


@dataclass(slots=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    extension: str
    revised_prompt: str | None = None


class ImageGenerationService:
    """Transport-only image generator; QwenPaw remains the agent runtime."""

    def __init__(
        self,
        *,
        token_store: TokenStore | None = None,
        oauth_service: OAuthService | None = None,
        http_client: CodexResponsesHTTPClient | None = None,
    ) -> None:
        self.token_store = token_store or TokenStore()
        self.oauth_service = oauth_service or OAuthService(self.token_store)
        self.http_client = http_client or CodexResponsesHTTPClient()

    async def generate(
        self,
        *,
        prompt: str,
        references: list[str],
        workspace: Path,
        size: str,
        quality: str,
        output_format: str,
        background: str,
        count: int,
    ) -> list[GeneratedImage]:
        if not prompt.strip():
            raise CodexSubscriptionError(
                "CODEX_IMAGE_PROMPT_REQUIRED",
                "Image generation requires a prompt",
            )
        if not 1 <= count <= 4:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_COUNT_INVALID",
                "Image count must be 1 to 4",
            )
        if len(references) > 5:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_REFERENCES_INVALID",
                "At most five reference images are supported",
            )
        if output_format == "jpeg" and background == "transparent":
            raise CodexSubscriptionError(
                "CODEX_IMAGE_OPTIONS_INVALID",
                "JPEG output does not support a transparent background",
            )

        content: list[dict[str, Any]] = [
            {"type": "input_text", "text": prompt.strip()},
        ]
        for reference in references:
            content.append(
                {
                    "type": "input_image",
                    "image_url": await self._reference_data_url(
                        reference,
                        workspace,
                    ),
                },
            )
        body = {
            "model": "gpt-5.6-sol",
            "input": [{"role": "user", "content": content}],
            "instructions": "You are an image generation assistant.",
            "tools": [
                {
                    "type": "image_generation",
                    "model": "gpt-image-2",
                    "size": size,
                    "quality": quality,
                    "output_format": output_format,
                    "background": background,
                },
            ],
            "tool_choice": {"type": "image_generation"},
            "stream": True,
            "store": False,
            "parallel_tool_calls": False,
        }
        record = await self.token_store.get_valid(self.oauth_service.refresh)
        stale_token = record.access_token.get_secret_value()
        images: list[GeneratedImage] = []
        refreshed_after_401 = False
        for _ in range(count):
            try:
                images.extend(await self._request(body, record))
            except CodexSubscriptionError as exc:
                if exc.status_code == 401 and not refreshed_after_401:
                    record = await self.token_store.get_valid(
                        self.oauth_service.refresh,
                        force_refresh=True,
                        stale_access_token=stale_token,
                    )
                    refreshed_after_401 = True
                    images.extend(await self._request(body, record))
                else:
                    if exc.status_code in {403, 404}:
                        record_model_availability("gpt-image-2", "unavailable")
                    raise
            if len(images) >= count:
                break
        if not images:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_EMPTY",
                "ChatGPT returned no generated image",
            )
        if len(images) > count:
            images = images[:count]
        record_model_availability("gpt-image-2", "available")
        return images

    async def _request(
        self,
        body: dict[str, Any],
        record: Any,
    ) -> list[GeneratedImage]:
        results: list[GeneratedImage] = []
        seen: set[str] = set()
        total_bytes = 0
        event_count = 0
        try:
            async for response in self.http_client.stream(
                body=body,
                access_token=record.access_token.get_secret_value(),
                account_id=record.account_id.get_secret_value(),
                responses_lite=False,
            ):

                async def limited_lines(stream_response: httpx.Response):
                    nonlocal total_bytes
                    async for line in stream_response.aiter_lines():
                        total_bytes += len(line.encode("utf-8"))
                        if total_bytes > MAX_SSE_BYTES:
                            raise CodexSubscriptionError(
                                "CODEX_IMAGE_RESPONSE_TOO_LARGE",
                                "Image response exceeded the safety limit",
                            )
                        yield line

                async for event in iter_sse_events(limited_lines(response)):
                    event_count += 1
                    if event_count > MAX_SSE_EVENTS:
                        raise CodexSubscriptionError(
                            "CODEX_IMAGE_RESPONSE_TOO_LARGE",
                            "Image response contained too many events",
                        )
                    event_type = str(event.get("type") or "")
                    if event_type == "response.failed":
                        raise CodexSubscriptionError(
                            "CODEX_IMAGE_FAILED",
                            "ChatGPT could not generate the image",
                        )
                    if event_type == "response.incomplete":
                        raise CodexSubscriptionError(
                            "CODEX_IMAGE_INCOMPLETE",
                            "ChatGPT stopped before generating the image",
                            details=self._incomplete_details(event),
                        )
                    for item in self._image_items(event):
                        encoded = item.get("result")
                        if not isinstance(encoded, str) or not encoded:
                            continue
                        digest = hashlib.sha256(encoded.encode()).hexdigest()
                        if digest in seen:
                            continue
                        seen.add(digest)
                        results.append(
                            self._decode_image(
                                encoded,
                                revised_prompt=(
                                    str(item["revised_prompt"])
                                    if item.get("revised_prompt")
                                    else None
                                ),
                            ),
                        )
        except httpx.HTTPError as exc:
            raise CodexSubscriptionError(
                "CODEX_NETWORK",
                "Image generation connection was interrupted",
            ) from exc
        return results

    @staticmethod
    def _image_items(event: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        item = event.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "image_generation_call"
        ):
            items.append(item)
        response = event.get("response")
        output = response.get("output") if isinstance(response, dict) else None
        if isinstance(output, list):
            items.extend(
                value
                for value in output
                if isinstance(value, dict)
                and value.get("type") == "image_generation_call"
            )
        return items

    @staticmethod
    def _incomplete_details(event: dict[str, Any]) -> dict[str, Any] | None:
        response = event.get("response")
        details = (
            response.get("incomplete_details")
            if isinstance(response, dict)
            else None
        )
        return details if isinstance(details, dict) else None

    @staticmethod
    def _decode_image(
        encoded: str,
        *,
        revised_prompt: str | None,
    ) -> GeneratedImage:
        if len(encoded) > MAX_IMAGE_BASE64_CHARS:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_RESPONSE_TOO_LARGE",
                "Generated image exceeded the safety limit",
            )
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_INVALID",
                "Generated image data is invalid",
            ) from exc
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
                image_format = str(image.format or "").upper()
        except Exception as exc:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_INVALID",
                "Generated result is not a valid PNG, JPEG, or WebP image",
            ) from exc
        extension = ALLOWED_FORMATS.get(image_format)
        if extension is None:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_INVALID",
                "Generated result is not a supported raster image",
            )
        mime = "image/jpeg" if extension == "jpeg" else f"image/{extension}"
        return GeneratedImage(data, mime, extension, revised_prompt)

    async def _reference_data_url(self, value: str, workspace: Path) -> str:
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"}:
            data = await self._download_reference(value)
        else:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = workspace / path
            try:
                path = path.resolve(strict=True)
                path.relative_to(workspace.resolve())
            except (OSError, ValueError) as exc:
                raise CodexSubscriptionError(
                    "CODEX_IMAGE_REFERENCE_DENIED",
                    "Reference image must be inside the agent workspace",
                ) from exc
            if not path.is_file() or path.stat().st_size > MAX_REFERENCE_BYTES:
                raise CodexSubscriptionError(
                    "CODEX_IMAGE_REFERENCE_INVALID",
                    "Reference image is missing or too large",
                )
            data = await asyncio.to_thread(path.read_bytes)
        decoded = self._decode_image(
            base64.b64encode(data).decode(),
            revised_prompt=None,
        )
        encoded = base64.b64encode(data).decode()
        return f"data:{decoded.mime_type};base64,{encoded}"

    async def _download_reference(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_REFERENCE_DENIED",
                "Remote reference images must use HTTPS",
            )
        try:
            return await download_ssrf_safe(
                url,
                max_bytes=MAX_REFERENCE_BYTES,
                allowed_schemes=frozenset({"https"}),
            )
        except SSRFSafeRequestError as exc:
            raise CodexSubscriptionError(
                "CODEX_IMAGE_REFERENCE_DENIED",
                "Remote reference image was blocked by network policy",
            ) from exc
