# -*- coding: utf-8 -*-
# pylint: disable=protected-access
from types import SimpleNamespace

import pytest

from qwenpaw.app.workspace.local_workspace import QwenPawLocalWorkspace


class _CapturingRegistry:
    def __init__(self) -> None:
        self.filters: list[dict] = []

    def default_enabled_names(self) -> set[str]:
        return {"image_generate"}

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return []


def _workspace(registry: _CapturingRegistry) -> QwenPawLocalWorkspace:
    workspace = object.__new__(QwenPawLocalWorkspace)
    workspace._tool_registry = registry
    workspace._governor = None
    return workspace


def _agent_config(provider_id: str):
    return SimpleNamespace(
        active_model=SimpleNamespace(provider_id=provider_id),
        tools=None,
    )


@pytest.mark.asyncio
async def test_image_generate_hidden_for_non_codex_active_provider():
    registry = _CapturingRegistry()

    await _workspace(registry).list_tools(agent_config=_agent_config("openai"))

    assert "image_generate" in registry.filters[0]["denied"]


@pytest.mark.asyncio
async def test_image_generate_available_for_codex_active_provider():
    registry = _CapturingRegistry()

    await _workspace(registry).list_tools(
        agent_config=_agent_config("openai-codex"),
    )

    assert "image_generate" not in registry.filters[0]["denied"]
