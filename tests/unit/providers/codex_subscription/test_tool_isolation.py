# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.settings import (
    CodexSubscriptionSettings,
)
from qwenpaw.providers.codex_subscription.tool_isolation import (
    CodexToolIsolationProfile,
    build_tool_isolation_config,
)


def test_default_profile_disables_every_builtin_side_effect_surface():
    profile = CodexToolIsolationProfile()
    assert all(profile.__dict__.values())

    config = build_tool_isolation_config(
        profile,
        mcp_server_names=("zeta", "alpha", "alpha"),
    )

    assert config["web_search"] == "disabled"
    assert config["orchestrator"] == {"mcp": {"enabled": False}}
    assert config["mcp_servers"] == {
        "alpha": {"enabled": False},
        "zeta": {"enabled": False},
    }
    assert config["features"] == {
        "shell_tool": False,
        "shell_zsh_fork": False,
        "unified_exec": False,
        "unified_exec_zsh_fork": False,
        "code_mode": False,
        "code_mode_only": False,
        "request_permissions_tool": False,
        "default_mode_request_user_input": False,
        "web_search_request": False,
        "web_search_cached": False,
        "standalone_web_search": False,
        "apps": False,
        "enable_mcp_apps": False,
        "plugins": False,
        "in_app_browser": False,
        "browser_use": False,
        "browser_use_external": False,
        "browser_use_full_cdp_access": False,
        "computer_use": False,
        "image_generation": False,
        "multi_agent": False,
        "collab": False,
        "multi_agent_v2": False,
        "enable_fanout": False,
    }
    assert config["tools"] == {
        "experimental_request_user_input": {"enabled": False},
    }


def test_only_probe_recorder_persists_valid_schema_fingerprint(tmp_path):
    path = tmp_path / "settings.json"
    settings = CodexSubscriptionSettings(binary_path="/opt/codex")
    fingerprint = "a" * 64

    settings.record_tool_isolation_verification(fingerprint, path)
    settings.record_tool_isolation_verification(fingerprint, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["binary_path"] == "/opt/codex"
    assert payload["tool_isolation_verified_fingerprints"] == [fingerprint]

    with pytest.raises(CodexSubscriptionError):
        settings.record_tool_isolation_verification("not-a-hash", path)
