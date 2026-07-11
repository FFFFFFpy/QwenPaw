# -*- coding: utf-8 -*-
"""Fail-closed Codex built-in tool isolation configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CodexToolIsolationProfile:
    """Built-in Codex capabilities that QwenPaw never exposes to the model."""

    disable_shell: bool = True
    disable_web_search: bool = True
    disable_mcp: bool = True
    disable_apps: bool = True
    disable_plugins: bool = True
    disable_browser_use: bool = True
    disable_computer_use: bool = True
    disable_image_generation: bool = True
    disable_subagents: bool = True


def build_tool_isolation_config(
    profile: CodexToolIsolationProfile | None = None,
) -> dict[str, Any]:
    """Build thread-local overrides using keys from Codex's config schema.

    This is the primary permission boundary. Approval rejection and streamed
    side-effect detection remain protocol-violation circuit breakers.
    """

    selected = profile or CodexToolIsolationProfile()
    features: dict[str, bool] = {}
    if selected.disable_shell:
        features.update(
            {
                "shell_tool": False,
                "shell_zsh_fork": False,
                "unified_exec": False,
                "unified_exec_zsh_fork": False,
                "code_mode": False,
                "code_mode_only": False,
                "request_permissions_tool": False,
                "default_mode_request_user_input": False,
            },
        )
    if selected.disable_web_search:
        features.update(
            {
                "web_search_request": False,
                "web_search_cached": False,
                "standalone_web_search": False,
            },
        )
    if selected.disable_apps:
        features["apps"] = False
    if selected.disable_mcp:
        features["enable_mcp_apps"] = False
    if selected.disable_plugins:
        features["plugins"] = False
    if selected.disable_browser_use:
        features.update(
            {
                "in_app_browser": False,
                "browser_use": False,
                "browser_use_external": False,
                "browser_use_full_cdp_access": False,
            },
        )
    if selected.disable_computer_use:
        features["computer_use"] = False
    if selected.disable_image_generation:
        features["image_generation"] = False
    if selected.disable_subagents:
        features.update(
            {
                "multi_agent": False,
                "collab": False,
                "multi_agent_v2": False,
                "enable_fanout": False,
            },
        )

    config: dict[str, Any] = {
        "features": features,
        "tools": {"experimental_request_user_input": {"enabled": False}},
    }
    if selected.disable_web_search:
        config["web_search"] = "disabled"
    if selected.disable_mcp:
        config["orchestrator"] = {"mcp": {"enabled": False}}
    return config
