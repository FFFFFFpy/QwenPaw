"""A marker credential: authentication remains owned by App Server."""

from __future__ import annotations

from typing import Type

from agentscope.credential import CredentialBase
from agentscope.model import ChatModelBase


class CodexSubscriptionCredential(CredentialBase):
    """Contains no token, API key, cookie, or authorization header."""

    @classmethod
    def get_chat_model_class(cls) -> Type[ChatModelBase]:
        from .chat_model import CodexSubscriptionChatModel

        return CodexSubscriptionChatModel
