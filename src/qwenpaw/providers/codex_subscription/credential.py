"""Marker credential; OAuth secrets remain in the encrypted TokenStore."""

from __future__ import annotations

from typing import Type

from agentscope.credential import CredentialBase
from agentscope.model import ChatModelBase


class CodexSubscriptionCredential(CredentialBase):
    @classmethod
    def get_chat_model_class(cls) -> Type[ChatModelBase]:
        from .chat_model import ChatGPTSubscriptionChatModel

        return ChatGPTSubscriptionChatModel
