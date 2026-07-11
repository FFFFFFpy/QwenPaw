import time

import pytest
from agentscope.message import SystemMsg, TextBlock, UserMsg

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
            'data: {"type":"response.output_text.delta","delta":"PONG"}',
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
    assert chunks[0].content[0].text == "PONG"
    assert chunks[-1].is_last
    assert chunks[-1].usage.input_tokens == 2
    assert http.calls == 1


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
    assert chunks[0].content[0].text == "PONG"
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
