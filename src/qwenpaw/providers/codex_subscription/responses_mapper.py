# -*- coding: utf-8 -*-
"""Build the Codex compatibility envelope around formatted Responses items."""

from __future__ import annotations

import json
from typing import Any

from .catalog import uses_responses_lite
from .errors import CodexSubscriptionError


class ResponsesMapper:
    def __init__(
        self,
        *,
        max_request_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.max_request_bytes = max_request_bytes

    def build_request(
        self,
        *,
        model: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        reasoning_effort: str | None = None,
        relay_reasoning: bool = True,
        structured_format: dict[str, Any] | None = None,
        parallel_tool_calls: bool = True,
    ) -> dict[str, Any]:
        responses_lite = uses_responses_lite(model)
        instructions: list[str] = []
        request_input: list[dict[str, Any]] = []
        for item in input_items:
            if item.get("role") != "system":
                request_input.append(item)
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("text"):
                    instructions.append(str(content["text"]))
        body: dict[str, Any] = {
            "model": model,
            "instructions": "\n\n".join(instructions),
            "input": request_input,
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
                },
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
                "format": self._structured_format(structured_format),
            }
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        if len(encoded) > self.max_request_bytes:
            raise CodexSubscriptionError(
                "CODEX_REQUEST_TOO_LARGE",
                "The ChatGPT request is too large",
            )
        return body

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
                        "parameters",
                        {"type": "object", "properties": {}},
                    ),
                },
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
