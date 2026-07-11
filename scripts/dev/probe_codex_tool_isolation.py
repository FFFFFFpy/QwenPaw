#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pylint: disable=wrong-import-position
"""Probe schema-only and explicitly authorized Codex tool isolation.

The default mode never calls account/read. Passing --account-backed is an
explicit release operation that may make the official App Server use its own
credential store.
"""

from __future__ import annotations

import argparse
import atexit
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

# The probe must not read QwenPaw's own keychain or persisted secrets. This is
# established before importing any qwenpaw provider module. Account-backed
# mode may still ask the official Codex process to use its own credential
# store, so that mode remains an explicit human-authorized release operation.
_PROBE_SECRET_ROOT = tempfile.mkdtemp(prefix="qwenpaw-codex-probe-secrets-")
os.environ["QWENPAW_DISABLE_KEYRING"] = "1"
os.environ["QWENPAW_SECRET_DIR"] = _PROBE_SECRET_ROOT
atexit.register(shutil.rmtree, _PROBE_SECRET_ROOT, ignore_errors=True)

from qwenpaw.providers.codex_subscription.runtime import (  # noqa: E402
    CodexAppServerRuntime,
)
from qwenpaw.providers.codex_subscription.schema_capabilities import (  # noqa: E402
    build_restricted_sandbox_policy,
)
from qwenpaw.providers.codex_subscription.settings import (  # noqa: E402
    CodexSubscriptionSettings,
)
from qwenpaw.providers.codex_subscription.tool_isolation import (  # noqa: E402
    build_tool_isolation_config,
)

_BLOCKED_ITEMS = {
    "commandExecution",
    "fileChange",
    "webSearch",
    "mcpToolCall",
    "collabAgentToolCall",
    "browserUse",
    "computerUse",
    "imageView",
    "imageGeneration",
}


async def _run_turn(
    runtime: CodexAppServerRuntime,
    thread_id: str,
    text: str,
    item_types: set[str],
    timeout: float,
) -> None:
    completed = asyncio.Event()
    turn_id: str | None = None

    def on_item(params: dict[str, Any]) -> None:
        item = params.get("item")
        item_type = item.get("type") if isinstance(item, dict) else None
        if isinstance(item_type, str):
            item_types.add(item_type)

    def on_completed(params: dict[str, Any]) -> None:
        turn = params.get("turn")
        if isinstance(turn, dict) and turn.get("id") == turn_id:
            completed.set()

    unsubscribe_item = runtime.subscribe(
        "item/started", on_item, thread_id=thread_id
    )
    unsubscribe_completed = runtime.subscribe(
        "turn/completed", on_completed, thread_id=thread_id
    )
    try:
        response = await runtime.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text}],
                "sandboxPolicy": build_restricted_sandbox_policy(
                    runtime.capabilities
                ),
            },
            timeout=timeout,
        )
        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str):
            raise RuntimeError("turn/start returned no turn id")
        await asyncio.wait_for(completed.wait(), timeout)
    finally:
        unsubscribe_item()
        unsubscribe_completed()


