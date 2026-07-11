"""Deterministic AgentScope-message to Codex turn-input mapping."""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import urlparse

from agentscope.message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    URLSource,
)

from .errors import CodexSubscriptionError


class MessageMapper:
    def __init__(self, *, max_image_bytes: int = 8 * 1024 * 1024) -> None:
        self.max_image_bytes = max_image_bytes

    def map_messages(self, messages: list[Msg]) -> list[dict[str, Any]]:
        lines = ['<QWENPAW_CONTEXT version="1">']
        images: list[dict[str, Any]] = []
        image_index = 0
        for message in messages:
            role = html.escape(message.role, quote=True)
            name = html.escape(message.name, quote=True)
            lines.append(f'<message role="{role}" name="{name}">')
            for block in message.get_content_blocks():
                if isinstance(block, TextBlock):
                    lines.append(f"<text>{_escape(block.text)}</text>")
                elif isinstance(block, ThinkingBlock):
                    # Hidden/reasoning content is never replayed to the model.
                    continue
                elif isinstance(block, ToolCallBlock):
                    lines.append(
                        '<tool-call id="{}" name="{}">{}</tool-call>'.format(
                            html.escape(block.id, quote=True),
                            html.escape(block.name, quote=True),
                            _escape(block.input),
                        ),
                    )
                elif isinstance(block, ToolResultBlock):
                    lines.append(
                        (
                            '<tool-result id="{}" name="{}">{}</tool-result>'
                        ).format(
                            html.escape(block.id, quote=True),
                            html.escape(block.name, quote=True),
                            _escape(_tool_result_text(block)),
                        ),
                    )
                elif isinstance(block, DataBlock):
                    image_index += 1
                    images.append(self._map_image(block))
                    lines.append(f'<image ref="image-{image_index}"/>')
            lines.append("</message>")
        lines.append("</QWENPAW_CONTEXT>")
        return [
            {
                "type": "text",
                "text": "\n".join(lines),
                "text_elements": [],
            },
            *images,
        ]

    def _map_image(self, block: DataBlock) -> dict[str, Any]:
        source = block.source
        if not source.media_type.lower().startswith("image/"):
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "Codex subscription currently accepts image attachments only",
            )
        if isinstance(source, Base64Source):
            estimated_size = len(source.data.rstrip("=")) * 3 // 4
            if estimated_size > self.max_image_bytes:
                raise CodexSubscriptionError(
                    "CODEX_ATTACHMENT_TOO_LARGE",
                    "The image attachment is too large for Codex",
                    details={
                        "payload_bytes": estimated_size,
                        "limit_bytes": self.max_image_bytes,
                        "attachment_count": 1,
                    },
                )
            return {
                "type": "image",
                "url": f"data:{source.media_type};base64,{source.data}",
            }
        if isinstance(source, URLSource):
            url = str(source.url)
            parsed = urlparse(url)
            if parsed.scheme not in {"https", "http", "data"}:
                raise CodexSubscriptionError(
                    "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED",
                    "Local paths are not accepted as Codex image inputs",
                )
            encoded_size = len(url.encode("utf-8"))
            if parsed.scheme == "data" and encoded_size > self.max_image_bytes:
                raise CodexSubscriptionError(
                    "CODEX_ATTACHMENT_TOO_LARGE",
                    "The image attachment is too large for Codex",
                    details={
                        "payload_bytes": encoded_size,
                        "limit_bytes": self.max_image_bytes,
                        "attachment_count": 1,
                    },
                )
            return {"type": "image", "url": url}
        raise CodexSubscriptionError(
            "CODEX_PROTOCOL_INCOMPATIBLE",
            "Unsupported image source",
        )


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _tool_result_text(block: ToolResultBlock) -> str:
    if isinstance(block.output, str):
        return block.output
    output: list[str] = []
    for item in block.output:
        if isinstance(item, TextBlock):
            output.append(item.text)
        elif isinstance(item, DataBlock):
            output.append(
                json.dumps(
                    {
                        "type": "attachment",
                        "media_type": item.source.media_type,
                    },
                    separators=(",", ":"),
                ),
            )
    return "\n".join(output)
