"""QwenPaw-native ChatModel using the direct Codex Responses SSE route."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import time
from typing import Any

from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIResponseFormatter
from agentscope.message import Msg, TextBlock, ThinkingBlock, ToolCallBlock
from agentscope.model import (
    ChatModelBase,
    ChatResponse,
    ChatUsage,
    FinishedReason,
)
from pydantic import BaseModel

from .errors import CodexSubscriptionError
from .http_client import CodexResponsesHTTPClient
from .oauth import OAuthService
from .responses_mapper import ResponsesMapper
from .stream_parser import ResponsesStreamParser, iter_sse_events
from .token_store import TokenStore


class ChatGPTSubscriptionChatModel(ChatModelBase):
    """Provider transport only. Tool execution remains in QwenPaw ReAct."""

    class Parameters(BaseModel):
        reasoning_effort: str | None = None

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        parameters: BaseModel,
        *,
        token_store: TokenStore | None = None,
        oauth_service: OAuthService | None = None,
        http_client: CodexResponsesHTTPClient | None = None,
        mapper: ResponsesMapper | None = None,
        relay_reasoning: bool = True,
        stream: bool = True,
        max_retries: int = 0,
        retry_delay: float = 1.0,
        context_size: int = 262_144,
    ) -> None:
        self.token_store = token_store or TokenStore()
        self.oauth_service = oauth_service or OAuthService(self.token_store)
        self.http_client = http_client or CodexResponsesHTTPClient()
        self.mapper = mapper or ResponsesMapper()
        self.relay_reasoning = relay_reasoning
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
        )
        self.formatter = OpenAIResponseFormatter()

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        effort = generate_kwargs.pop("reasoning_effort", None)
        if effort is None:
            effort = getattr(self.parameters, "reasoning_effort", None)
        structured = generate_kwargs.pop(
            "response_format", None
        ) or generate_kwargs.pop("structured_format", None)
        body = self.mapper.build_request(
            model=model_name,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            reasoning_effort=effort,
            relay_reasoning=self.relay_reasoning,
            structured_format=structured,
            parallel_tool_calls=bool(
                generate_kwargs.pop("parallel_tool_calls", True)
            ),
        )
        stream = self._stream_response(body)
        if self.stream:
            return stream
        collected = ChatResponse(content=[], is_last=True)
        async for chunk in stream:
            if chunk.is_last:
                collected.finished_reason = chunk.finished_reason
                collected.usage = chunk.usage
            else:
                collected.append_chat_response(chunk)
        return collected

    async def _stream_response(
        self, body: dict[str, Any]
    ) -> AsyncGenerator[ChatResponse, None]:
        started = time.monotonic()
        refreshed_after_401 = False
        while True:
            record = await self.token_store.get_valid(
                self.oauth_service.refresh
            )
            emitted = False
            usage: dict[str, int] | None = None
            parser = ResponsesStreamParser()
            completed = False
            try:
                async for response in self.http_client.stream(
                    body=body,
                    access_token=record.access_token.get_secret_value(),
                    account_id=record.account_id.get_secret_value(),
                ):
                    async for event in iter_sse_events(response.aiter_lines()):
                        for part in parser.feed(event):
                            if part.kind == "usage":
                                usage = part.usage
                            elif part.kind == "completed":
                                completed = True
                            elif part.kind == "text" and part.text:
                                emitted = True
                                yield ChatResponse(
                                    content=[TextBlock(text=part.text)],
                                    is_last=False,
                                )
                            elif (
                                part.kind == "reasoning"
                                and part.text
                                and self.relay_reasoning
                            ):
                                emitted = True
                                yield ChatResponse(
                                    content=[
                                        ThinkingBlock(thinking=part.text)
                                    ],
                                    is_last=False,
                                )
                            elif part.kind == "tool":
                                emitted = True
                                yield ChatResponse(
                                    content=[
                                        ToolCallBlock(
                                            id=part.call_id,
                                            name=part.name,
                                            input=part.arguments,
                                        )
                                    ],
                                    is_last=False,
                                )
                if not completed:
                    raise CodexSubscriptionError(
                        "CODEX_STREAM_DISCONNECTED",
                        "ChatGPT disconnected before completing the response",
                    )
                chat_usage = None
                if usage is not None:
                    chat_usage = ChatUsage(
                        input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"],
                        cache_input_tokens=usage["cached_tokens"],
                        time=time.monotonic() - started,
                        metadata={"total_tokens": usage["total_tokens"]},
                    )
                yield ChatResponse(
                    content=[],
                    is_last=True,
                    usage=chat_usage,
                    finished_reason=FinishedReason.COMPLETED,
                )
                return
            except CodexSubscriptionError as exc:
                if (
                    exc.status_code == 401
                    and not refreshed_after_401
                    and not emitted
                ):
                    await self.token_store.get_valid(
                        self.oauth_service.refresh, force_refresh=True
                    )
                    refreshed_after_401 = True
                    continue
                raise


# Compatibility aliases for saved class names and imports.
CodexSubscriptionChatModel = ChatGPTSubscriptionChatModel
