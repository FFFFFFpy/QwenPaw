# -*- coding: utf-8 -*-
# pylint: disable=protected-access
from agentscope.formatter import OpenAIResponseFormatter
import pytest

from qwenpaw.agents.model_factory import _create_formatter_instance
from qwenpaw.providers.codex_subscription.chat_model import (
    ChatGPTSubscriptionChatModel,
)
from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.provider import PROVIDER_OPENAI_CODEX


def test_provider_id_catalog_and_native_formatter():
    assert PROVIDER_OPENAI_CODEX.id == "openai-codex"
    assert PROVIDER_OPENAI_CODEX.meta["runtime_kind"] == "qwenpaw_native"
    model = PROVIDER_OPENAI_CODEX.get_chat_model_instance("gpt-5.6-luna")
    assert isinstance(model, ChatGPTSubscriptionChatModel)
    assert isinstance(model.formatter, OpenAIResponseFormatter)
    assert _create_formatter_instance(model) is not None
    assert model.context_size == 262_144


def test_feature_flag_disables_inference(monkeypatch):
    monkeypatch.setenv("QWENPAW_OPENAI_CODEX_DIRECT_ENABLED", "false")
    with pytest.raises(CodexSubscriptionError) as raised:
        PROVIDER_OPENAI_CODEX.get_chat_model_instance("gpt-5.6-luna")
    assert raised.value.error_code == "CODEX_COMPATIBILITY_PAUSED"


@pytest.mark.asyncio
async def test_generic_provider_info_exposes_only_account_connection_state(
    monkeypatch,
):
    monkeypatch.setattr(
        PROVIDER_OPENAI_CODEX._token_store,
        "status",
        lambda: {
            "connected": True,
            "status": "connected",
            "display_name": "Private Name",
            "email_masked": "p***e@example.test",
            "expires_at": 123456789,
        },
    )

    info = await PROVIDER_OPENAI_CODEX.get_info()

    assert info.meta["account"] == {
        "connected": True,
        "status": "connected",
    }
    assert "display_name" not in info.meta["account"]
    assert "email_masked" not in info.meta["account"]
