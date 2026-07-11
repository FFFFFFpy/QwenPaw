import asyncio
import time

import pytest
from agentscope.message import SystemMsg, TextBlock, UserMsg
from agentscope.model import FinishedReason

from qwenpaw.providers.codex_subscription.chat_model import (
    ChatGPTSubscriptionChatModel,
)
from qwenpaw.providers.codex_subscription.credential import (
    CodexSubscriptionCredential,
)
from qwenpaw.providers.codex_subscription.oauth import OAuthService
from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.token_store import (
    TokenRecord,
    TokenStore,
)


class FakeResponse:
    async def aiter_lines(self):
        for line in [
            'data: {"type":"response.output_text.delta","delta":"PO"}',
            "",
            'data: {"type":"response.output_text.delta","delta":"NG"}',
            "",
            'data: {"type":"response.completed","response":'
            '{"usage":{"input_tokens":2,"output_tokens":1,'
            '"total_tokens":3}}}',
            "",
        ]:
            yield line


class FakeHTTP:
    calls = 0

    async def stream(self, **kwargs):
        self.calls += 1
        yield FakeResponse()


class FakeToolResponse:
    async def aiter_lines(self):
        for line in [
            'data: {"type":"response.output_item.done","item":'
            '{"type":"function_call","call_id":"call-image",'
            '"name":"image_generate","arguments":"{\\"prompt\\":'
            '\\"draw a cat\\"}"}}',
            "",
            'data: {"type":"response.completed","response":'
            '{"usage":{"input_tokens":2,"output_tokens":3,'
            '"total_tokens":5}}}',
            "",
        ]:
            yield line


class FakeToolHTTP:
    async def stream(self, **kwargs):
        yield FakeToolResponse()


@pytest.mark.asyncio
async def test_direct_text_stream_has_no_thread_or_runtime(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="acct",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        )
    )
    http = FakeHTTP()
    model = ChatGPTSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test", name="test"),
        model="gpt-5.6-luna",
        parameters=ChatGPTSubscriptionChatModel.Parameters(
            reasoning_effort="low"
        ),
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=http,
    )
    response = await model(
        [
            SystemMsg(name="system", content=[TextBlock(text="exact")]),
            UserMsg(name="user", content=[TextBlock(text="ping")]),
        ]
    )
    chunks = [chunk async for chunk in response]
    assert chunks[0].content[0].text == "PO"
    assert chunks[1].content[0].text == "NG"
    assert chunks[0].content[0].id == chunks[1].content[0].id
    assert chunks[-1].is_last
    assert len(chunks[-1].content) == 1
    assert chunks[-1].content[0].text == "PONG"
    assert chunks[-1].usage.input_tokens == 2
    assert http.calls == 1


@pytest.mark.asyncio
async def test_terminal_response_retains_accumulated_tool_call(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="acct",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        )
    )
    model = ChatGPTSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test", name="test"),
        model="gpt-5.6-luna",
        parameters=ChatGPTSubscriptionChatModel.Parameters(),
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=FakeToolHTTP(),
    )
    response = await model(
        [UserMsg(name="user", content=[TextBlock(text="draw a cat")])]
    )
    chunks = [chunk async for chunk in response]
    terminal = chunks[-1]
    assert terminal.is_last
    assert terminal.content[0].name == "image_generate"
    assert terminal.content[0].input == '{"prompt":"draw a cat"}'
    assert terminal.usage.output_tokens == 3


class RefreshingOAuth(OAuthService):
    calls = 0

    async def refresh(self, record):
        self.calls += 1
        record.access_token = "refreshed"
        record.expires_at = time.time() + 3600
        return record


class UnauthorizedOnceHTTP(FakeHTTP):
    async def stream(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise CodexSubscriptionError(
                "CODEX_NOT_LOGGED_IN", "expired", status_code=401
            )
        yield FakeResponse()


@pytest.mark.asyncio
async def test_401_refreshes_and_retries_only_once_before_output(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="acct",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        )
    )
    oauth = RefreshingOAuth(store)
    http = UnauthorizedOnceHTTP()
    model = ChatGPTSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test", name="test"),
        model="gpt-5.6-luna",
        parameters=ChatGPTSubscriptionChatModel.Parameters(),
        token_store=store,
        oauth_service=oauth,
        http_client=http,
    )
    response = await model(
        [UserMsg(name="user", content=[TextBlock(text="ping")])]
    )
    chunks = [chunk async for chunk in response]
    assert chunks[0].content[0].text == "PO"
    assert oauth.calls == 1
    assert http.calls == 2


