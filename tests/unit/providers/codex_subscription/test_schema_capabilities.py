from __future__ import annotations

import pytest

from qwenpaw.providers.codex_subscription.errors import (
    CodexSubscriptionError,
)
from qwenpaw.providers.codex_subscription.schema_capabilities import (
    CodexCapabilities,
)


def test_capabilities_combine_client_and_server_schema_surfaces():
    client_schema = " ".join(
        (
            "account/login/start",
            "chatgpt",
            "chatgptDeviceCode",
            "model/list",
            "account/rateLimits/read",
            "turn/interrupt",
            "thread/unsubscribe",
            "dynamicTools",
            "inputModalities",
            '"image"',
            "approvalPolicy",
            "read-only",
        )
    )
    server_schema = "item/tool/call"
    capabilities = CodexCapabilities.from_schema_text(
        client_schema + server_schema
    )
    assert capabilities.dynamic_tools is True
    assert capabilities.device_code_login is True
    capabilities.validate_required_surface()


def test_missing_required_surface_is_incompatible():
    capabilities = CodexCapabilities.from_schema_text("model/list")
    with pytest.raises(CodexSubscriptionError) as caught:
        capabilities.validate_required_surface()
    assert caught.value.error_code == "CODEX_PROTOCOL_INCOMPATIBLE"