async def _run(args: argparse.Namespace) -> int:
    settings_path = (
        Path(args.record_settings).expanduser().resolve()
        if args.record_settings
        else None
    )
    settings = (
        CodexSubscriptionSettings.load(settings_path)
        if settings_path is not None
        else CodexSubscriptionSettings()
    )
    if args.binary:
        settings.binary_path = args.binary
    runtime = CodexAppServerRuntime(settings)
    dynamic_calls = 0
    try:
        await runtime.start()
        capabilities = runtime.capabilities
        if capabilities is None:
            raise RuntimeError("capability detection returned no result")
        result: dict[str, Any] = {
            "schemaFingerprint": capabilities.schema_fingerprint,
            "threadSandboxMode": capabilities.thread_sandbox_mode,
            "turnSandboxPolicy": capabilities.turn_sandbox_policy,
            "sandboxNetworkDisable": capabilities.sandbox_network_disable,
            "configOverrides": capabilities.config_overrides,
            "dynamicTools": capabilities.dynamic_tools,
            "accountBacked": args.account_backed,
            "toolIsolationVerified": False,
        }
        if not args.account_backed:
            print(json.dumps(result, sort_keys=True))
            return 0

        account = await runtime.request(
            "account/read", {"refreshToken": False}, timeout=args.timeout
        )
        if not isinstance(account.get("account"), dict):
            raise RuntimeError(
                "account-backed probe requires a connected account"
            )
        models = await runtime.request(
            "model/list",
            {"limit": 100, "includeHidden": False},
            timeout=args.timeout,
        )
        rows = models.get("data")
        rows = rows if isinstance(rows, list) else []
        model = args.model or next(
            (
                row.get("id") or row.get("model")
                for row in rows
                if isinstance(row, dict)
            ),
            None,
        )
        if not isinstance(model, str) or not model:
            raise RuntimeError("model/list returned no probe model")

        async def dynamic_tool(_params: dict[str, Any]) -> dict[str, Any]:
            nonlocal dynamic_calls
            dynamic_calls += 1
            return {
                "contentItems": [
                    {"type": "inputText", "text": "isolation probe ok"}
                ],
                "success": True,
            }

        runtime.register_server_request("item/tool/call", dynamic_tool)
        mcp_server_names = await runtime.list_mcp_server_names()
        with tempfile.TemporaryDirectory(
            prefix="qwenpaw-codex-isolation-"
        ) as cwd:
            thread_response = await runtime.request(
                "thread/start",
                {
                    "model": model,
                    "cwd": cwd,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "ephemeral": True,
                    "serviceName": "qwenpaw_isolation_probe",
                    "environments": [],
                    "developerInstructions": (
                        "Do not use built-in tools. The qwenpaw_probe dynamic "
                        "tool is the only allowed action."
                    ),
                    "config": build_tool_isolation_config(
                        mcp_server_names=mcp_server_names,
                    ),
                    "dynamicTools": [
                        {
                            "name": "qwenpaw_probe",
                            "description": "Return a safe probe result",
                            "inputSchema": {
                                "type": "object",
                                "additionalProperties": False,
                            },
                        }
                    ],
                },
                timeout=args.timeout,
            )
            thread = thread_response.get("thread")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if not isinstance(thread_id, str):
                raise RuntimeError("thread/start returned no thread id")
            item_types: set[str] = set()
            prompts = (
                "List the current directory and execute echo probe.",
                "Use Web Search and read a local file.",
                "Call the qwenpaw_probe dynamic tool exactly once.",
            )
            for prompt in prompts:
                await _run_turn(
                    runtime, thread_id, prompt, item_types, args.timeout
                )
            await runtime.request(
                "thread/unsubscribe",
                {"threadId": thread_id},
                timeout=args.timeout,
            )

        blocked = sorted(item_types & _BLOCKED_ITEMS)
        if blocked:
            raise RuntimeError(f"built-in tool items observed: {blocked}")
        if dynamic_calls < 1:
            raise RuntimeError("the QwenPaw dynamic tool was not called")
        result.update(
            {
                "toolIsolationVerified": True,
                "dynamicToolCalls": dynamic_calls,
                "observedItemTypes": sorted(item_types),
            }
        )
        if settings_path is not None:
            settings.record_tool_isolation_verification(
                capabilities.schema_fingerprint,
                settings_path,
            )
            result["verificationRecorded"] = True
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        await runtime.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", help="absolute path to the Codex CLI")
    parser.add_argument("--model", help="model id for account-backed turns")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--account-backed",
        action="store_true",
        help="explicitly allow account/model/turn operations",
    )
    parser.add_argument(
        "--record-settings",
        metavar="PATH",
        help=(
            "record the verified schema fingerprint in this settings file "
            "after a successful account-backed probe"
        ),
    )
    args = parser.parse_args()
    if args.record_settings and not args.account_backed:
        parser.error("--record-settings requires --account-backed")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
