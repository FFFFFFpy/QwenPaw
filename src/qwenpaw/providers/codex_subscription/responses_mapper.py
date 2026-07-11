"""Map AgentScope messages and tools to Codex Responses request objects."""

from __future__ import annotations

import base64
import json
from typing import Any

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

from .catalog import uses_responses_lite
from .errors import CodexSubscriptionError

ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


class ResponsesMapper:
    def __init__(
        self,
        *,
        max_image_bytes: int = 8 * 1024 * 1024,
        max_request_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.max_image_bytes = max_image_bytes
        self.max_request_bytes = max_request_bytes

    def build_request(
        self,
        *,
        model: str,
        messages: list[Msg],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        reasoning_effort: str | None = None,
        relay_reasoning: bool = True,
        structured_format: dict[str, Any] | None = None,
        parallel_tool_calls: bool = True,
    ) -> dict[str, Any]:
        responses_lite = uses_responses_lite(model)
        instructions: list[str] = []
        input_items: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                text = self._text(message.content)
                if text:
                    instructions.append(text)
                continue
            content: list[dict[str, Any]] = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    content.append({"type": "input_text", "text": block.text})
                elif isinstance(block, DataBlock):
                    content.append(self._image(block))
                elif isinstance(block, ThinkingBlock):
                    # Summaries are display-only and are never replayed.
                    continue
                elif isinstance(block, ToolCallBlock):
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": block.id,
                            "name": block.name,
                            "arguments": block.input,
                        }
                    )
                elif isinstance(block, ToolResultBlock):
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": block.id,
                            "output": self._tool_output(block.output),
                        }
                    )
                else:
                    raise CodexSubscriptionError(
                        "CODEX_UNSUPPORTED_MESSAGE",
                        f"Unsupported message block: {type(block).__name__}",
                    )
            if content:
                input_items.append(
                    {
                        "role": (
                            "assistant"
                            if message.role == "assistant"
                            else "user"
                        ),
                        "content": content,
                    }
                )
        body: dict[str, Any] = {
            "model": model,
            "instructions": "\n\n".join(instructions),
            "input": input_items,
            "stream": True,
            "store": False,
        }
        mapped_tools = self._tools(tools or [])
        if mapped_tools:
            body.update(
                {
                    "tools": mapped_tools,
                    "tool_choice": self._tool_choice(tool_choice),
                    # Responses Lite currently requires sequential tool calls.
                    "parallel_tool_calls": (
                        False if responses_lite else parallel_tool_calls
                    ),
                }
            )
        elif responses_lite:
            # Required by the Lite endpoint even when the request has no
            # tools. Omitting the field is rejected as an invalid request.
            body["parallel_tool_calls"] = False
        if reasoning_effort and reasoning_effort != "auto":
            body["reasoning"] = {
                "effort": reasoning_effort,
                "summary": "auto" if relay_reasoning else "none",
            }
        elif relay_reasoning:
            body["reasoning"] = {"summary": "auto"}
        if responses_lite:
            body.setdefault("reasoning", {})["context"] = "all_turns"
        if structured_format:
            body["text"] = {
                "format": self._structured_format(structured_format)
            }
        encoded = json.dumps(
            body, ensure_ascii=False, separators=(",", ":")
        ).encode()
        if len(encoded) > self.max_request_bytes:
            raise CodexSubscriptionError(
                "CODEX_REQUEST_TOO_LARGE", "The ChatGPT request is too large"
            )
        return body

    @staticmethod
    def _text(content: list[Any]) -> str:
        return "\n".join(
            block.text for block in content if isinstance(block, TextBlock)
        )

    def _image(self, block: DataBlock) -> dict[str, Any]:
        source = block.source
        media_type = source.media_type.lower()
        if media_type not in ALLOWED_IMAGE_MIME_TYPES:
            raise CodexSubscriptionError(
                "CODEX_UNSUPPORTED_IMAGE",
                "Only JPEG, PNG, GIF, and WebP images are supported",
            )
        if isinstance(source, URLSource):
            url = str(source.url)
        elif isinstance(source, Base64Source):
            try:
                size = len(base64.b64decode(source.data, validate=True))
            except ValueError as exc:
                raise CodexSubscriptionError(
                    "CODEX_UNSUPPORTED_IMAGE", "Image data is invalid"
                ) from exc
            if size > self.max_image_bytes:
                raise CodexSubscriptionError(
                    "CODEX_REQUEST_TOO_LARGE", "Image exceeds the 8 MiB limit"
                )
            url = f"data:{media_type};base64,{source.data}"
        else:
            raise CodexSubscriptionError(
                "CODEX_UNSUPPORTED_IMAGE", "Image source is unsupported"
            )
        return {"type": "input_image", "image_url": url}

    @staticmethod
    def _tool_output(output: Any) -> str:
        if isinstance(output, str):
            return output
        return "\n".join(
            item.text for item in output if isinstance(item, TextBlock)
        )

    @staticmethod
    def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mapped = []
        for tool in tools:
            fn = tool.get("function", tool)
            name = fn.get("name") if isinstance(fn, dict) else None
            if not name:
                continue
            mapped.append(
                {
                    "type": "function",
                    "name": name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )
        return mapped

    @staticmethod
    def _tool_choice(choice: Any) -> Any:
        if choice is None:
            return "auto"
        if hasattr(choice, "model_dump"):
            choice = choice.model_dump()
        if isinstance(choice, str):
            return (
                choice
                if choice in {"auto", "none", "required"}
                else {"type": "function", "name": choice}
            )
        if isinstance(choice, dict):
            mode = choice.get("mode", "auto")
            return (
                mode
                if mode in {"auto", "none", "required"}
                else {"type": "function", "name": mode}
            )
        return "auto"

    @staticmethod
    def _structured_format(value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") in {"json_object", "json_schema"}:
            return value
        return {
            "type": "json_schema",
            "name": "response",
            "strict": True,
            "schema": value,
        }