class PartialDisconnectHTTP(FakeHTTP):
    async def stream(self, **kwargs):
        self.calls += 1

        class Partial:
            async def aiter_lines(self):
                for line in [
                    'data: {"type":"response.output_text.delta",'
                    '"delta":"partial"}',
                    "",
                ]:
                    yield line

        yield Partial()


@pytest.mark.asyncio
async def test_partial_stream_is_never_replayed(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="acct",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        )
    )
    http = PartialDisconnectHTTP()
    model = ChatGPTSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test", name="test"),
        model="gpt-5.6-luna",
        parameters=ChatGPTSubscriptionChatModel.Parameters(),
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=http,
    )
    response = await model(
        [UserMsg(name="user", content=[TextBlock(text="ping")])]
    )
    with pytest.raises(CodexSubscriptionError):
        [chunk async for chunk in response]
    assert http.calls == 1


class IncompleteHTTP(FakeHTTP):
    async def stream(self, **kwargs):
        self.calls += 1

        class Incomplete:
            async def aiter_lines(self):
                for line in [
                    'data: {"type":"response.incomplete","response":'
                    '{"incomplete_details":{"reason":"max_output_tokens"}}}',
                    "",
                ]:
                    yield line

        yield Incomplete()


@pytest.mark.asyncio
async def test_incomplete_response_is_an_error_with_safe_details(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="acct",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        )
    )
    model = ChatGPTSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test", name="test"),
        model="gpt-5.6-luna",
        parameters=ChatGPTSubscriptionChatModel.Parameters(),
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=IncompleteHTTP(),
    )
    response = await model(
        [UserMsg(name="user", content=[TextBlock(text="ping")])]
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        [chunk async for chunk in response]
    assert caught.value.error_code == "CODEX_RESPONSE_INCOMPLETE"
    assert caught.value.details["reason"] == "max_output_tokens"


@pytest.mark.asyncio
async def test_availability_changes_only_for_success_and_permission(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="acct",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        )
    )
    updates = []
    model = ChatGPTSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test", name="test"),
        model="gpt-5.6-luna",
        parameters=ChatGPTSubscriptionChatModel.Parameters(),
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=FakeHTTP(),
        availability_callback=lambda model_id, value: updates.append(
            (model_id, value)
        ),
    )
    response = await model(
        [UserMsg(name="user", content=[TextBlock(text="ping")])]
    )
    [chunk async for chunk in response]
    assert updates == [("gpt-5.6-luna", "available")]

    class ForbiddenHTTP:
        async def stream(self, **kwargs):
            raise CodexSubscriptionError(
                "CODEX_MODEL_UNAVAILABLE", "forbidden", status_code=403
            )
            yield  # pragma: no cover

    updates.clear()
    model.http_client = ForbiddenHTTP()
    response = await model(
        [UserMsg(name="user", content=[TextBlock(text="ping")])]
    )
    with pytest.raises(CodexSubscriptionError):
        [chunk async for chunk in response]
    assert updates == [("gpt-5.6-luna", "unavailable")]


@pytest.mark.asyncio
async def test_cancellation_finishes_as_interrupted(tmp_path):
    store = TokenStore(tmp_path / "oauth.enc")
    store.save(
        TokenRecord(
            account_local_id="default",
            access_token="access",
            refresh_token="refresh",
            account_id="acct",
            expires_at=time.time() + 3600,
            last_refresh_at=time.time(),
        )
    )

    class BlockingHTTP:
        async def stream(self, **kwargs):
            class BlockingResponse:
                async def aiter_lines(self):
                    yield (
                        'data: {"type":"response.output_text.delta",'
                        '"delta":"partial"}'
                    )
                    yield ""
                    await asyncio.Event().wait()
                    yield ""  # pragma: no cover

            yield BlockingResponse()

    model = ChatGPTSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="test", name="test"),
        model="gpt-5.6-luna",
        parameters=ChatGPTSubscriptionChatModel.Parameters(),
        token_store=store,
        oauth_service=OAuthService(store),
        http_client=BlockingHTTP(),
    )
    response = await model(
        [UserMsg(name="user", content=[TextBlock(text="long response")])]
    )
    chunks = []

    async def consume():
        async for chunk in response:
            chunks.append(chunk)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    await task
    assert chunks[-1].is_last
    assert chunks[-1].content[0].text == "partial"
    assert chunks[-1].finished_reason == FinishedReason.INTERRUPTED
