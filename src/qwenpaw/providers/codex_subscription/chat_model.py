"""AgentScope ChatModel adapter for Codex App Server turns."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import hashlib
import logging
import os
import tempfile
import time
from typing import Any

from agentscope.credential import CredentialBase
from agentscope.message import (
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    FinishedReason,
)
from pydantic import BaseModel

from .auth_service import AuthService
from .errors import CodexSubscriptionError
from .message_mapper import MessageMapper
from .runtime import CodexAppServerRuntime, RuntimeState
from .schema_capabilities import build_restricted_sandbox_policy
from .tool_bridge import format_dynamic_tools
from .turn_bridge import CleanupState, TurnBridge

logger = logging.getLogger(__name__)

_BLOCKED_ITEM_TYPES = {
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "webSearch",
}

_DEVELOPER_INSTRUCTIONS = (
    "QwenPaw is the sole agent and permission authority. Do not use built-in "
    "command execution, file changes, web search, MCP tools, sub-agents, or "
    "any other side-effecting Codex tool. Respond using model output only. "
    "QwenPaw-provided dynamic tools, when present, are the only allowed "
    "action path."
)


class CodexSubscriptionChatModel(ChatModelBase):
    class Parameters(BaseModel):
        reasoning_effort: str | None = None

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        parameters: BaseModel,
        *,
        runtime: CodexAppServerRuntime,
        relay_reasoning: bool = True,
        stream: bool = True,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        context_size: int = 32768,
        message_mapper: MessageMapper | None = None,
        auth_service: AuthService | None = None,
    ) -> None:
        self.runtime = runtime
        self.auth_service = auth_service or AuthService(runtime)
        self.relay_reasoning = relay_reasoning
        self.message_mapper = message_mapper or MessageMapper()
        self._active_turn: TurnBridge | None = None
        self._starting_turn = False
        self._cleanup_bridge: TurnBridge | None = None
        self._cleanup_barrier: asyncio.Task[None] | None = None
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
        )

    async def __call__(
        self,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        result = await super().__call__(messages, tools, tool_choice, **kwargs)
        if isinstance(result, ChatResponse):
            return result

        async def cancellation_aware() -> AsyncGenerator[ChatResponse, None]:
            async for chunk in result:
                current = asyncio.current_task()
                if chunk.is_last and (
                    (current and current.cancelling())
                    or "codex_turn_id" not in chunk.metadata
                ):
                    chunk["finished_reason"] = FinishedReason.INTERRUPTED
                yield chunk

        return cancellation_aware()

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        await self._await_cleanup_barrier()
        if self._active_turn is not None:
            bridge = self._active_turn
            tool_bridge = bridge.tool_bridge
            if tool_bridge is None:
                raise CodexSubscriptionError(
                    "CODEX_TURN_FAILED",
                    "A Codex turn is already active on this model instance",
                )
            if tool_bridge.terminal_error:
                error = tool_bridge.terminal_error
                task = self._track_cleanup(bridge, interrupt=True)
                self._active_turn = None
                await asyncio.shield(task)
                raise error
            results = _extract_tool_results(messages)
            try:
                tool_bridge.submit_results(results)
            except Exception:
                task = self._track_cleanup(bridge, interrupt=True)
                self._active_turn = None
                await asyncio.shield(task)
                raise
            stream = self._consume_turn(bridge, model_name)
            return await self._maybe_collect(stream)

        if self._starting_turn:
            raise CodexSubscriptionError(
                "CODEX_TURN_FAILED",
                "Concurrent calls on one Codex model instance are not "
                "supported",
            )

        dynamic_tools = format_dynamic_tools(tools) if tools else []
        dynamic_tools, tool_instruction = _apply_tool_choice(
            dynamic_tools,
            tool_choice,
        )
        self._starting_turn = True
        stream = self._stream_new_turn(
            model_name,
            messages,
            generate_kwargs,
            dynamic_tools,
            tool_instruction,
        )
        return await self._maybe_collect(stream)

    async def _maybe_collect(
        self,
        stream: AsyncGenerator[ChatResponse, None],
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        if self.stream:
            return stream
        collected = ChatResponse(content=[], is_last=True)
        async for chunk in stream:
            if not chunk.is_last:
                collected.append_chat_response(chunk)
            else:
                collected["finished_reason"] = chunk["finished_reason"]
                collected.metadata.update(chunk.metadata)
        return collected

    async def _stream_new_turn(
        self,
        model_name: str,
        messages: list[Msg],
        generate_kwargs: dict[str, Any],
        dynamic_tools: list[dict[str, Any]],
        tool_instruction: str,
    ) -> AsyncGenerator[ChatResponse, None]:
        temporary = tempfile.TemporaryDirectory(prefix="qwenpaw-codex-turn-")
        bridge: TurnBridge | None = None
        try:
            if self.runtime.state is not RuntimeState.READY:
                await self.runtime.start()
            capabilities = self.runtime.capabilities
            if capabilities is None:
                raise CodexSubscriptionError(
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    "Codex capabilities are unavailable",
                )
            if dynamic_tools:
                disabled = os.getenv(
                    "QWENPAW_CODEX_DYNAMIC_TOOLS", "auto"
                ).lower()
                if (
                    disabled in {"0", "false", "no", "off"}
                    or capabilities is None
                    or not capabilities.dynamic_tools
                ):
                    raise CodexSubscriptionError(
                        "CODEX_TOOL_BRIDGE_UNSUPPORTED",
                        "The installed Codex App Server does not support "
                        "dynamic tools",
                    )

            account = await self.auth_service.read_account()
            if not account.connected:
                raise CodexSubscriptionError(
                    "CODEX_NOT_LOGGED_IN",
                    "Connect ChatGPT before using OpenAI Codex",
                )

            models = await self.runtime.request(
                "model/list",
                {"limit": 100, "includeHidden": False},
            )
            model_row = next(
                (
                    item
                    for item in models.get("data", [])
                    if isinstance(item, dict)
                    and (
                        item.get("id") == model_name
                        or item.get("model") == model_name
                    )
                ),
                None,
            )
            if model_row is None:
                raise CodexSubscriptionError(
                    "CODEX_MODEL_UNAVAILABLE",
                    f"Codex model '{model_name}' is no longer available",
                )

            turn_input = self.message_mapper.map_messages(messages)
            if any(item.get("type") == "image" for item in turn_input):
                modalities = model_row.get("inputModalities", ["text"])
                if "image" not in modalities:
                    raise CodexSubscriptionError(
                        "CODEX_PROTOCOL_INCOMPATIBLE",
                        f"Codex model '{model_name}' does not support image "
                        "input",
                    )

            thread_params: dict[str, Any] = {
                "model": model_name,
                "cwd": temporary.name,
                "approvalPolicy": "never",
                "sandbox": build_restricted_sandbox_policy(
                    capabilities,
                    temporary.name,
                ),
                "ephemeral": True,
                "serviceName": "qwenpaw",
                "developerInstructions": (
                    _DEVELOPER_INSTRUCTIONS + tool_instruction
                ),
            }
            if capabilities.runtime_workspace_roots:
                thread_params["runtimeWorkspaceRoots"] = [temporary.name]
            if capabilities.environments_field:
                thread_params["environments"] = []
            if dynamic_tools:
                thread_params["dynamicTools"] = dynamic_tools
            thread_response = await self.runtime.request(
                "thread/start",
                thread_params,
            )
            thread = thread_response.get("thread")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if not isinstance(thread_id, str) or not thread_id:
                raise CodexSubscriptionError(
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    "Codex did not return a thread identifier",
                )

            bridge = TurnBridge(self.runtime, thread_id, temporary)
            bridge.subscribe(
                (
                    "item/agentMessage/delta",
                    "item/reasoning/summaryTextDelta",
                    "error",
                    "item/started",
                    "turn/completed",
                ),
            )

            effort = generate_kwargs.get("reasoning_effort")
            if not isinstance(effort, str):
                effort = getattr(self.parameters, "reasoning_effort", None)
            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": turn_input,
            }
            if effort:
                turn_params["effort"] = effort
            turn_response = await self.runtime.request(
                "turn/start", turn_params
            )
            turn = turn_response.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise CodexSubscriptionError(
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    "Codex did not return a turn identifier",
                )
            bridge.turn_id = turn_id
            if dynamic_tools:
                names = {str(tool["name"]) for tool in dynamic_tools}
                active_bridge = bridge

                def on_tool_timeout() -> None:
                    self._track_cleanup(active_bridge, interrupt=True)
                    if self._active_turn is active_bridge:
                        self._active_turn = None

                bridge.enable_tools(
                    names,
                    self.runtime.settings.tool_wait_timeout_seconds,
                    on_tool_timeout,
                )
            self._active_turn = bridge
            logger.info(
                "Codex turn started generation=%s model=%s thread=%s "
                "turn=%s",
                self.runtime.generation_id,
                model_name,
                _short_id(bridge.thread_id),
                _short_id(turn_id),
            )
            async for chunk in self._consume_turn(bridge, model_name):
                yield chunk
            bridge = None
        finally:
            self._starting_turn = False
            if bridge is not None and self._active_turn is not bridge:
                task = self._track_cleanup(bridge, interrupt=True)
                await asyncio.shield(task)
            elif bridge is None and self._active_turn is None:
                temporary.cleanup()

    async def _consume_turn(
        self,
        bridge: TurnBridge,
        model_name: str,
    ) -> AsyncGenerator[ChatResponse, None]:
        keep_active = False
        completed = False
        started_at = time.monotonic()
        try:
            while True:
                try:
                    method, params = await bridge.next_event()
                except asyncio.TimeoutError:
                    raise CodexSubscriptionError(
                        "CODEX_TURN_FAILED",
                        "Codex turn timed out",
                    ) from None

                event_turn_id = params.get("turnId")
                if (
                    isinstance(event_turn_id, str)
                    and event_turn_id != bridge.turn_id
                ):
                    continue

                if method == "dynamic_tool_call":
                    if bridge.tool_bridge is None:
                        raise CodexSubscriptionError(
                            "CODEX_TOOL_BRIDGE_UNSUPPORTED",
                            "Codex requested a dynamic tool without "
                            "negotiation",
                        )
                    keep_active = True
                    blocks: list[
                        TextBlock | ToolCallBlock | ThinkingBlock | DataBlock
                    ] = [bridge.tool_bridge.make_tool_call_block(params)]
                    await asyncio.sleep(0)
                    while not bridge.queue.empty():
                        queued_method, queued_params = (
                            bridge.queue.get_nowait()
                        )
                        if queued_method == "dynamic_tool_call":
                            blocks.append(
                                bridge.tool_bridge.make_tool_call_block(
                                    queued_params
                                )
                            )
                        else:
                            bridge.backlog.append(
                                (queued_method, queued_params)
                            )
                    yield ChatResponse(
                        content=blocks,
                        is_last=True,
                        metadata={
                            "codex_thread_id": bridge.thread_id,
                            "codex_turn_id": bridge.turn_id or "",
                        },
                    )
                    return
                if method == "tool_timeout":
                    if (
                        bridge.tool_bridge
                        and bridge.tool_bridge.terminal_error
                    ):
                        raise bridge.tool_bridge.terminal_error
                    raise CodexSubscriptionError(
                        "CODEX_TOOL_WAIT_TIMEOUT",
                        "Codex tool result timed out",
                    )
                if method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    if isinstance(delta, str) and delta:
                        yield ChatResponse(
                            content=[
                                TextBlock(
                                    text=delta,
                                    id=str(
                                        params.get("itemId") or "codex-text"
                                    ),
                                ),
                            ],
                            is_last=False,
                        )
                elif method == "item/reasoning/summaryTextDelta":
                    delta = params.get("delta")
                    if (
                        self.relay_reasoning
                        and isinstance(delta, str)
                        and delta
                    ):
                        yield ChatResponse(
                            content=[
                                ThinkingBlock(
                                    thinking=delta,
                                    id=str(
                                        params.get("itemId")
                                        or "codex-reasoning"
                                    ),
                                ),
                            ],
                            is_last=False,
                        )
                elif method == "item/started":
                    item = params.get("item")
                    item_type = (
                        item.get("type") if isinstance(item, dict) else None
                    )
                    if item_type in _BLOCKED_ITEM_TYPES:
                        raise CodexSubscriptionError(
                            "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED",
                            "Codex attempted a built-in side effect blocked "
                            "by QwenPaw",
                            details={"event_type": str(item_type)},
                        )
                    if (
                        item_type == "dynamicToolCall"
                        and bridge.tool_bridge is None
                    ):
                        raise CodexSubscriptionError(
                            "CODEX_TOOL_BRIDGE_UNSUPPORTED",
                            "Codex attempted an unregistered dynamic tool",
                        )
                elif method == "error":
                    if params.get("willRetry") is not True:
                        raise _map_turn_error(params.get("error"), model_name)
                elif method == "turn/completed":
                    turn = params.get("turn")
                    if not isinstance(turn, dict):
                        raise CodexSubscriptionError(
                            "CODEX_PROTOCOL_INCOMPATIBLE",
                            "Codex emitted an invalid completion event",
                        )
                    if turn.get("id") != bridge.turn_id:
                        continue
                    status = turn.get("status")
                    if status == "failed":
                        raise _map_turn_error(turn.get("error"), model_name)
                    completed = True
                    logger.info(
                        "Codex turn completed generation=%s model=%s "
                        "thread=%s turn=%s status=%s elapsed=%.3f",
                        self.runtime.generation_id,
                        model_name,
                        _short_id(bridge.thread_id),
                        _short_id(bridge.turn_id or ""),
                        status,
                        time.monotonic() - started_at,
                    )
                    yield ChatResponse(
                        content=[],
                        is_last=True,
                        finished_reason=(
                            FinishedReason.INTERRUPTED
                            if status == "interrupted"
                            else FinishedReason.COMPLETED
                        ),
                        metadata={
                            "codex_thread_id": bridge.thread_id,
                            "codex_turn_id": bridge.turn_id or "",
                        },
                    )
                    return
        finally:
            if not keep_active:
                current = asyncio.current_task()
                cancelling = bool(current and current.cancelling())
                task = self._track_cleanup(
                    bridge,
                    interrupt=not completed,
                )
                if self._active_turn is bridge:
                    self._active_turn = None
                if not cancelling:
                    await asyncio.shield(task)

    def _track_cleanup(
        self,
        bridge: TurnBridge,
        *,
        interrupt: bool,
        retry: bool = False,
    ) -> asyncio.Task[None]:
        task = bridge.start_cleanup(interrupt=interrupt, retry=retry)
        self._cleanup_bridge = bridge
        self._cleanup_barrier = task
        return task

    async def _await_cleanup_barrier(self) -> None:
        task = self._cleanup_barrier
        if task is not None:
            # A successful runtime restart is an explicit recovery boundary.
            if (
                task.done()
                and self.runtime.turn_cleanup_error is None
                and self._cleanup_bridge is not None
                and self._cleanup_bridge.cleanup_state is CleanupState.FAILED
            ):
                self._cleanup_barrier = None
                self._cleanup_bridge = None
            else:
                await asyncio.shield(task)
                if self._cleanup_barrier is task:
                    self._cleanup_barrier = None
                    self._cleanup_bridge = None
        self.runtime.assert_turn_start_allowed()

    async def retry_cleanup(self) -> None:
        """Explicitly retry a failed cleanup before accepting a new turn."""

        bridge = self._cleanup_bridge
        if bridge is None:
            self.runtime.assert_turn_start_allowed()
            return
        task = self._track_cleanup(
            bridge,
            interrupt=True,
            retry=True,
        )
        await asyncio.shield(task)
        if self._cleanup_barrier is task:
            self._cleanup_barrier = None
            self._cleanup_bridge = None


def _extract_tool_results(messages: list[Msg]) -> list[ToolResultBlock]:
    results: list[ToolResultBlock] = []
    for message in messages:
        for block in message.get_content_blocks("tool_result"):
            if isinstance(block, ToolResultBlock):
                results.append(block)
    if not results:
        raise CodexSubscriptionError(
            "CODEX_PROTOCOL_INCOMPATIBLE",
            "The active Codex turn is waiting for a ToolResult",
        )
    return results


def _apply_tool_choice(
    tools: list[dict[str, Any]],
    tool_choice: Any | None,
) -> tuple[list[dict[str, Any]], str]:
    if tool_choice is None:
        return tools, ""
    mode = getattr(tool_choice, "mode", None)
    allowed = getattr(tool_choice, "tools", None)
    if mode == "none":
        return [], ""
    if isinstance(allowed, list):
        allowed_names = {str(name) for name in allowed}
        tools = [tool for tool in tools if tool["name"] in allowed_names]
    if mode == "required":
        if not tools:
            raise CodexSubscriptionError(
                "CODEX_TOOL_BRIDGE_UNSUPPORTED",
                "Tool choice requires a tool, but none is available",
            )
        return (
            tools,
            " You must call at least one QwenPaw dynamic tool before "
            "responding.",
        )
    if isinstance(mode, str) and mode not in {"auto", "none", "required"}:
        selected = [tool for tool in tools if tool["name"] == mode]
        if not selected:
            raise CodexSubscriptionError(
                "CODEX_TOOL_BRIDGE_UNSUPPORTED",
                f"Requested QwenPaw tool '{mode}' is not available",
            )
        return (
            selected,
            f" You must call the QwenPaw dynamic tool '{mode}' before "
            "responding.",
        )
    return tools, ""


def _short_id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def _map_turn_error(value: object, model_name: str) -> CodexSubscriptionError:
    message = "Codex turn failed"
    if isinstance(value, dict) and isinstance(value.get("message"), str):
        message = value["message"]
    normalized = message.lower()
    if any(
        term in normalized for term in ("rate limit", "quota", "usage limit")
    ):
        code = "CODEX_USAGE_LIMIT_EXCEEDED"
    elif any(
        term in normalized
        for term in ("unauthorized", "authentication", "login")
    ):
        code = "CODEX_NOT_LOGGED_IN"
    elif "context" in normalized and any(
        term in normalized for term in ("length", "window", "large")
    ):
        code = "CODEX_CONTEXT_WINDOW_EXCEEDED"
    elif "model" in normalized and any(
        term in normalized for term in ("unavailable", "not found", "removed")
    ):
        code = "CODEX_MODEL_UNAVAILABLE"
    else:
        code = "CODEX_TURN_FAILED"
    return CodexSubscriptionError(
        code,
        message,
        details={"model": model_name},
    )
