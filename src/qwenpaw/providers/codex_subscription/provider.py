"""Built-in OpenAI Codex cloud-subscription provider."""

from __future__ import annotations

from typing import Any

from agentscope.model import ChatModelBase
from pydantic import PrivateAttr

from qwenpaw.providers.provider import ModelInfo, Provider, ProviderInfo

from .auth_service import AuthService
from .errors import CodexSubscriptionError
from .model_catalog import ModelCatalog
from .rate_limits import RateLimitService
from .runtime import CodexAppServerRuntime, RuntimeState, get_codex_runtime


class CodexSubscriptionProvider(Provider):
    _runtime: CodexAppServerRuntime = PrivateAttr()
    _auth: AuthService = PrivateAttr()
    _catalog: ModelCatalog = PrivateAttr()
    _rate_limits: RateLimitService = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        del __context
        # Never preserve a value injected through legacy/provider storage.
        self.api_key = ""
        self.base_url = "codex-app-server://local"
        self.set_runtime(get_codex_runtime())

    def set_runtime(self, runtime: CodexAppServerRuntime) -> None:
        """Replace runtime and dependent services (primarily for tests)."""

        self._runtime = runtime
        self._auth = AuthService(runtime)
        self._catalog = ModelCatalog(runtime)
        self._rate_limits = RateLimitService(runtime)

    @property
    def runtime(self) -> CodexAppServerRuntime:
        return self._runtime

    @property
    def auth_service(self) -> AuthService:
        return self._auth

    @property
    def rate_limit_service(self) -> RateLimitService:
        return self._rate_limits

    @property
    def model_catalog_stale(self) -> bool:
        return self._catalog.stale

    async def check_connection(self, timeout: float = 5) -> tuple[bool, str]:
        del timeout
        try:
            account = await self._auth.read_account()
            if not account.connected:
                return False, "ChatGPT is not connected"
            return True, ""
        except CodexSubscriptionError as exc:
            return False, str(exc)

    async def fetch_models(self, timeout: float = 5) -> list[ModelInfo]:
        del timeout
        account = await self._auth.read_account()
        if not account.connected:
            raise CodexSubscriptionError(
                "CODEX_NOT_LOGGED_IN",
                "Connect ChatGPT before refreshing Codex models",
            )
        models = await self._catalog.fetch()
        self.extra_models = [model.model_copy(deep=True) for model in models]
        return models

    async def check_model_connection(
        self,
        model_id: str,
        timeout: float = 5,
    ) -> tuple[bool, str]:
        del timeout
        try:
            account = await self._auth.read_account()
            if not account.connected:
                return False, "ChatGPT is not connected"
            models = await self._catalog.fetch()
            if model_id not in {model.id for model in models}:
                return False, f"Codex model '{model_id}' is not available"
            return True, ""
        except CodexSubscriptionError as exc:
            return False, str(exc)

    def update_config(self, config: dict) -> None:
        safe_config = dict(config)
        safe_config.pop("api_key", None)
        safe_config.pop("base_url", None)
        super().update_config(safe_config)
        self.api_key = ""

    async def get_info(self, mock_secret: bool = True) -> ProviderInfo:
        del mock_secret
        account = None
        if self._runtime.state is RuntimeState.READY:
            try:
                account = await self._auth.read_account()
            except Exception:
                pass
        data = self.model_dump()
        data["api_key"] = ""
        data["oauth_connected"] = bool(account and account.connected)
        data["meta"] = {
            "supports_oauth": True,
            "provider_kind": "cloud_subscription",
            "auth_kind": "chatgpt_subscription",
            "runtime_kind": "codex_app_server",
            **self.meta,
            "runtime_state": self._runtime.state.value,
            "binary_path": self._runtime.binary_path,
            "account_email_masked": (
                account.email_masked if account is not None else None
            ),
            "plan_type": account.plan_type if account is not None else None,
            "model_catalog_stale": self._catalog.stale,
        }
        return ProviderInfo.model_validate(data)

    def get_chat_model_instance(self, model_id: str) -> ChatModelBase:
        from .chat_model import CodexSubscriptionChatModel
        from .credential import CodexSubscriptionCredential

        model = self.get_model_info(model_id)
        if model is None:
            raise CodexSubscriptionError(
                "CODEX_MODEL_UNAVAILABLE",
                f"Codex model '{model_id}' is not available",
            )
        return CodexSubscriptionChatModel(
            credential=CodexSubscriptionCredential(
                id="qwenpaw-openai-codex",
                name="ChatGPT subscription managed by Codex",
            ),
            model=model_id,
            parameters=CodexSubscriptionChatModel.Parameters(),
            stream=True,
            context_size=self.get_context_size(model_id),
            runtime=self._runtime,
            auth_service=self._auth,
            relay_reasoning=model.relay_reasoning,
        )


PROVIDER_OPENAI_CODEX = CodexSubscriptionProvider(
    id="openai-codex",
    name="OpenAI Codex",
    base_url="codex-app-server://local",
    api_key="",
    chat_model="CodexSubscriptionChatModel",
    require_api_key=False,
    freeze_url=True,
    support_model_discovery=True,
    support_connection_check=True,
    supports_oauth=True,
    provider_group="openai",
    provider_group_name="OpenAI",
    provider_variant="codex_subscription",
    thinking_param_style="effort",
    meta={
        "supports_oauth": True,
        "provider_kind": "cloud_subscription",
        "auth_kind": "chatgpt_subscription",
        "runtime_kind": "codex_app_server",
    },
)
