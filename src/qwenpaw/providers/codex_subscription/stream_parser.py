"""Incremental SSE and Codex Responses event parser."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterable, AsyncIterator, Literal

from .errors import CodexProtocolError, CodexSubscriptionError

logger = logging.getLogger(__name__)


async def iter_sse_events(
    lines: AsyncIterable[str],
) -> AsyncIterator[dict[str, Any]]:
    data: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                payload = "\n".join(data)
                data.clear()
                if payload == "[DONE]":
                    return
                try:
                    value = json.loads(payload)
                except ValueError as exc:
                    raise CodexProtocolError(
                        "ChatGPT returned invalid streaming data"
                    ) from exc
                if isinstance(value, dict):
                    yield value
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        payload = "\n".join(data)
        if payload != "[DONE]":
            try:
                value = json.loads(payload)
            except ValueError as exc:
                raise CodexProtocolError(
                    "ChatGPT returned invalid streaming data"
                ) from exc
            if isinstance(value, dict):
                yield value


@dataclass(slots=True)
class StreamPart:
    kind: Literal["text", "reasoning", "tool", "usage", "completed"]
    text: str = ""
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    usage: dict[str, int] | None = None
    incomplete: bool = False
    details: dict[str, Any] | None = None


class ResponsesStreamParser:
    def __init__(self) -> None:
        self._calls: dict[str, dict[str, str]] = {}
        self._order: list[str] = []
        self._emitted: set[str] = set()

    def feed(self, event: dict[str, Any]) -> list[StreamPart]:
        event_type = str(event.get("type") or "")
        parts: list[StreamPart] = []
        usage = self._usage(event)
        if event_type == "response.output_text.delta":
            parts.append(
                StreamPart("text", text=str(event.get("delta") or ""))
            )
        elif event_type == "response.reasoning_summary_text.delta":
            parts.append(
                StreamPart("reasoning", text=str(event.get("delta") or ""))
            )
        elif event_type in {
            "response.output_item.added",
            "response.output_item.done",
        }:
            item = (
                event.get("item")
                if isinstance(event.get("item"), dict)
                else {}
            )
            if item.get("type") == "function_call":
                key = str(
                    item.get("id")
                    or item.get("call_id")
                    or event.get("item_id")
                    or len(self._order)
                )
                call = self._ensure(key)
                call["call_id"] = str(
                    item.get("call_id") or call["call_id"] or key
                )
                call["name"] = str(item.get("name") or call["name"])
                if item.get("arguments") is not None:
                    call["arguments"] = str(item.get("arguments"))
                if event_type.endswith("done"):
                    parts.extend(self._emit(key))
        elif event_type in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            key = str(
                event.get("item_id")
                or event.get("call_id")
                or event.get("output_index")
                or "0"
            )
            call = self._ensure(key)
            call["call_id"] = str(
                event.get("call_id") or call["call_id"] or key
            )
            call["name"] = str(event.get("name") or call["name"])
            if event_type.endswith("delta"):
                call["arguments"] += str(event.get("delta") or "")
            elif event.get("arguments") is not None:
                call["arguments"] = str(event.get("arguments"))
            if event_type.endswith("done"):
                parts.extend(self._emit(key))
        elif event_type in {"response.failed"}:
            raise CodexSubscriptionError(
                "CODEX_REQUEST_FAILED",
                "ChatGPT could not complete the response",
            )
        elif event_type in {"response.completed", "response.incomplete"}:
            for key in self._order:
                parts.extend(self._emit(key))
            response = event.get("response")
            incomplete_details = (
                response.get("incomplete_details")
                if isinstance(response, dict)
                else event.get("incomplete_details")
            )
            parts.append(
                StreamPart(
                    "completed",
                    incomplete=event_type.endswith("incomplete"),
                    details=(
                        incomplete_details
                        if isinstance(incomplete_details, dict)
                        else None
                    ),
                )
            )
        elif event_type:
            logger.debug(
                "Ignoring unknown Codex Responses event type=%s", event_type
            )
        if usage:
            parts.append(StreamPart("usage", usage=usage))
        return parts

    def _ensure(self, key: str) -> dict[str, str]:
        if key not in self._calls:
            self._calls[key] = {"call_id": "", "name": "", "arguments": ""}
            self._order.append(key)
        return self._calls[key]

    def _emit(self, key: str) -> list[StreamPart]:
        if key in self._emitted:
            return []
        call = self._calls[key]
        if not call["name"]:
            return []
        self._emitted.add(key)
        return [
            StreamPart(
                "tool",
                call_id=call["call_id"] or key,
                name=call["name"],
                arguments=call["arguments"] or "{}",
            )
        ]

    @staticmethod
    def _usage(event: dict[str, Any]) -> dict[str, int] | None:
        usage = event.get("usage")
        response = event.get("response")
        if not isinstance(usage, dict) and isinstance(response, dict):
            usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
        details = (
            usage.get("input_tokens_details")
            or usage.get("prompt_tokens_details")
            or {}
        )
        return {
            "input_tokens": int(
                usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
            ),
            "output_tokens": int(
                usage.get("output_tokens", usage.get("completion_tokens", 0))
                or 0
            ),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "cached_tokens": (
                int(details.get("cached_tokens", 0) or 0)
                if isinstance(details, dict)
                else 0
            ),
        }
