# -*- coding: utf-8 -*-
"""Bridge App Server dynamic tool requests to AgentScope tool blocks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable
from urllib.parse import urlparse

from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    URLSource,
)

from .errors import CodexSubscriptionError

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def format_dynamic_tools(tools: list[dict]) -> list[dict[str, Any]]:
    """Convert sanitized OpenAI-style tools to App Server dynamic tools."""

    from qwenpaw.providers.openai_chat_model_compat import (
        _sanitize_tool_schemas,
    )

    formatted: list[dict[str, Any]] = []
    for tool in _sanitize_tool_schemas(tools):
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            function = tool if isinstance(tool, dict) else {}
        name = function.get("name")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise CodexSubscriptionError(
                "CODEX_TOOL_BRIDGE_UNSUPPORTED",
                "A QwenPaw tool has an invalid name for Codex",
            )
        description = function.get("description")
        parameters = function.get(
            "parameters",
            function.get("inputSchema", {}),
        )
        if not isinstance(parameters, dict):
            raise CodexSubscriptionError(
                "CODEX_TOOL_BRIDGE_UNSUPPORTED",
                f"QwenPaw tool '{name}' has an invalid input schema",
            )
        formatted.append(
            {
                "type": "function",
                "name": name,
                "description": (
                    description if isinstance(description, str) else ""
                ),
                "inputSchema": parameters,
            },
        )
    return formatted


@dataclass
class _PendingCall:
    qwenpaw_call_id: str
    app_call_id: str
    tool: str
    arguments: Any
    future: asyncio.Future[dict[str, Any]]


class ToolTurnBridge:
    def __init__(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
        tool_names: set[str],
        event_queue: asyncio.Queue[tuple[str, dict[str, Any]]],
        timeout_seconds: float = 600.0,
        timeout_callback: Callable[[], None] | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.tool_names = frozenset(tool_names)
        self.event_queue = event_queue
        self.timeout_seconds = timeout_seconds
        self.timeout_callback = timeout_callback
        self._pending: dict[str, _PendingCall] = {}
        self._app_ids: set[str] = set()
        self._turn_id_ready = asyncio.Event()
        if turn_id is not None:
            self._turn_id_ready.set()
        self._turn_start_error: CodexSubscriptionError | None = None
        self.terminal_error: CodexSubscriptionError | None = None
        self.closed = False

    @property
    def pending_ids(self) -> set[str]:
        return set(self._pending)

    def bind_turn(self, turn_id: str) -> None:
        """Bind a provisional bridge after turn/start returns."""

        if self.closed or self._turn_start_error is not None:
            raise CodexSubscriptionError(
                "CODEX_CANCELLED",
                "The provisional Codex tool bridge is no longer active",
            )
        if self.turn_id is not None and self.turn_id != turn_id:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "The Codex tool bridge was rebound to another turn",
            )
        self.turn_id = turn_id
        self._turn_id_ready.set()

    def fail_turn_start(self, error: CodexSubscriptionError) -> None:
        """Release requests that arrived before a failed turn/start."""

        self._turn_start_error = error
        self.terminal_error = error
        self._turn_id_ready.set()

    async def handle_server_request(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if self.closed:
            raise CodexSubscriptionError(
                "CODEX_CANCELLED",
                "The Codex tool turn is no longer active",
            )
        if params.get("threadId") != self.thread_id:
            raise CodexSubscriptionError(
                "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED",
                "Rejected a cross-thread Codex tool request",
            )
        await self._turn_id_ready.wait()
        if self._turn_start_error is not None:
            raise self._turn_start_error
        if self.closed:
            raise CodexSubscriptionError(
                "CODEX_CANCELLED",
                "The Codex tool turn is no longer active",
            )
        if params.get("turnId") != self.turn_id:
            raise CodexSubscriptionError(
                "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED",
                "Rejected a cross-turn Codex tool request",
            )
        tool = params.get("tool")
        call_id = params.get("callId")
        if not isinstance(tool, str) or tool not in self.tool_names:
            raise CodexSubscriptionError(
                "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED",
                "Codex requested a tool that QwenPaw did not register",
            )
        if (
            not isinstance(call_id, str)
            or not call_id
            or call_id in self._app_ids
        ):
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "Codex emitted an invalid or duplicate tool call identifier",
            )
        digest = hashlib.sha256(self.thread_id.encode()).hexdigest()[:8]
        qwenpaw_call_id = f"codex:{digest}:{call_id}"
        future = asyncio.get_running_loop().create_future()
        pending = _PendingCall(
            qwenpaw_call_id=qwenpaw_call_id,
            app_call_id=call_id,
            tool=tool,
            arguments=params.get("arguments", {}),
            future=future,
        )
        self._pending[qwenpaw_call_id] = pending
        self._app_ids.add(call_id)
        event = dict(params)
        event["qwenpawCallId"] = qwenpaw_call_id
        self.event_queue.put_nowait(("dynamic_tool_call", event))
        try:
            return await asyncio.wait_for(
                asyncio.shield(future),
                self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            self.terminal_error = CodexSubscriptionError(
                "CODEX_TOOL_WAIT_TIMEOUT",
                f"QwenPaw tool '{tool}' did not finish before the timeout",
            )
            self.event_queue.put_nowait(
                ("tool_timeout", {"qwenpawCallId": qwenpaw_call_id}),
            )
            if self.timeout_callback:
                self.timeout_callback()
            return {
                "contentItems": [
                    {"type": "inputText", "text": "QwenPaw tool timed out"},
                ],
                "success": False,
            }
        finally:
            self._pending.pop(qwenpaw_call_id, None)

    def make_tool_call_block(self, params: dict[str, Any]) -> ToolCallBlock:
        call_id = params.get("qwenpawCallId")
        tool = params.get("tool")
        if not isinstance(call_id, str) or not isinstance(tool, str):
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "Invalid Codex dynamic tool event",
            )
        return ToolCallBlock(
            id=call_id,
            name=tool,
            input=json.dumps(
                params.get("arguments", {}),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def submit_results(self, blocks: list[ToolResultBlock]) -> None:
        if self.terminal_error:
            raise self.terminal_error
        matching = {
            block.id: block for block in blocks if block.id in self._pending
        }
        if not matching:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "No ToolResult matched the active Codex tool request",
            )
        missing = self.pending_ids - set(matching)
        if missing:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "A Codex turn is still waiting for additional tool results",
                details={"missing_result_count": len(missing)},
            )
        for call_id, block in matching.items():
            pending = self._pending[call_id]
            if block.name != pending.tool:
                raise CodexSubscriptionError(
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    "ToolResult name does not match the Codex tool request",
                )
            if not pending.future.done():
                pending.future.set_result(_format_tool_result(block))

    def close(self) -> None:
        self.closed = True
        self._turn_id_ready.set()
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_result(
                    {
                        "contentItems": [
                            {
                                "type": "inputText",
                                "text": "QwenPaw cancelled the tool request",
                            },
                        ],
                        "success": False,
                    },
                )
        self._pending.clear()


class ToolBridgeRegistry:
    """Route one runtime's server requests to isolated per-thread bridges."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._bridges: dict[str, ToolTurnBridge] = {}
        self._registered_generation = ""

    @property
    def active_count(self) -> int:
        return len(self._bridges)

    def bind_generation(self) -> None:
        """Install the global dynamic-tool route as soon as RPC exists."""

        generation = self.runtime.generation_id
        if self._registered_generation == generation:
            return
        for bridge in self._bridges.values():
            bridge.close()
        self._bridges.clear()
        self.runtime.register_server_request("item/tool/call", self.route)
        self._registered_generation = generation

    def add(self, bridge: ToolTurnBridge) -> None:
        if bridge.thread_id in self._bridges:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "Duplicate active Codex thread",
            )
        self._bridges[bridge.thread_id] = bridge

    def remove(self, thread_id: str) -> None:
        self._bridges.pop(thread_id, None)

    async def route(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = params.get("threadId")
        bridge = (
            self._bridges.get(thread_id)
            if isinstance(thread_id, str)
            else None
        )
        if bridge is None:
            raise CodexSubscriptionError(
                "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED",
                "Rejected a Codex tool request for an inactive thread",
            )
        return await bridge.handle_server_request(params)


def get_tool_registry(runtime: Any) -> ToolBridgeRegistry:
    registry = getattr(runtime, "_qwenpaw_tool_registry", None)
    if not isinstance(registry, ToolBridgeRegistry):
        registry = ToolBridgeRegistry(runtime)
        setattr(runtime, "_qwenpaw_tool_registry", registry)
    return registry


def _format_tool_result(block: ToolResultBlock) -> dict[str, Any]:
    state = str(block.state)
    success = state == "success"
    content_items: list[dict[str, Any]] = []
    prefix = "" if success else f"QwenPaw tool result ({state}): "
    if isinstance(block.output, str):
        content_items.append(
            {"type": "inputText", "text": prefix + block.output},
        )
    else:
        for item in block.output:
            if isinstance(item, TextBlock):
                content_items.append(
                    {"type": "inputText", "text": prefix + item.text},
                )
                prefix = ""
            elif isinstance(item, DataBlock):
                source = item.source
                if isinstance(source, URLSource):
                    url = str(source.url)
                    if source.media_type.startswith("image/") and urlparse(
                        url,
                    ).scheme in {"http", "https", "data"}:
                        content_items.append(
                            {"type": "inputImage", "imageUrl": url},
                        )
                elif isinstance(source, Base64Source):
                    if source.media_type.startswith("image/"):
                        content_items.append(
                            {
                                "type": "inputImage",
                                "imageUrl": (
                                    f"data:{source.media_type};base64,"
                                    f"{source.data}"
                                ),
                            },
                        )
    if not content_items:
        content_items.append(
            {"type": "inputText", "text": prefix or "QwenPaw tool completed"},
        )
    return {"contentItems": content_items, "success": success}
