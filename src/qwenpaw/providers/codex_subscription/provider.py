"""Built-in ChatGPT/Codex subscription provider using direct Responses SSE."""

from __future__ import annotations

from typing import Any

from agentscope.model import ChatModelBase
from pydantic import PrivateAttr

from qwenpaw.constant import SECRET_DIR
from qwenpaw.providers.provider import ModelInfo, Provider, ProviderInfo

from .catalog import subscription_models
from .errors import CodexSubscriptionError
from .oauth import OAuthService
from .settings import CodexSubscriptionSettings, direct_transport_enabled
from .token_store import TokenStore


class ChatGPTSubscriptionProvider(Provider):
    _token_store: TokenStore = PrivateAttr()
    _oauth: OAuthService = PrivateAttr()
    _settings: CodexSubscriptionSettings = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        del __context
        self.api_key = ""
        self.base_url = "https://chatgpt.com/backend-api/codex"
        self._token_store = TokenStore()
        self._oauth = OAuthService(self._token_store)
        self._settings = CodexSubscriptionSettings.load(
            SECRET_DIR / "codex_subscription" / "settings.json"
        )
        saved = {model.id: model for model in self.models}
        self.models = subscription_models()
        for model in self.models:
            previous = saved.get(model.id)
            if previous is not None:
                model.reasoning_effort = previous.reasoning_effort
                model.relay_reasoning = previous.relay_reasoning
        self.extra_models = []

    @property
    def token_store(self) -> TokenStore:
        return self._token_store

    @property
    def auth_service(self) -> OAuthService:
        return self._oauth

    @property
    def settings(self) -> CodexSubscriptionSettings:
        return self._settings

    async def check_connection(self, timeout: float = 5) -> tuple[bool, str]:
        del timeout
        if not direct_transport_enabled():
            return False, "ChatGPT subscription compatibility access is paused"
        status = self._token_store.status()
        return (
            bool(status["connected"]),
            "" if status["connected"] else "Sign in to ChatGPT",
        )

    async def fetch_models(self, timeout: float = 5) -> list[ModelInfo]:
        del timeout
        if not self._token_store.status()["connected"]:
            raise CodexSubscriptionError(
                "CODEX_NOT_LOGGED_IN",
                "Sign in to ChatGPT before refreshing models",
            )
        return [model.model_copy(deep=True) for model in self.models]

    async def check_model_connection(
        self, model_id: str, timeout: float = 5
    ) -> tuple[bool, str]:
        connected, message = await self.check_connection(timeout)
        if not connected:
            return connected, message
        if model_id not in {model.id for model in self.models}:
            return False, f"Subscription model '{model_id}' is not available"
        return True, ""

    def update_config(self, config: dict) -> None:
        safe = dict(config)
        for key in (
            "api_key",
            "base_url",
            "binary_path",
            "runtime_mode",
            "app_server_schema",
            "tool_isolation",
            "thread_options",
        ):
            safe.pop(key, None)
        super().update_config(safe)
        self.api_key = ""

    async def get_info(self, mock_secret: bool = True) -> ProviderInfo:
        del mock_secret
        data = self.model_dump()
        data.update(
            {
                "api_key": "",
                "oauth_connected": bool(
                    self._token_store.status()["connected"]
                ),
            }
        )
        data["meta"] = {
            **self.meta,
            "supports_oauth": True,
            "provider_kind": "cloud_subscription",
            "auth_kind": "chatgpt_subscription",
            "runtime_kind": "qwenpaw_native",
            "transport": "direct",
            "compatibility_route": True,
            "account": self._token_store.status(),
            "model_source": "subscription_catalog",
            "context_size": 262144,
            "compact_threshold": 0.90,
            "compact_trigger": 235930,
            "max_output_tokens": 131072,
            "direct_enabled": direct_transport_enabled(),
        }
        return ProviderInfo.model_validate(data)

    def get_chat_model_instance(self, model_id: str) -> ChatModelBase:
        from .chat_model import ChatGPTSubscriptionChatModel
        from .credential import CodexSubscriptionCredential

        if not direct_transport_enabled():
            raise CodexSubscriptionError(
                "CODEX_COMPATIBILITY_PAUSED",
                "ChatGPT subscription compatibility access is paused",
            )
        model = self.get_model_info(model_id)
        if model is None:
            raise CodexSubscriptionError(
                "CODEX_MODEL_UNAVAILABLE",
                f"Subscription model '{model_id}' is not available",
            )
        effort = model.reasoning_effort or self._settings.reasoning_effort
        if effort and effort not in (model.reasoning_effort_options or []):
            raise CodexSubscriptionError(
                "CODEX_REASONING_CONFIRMATION_REQUIRED",
                "The saved reasoning effort is no longer supported; "
                "confirm a new value",
            )
        return ChatGPTSubscriptionChatModel(
            credential=CodexSubscriptionCredential(
                id="qwenpaw-openai-codex", name="ChatGPT subscription"
            ),
            model=model_id,
            parameters=ChatGPTSubscriptionChatModel.Parameters(
                reasoning_effort=effort
            ),
            token_store=self._token_store,
            oauth_service=self._oauth,
            relay_reasoning=model.relay_reasoning
            and self._settings.relay_reasoning,
            stream=True,
            context_size=262_144,
        )


CodexSubscriptionProvider = ChatGPTSubscriptionProvider

PROVIDER_OPENAI_CODEX = ChatGPTSubscriptionProvider(
    id="openai-codex",
    name="OpenAI ChatGPT/Codex 订阅",
    base_url="https://chatgpt.com/backend-api/codex",
    api_key="",
    chat_model="ChatGPTSubscriptionChatModel",
    require_api_key=False,
    freeze_url=True,
    support_model_discovery=True,
    support_connection_check=True,
    supports_oauth=True,
    provider_group="openai",
    provider_group_name="OpenAI",
    provider_variant="codex_subscription",
    thinking_param_style="effort",
    reasoning_effort_options=[
        "auto",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ],
    models=subscription_models(),
    meta={
        "supports_oauth": True,
        "provider_kind": "cloud_subscription",
        "auth_kind": "chatgpt_subscription",
        "runtime_kind": "qwenpaw_native",
        "transport": "direct",
        "compatibility_route": True,
    },
)
