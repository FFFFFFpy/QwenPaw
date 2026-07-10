#!/usr/bin/env python3
"""Scriptable JSONL fake used by runtime integration tests."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any


async def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def main() -> None:
    pending_reverse: list[dict[str, Any]] = []
    connected = False
    login_id = "login-fake"
    thread_counter = 0
    turn_counter = 0
    dynamic_threads: set[str] = set()
    pending_tool_turn: tuple[str, str] | None = None

    async def finish_turn(
        thread_id: str,
        turn_id: str,
        text: str = "fake response",
    ) -> None:
        await send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": f"answer-{turn_id}",
                    "delta": text,
                },
            },
        )
        await send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {
                        "id": turn_id,
                        "status": "completed",
                        "error": None,
                    },
                },
            },
        )

    while line := await asyncio.to_thread(sys.stdin.readline):
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        if method is None and request_id == "tool-fake":
            if pending_tool_turn is not None:
                await finish_turn(
                    pending_tool_turn[0],
                    pending_tool_turn[1],
                    "tool complete",
                )
                pending_tool_turn = None
            continue
        if method == "initialize":
            await send(
                {
                    "id": request_id,
                    "result": {
                        "userAgent": "codex-fake/1",
                        "codexHome": "/tmp/codex-fake",
                        "platformFamily": "unix",
                        "platformOs": "linux",
                    },
                },
            )
        elif method == "initialized":
            continue
        elif method == "test/echo":
            await send({"id": request_id, "result": message.get("params", {})})
        elif method == "account/read":
            await send(
                {
                    "id": request_id,
                    "result": {
                        "account": (
                            {
                                "type": "chatgpt",
                                "email": "fixture@example.test",
                                "planType": "plus",
                            }
                            if connected
                            else None
                        ),
                        "requiresOpenaiAuth": True,
                    },
                },
            )
        elif method == "account/login/start":
            result: dict[str, Any] = {
                "type": params.get("type"),
                "loginId": login_id,
            }
            if params.get("type") == "chatgptDeviceCode":
                result.update(
                    {
                        "verificationUrl": "https://example.test/device",
                        "userCode": "FAKE-CODE",
                    },
                )
            else:
                result["authUrl"] = "https://example.test/authorize"
            await send({"id": request_id, "result": result})
        elif method == "test/completeLogin":
            connected = True
            await send({"id": request_id, "result": {}})
            await send(
                {
                    "method": "account/login/completed",
                    "params": {
                        "loginId": login_id,
                        "success": True,
                        "error": None,
                    },
                },
            )
            await send(
                {
                    "method": "account/updated",
                    "params": {"authMode": "chatgpt"},
                },
            )
        elif method == "account/login/cancel":
            await send({"id": request_id, "result": {}})
        elif method == "account/logout":
            connected = False
            await send({"id": request_id, "result": {}})
            await send(
                {"method": "account/updated", "params": {"authMode": None}},
            )
        elif method == "model/list":
            await send(
                {
                    "id": request_id,
                    "result": {
                        "data": [
                            {
                                "id": "codex-fake",
                                "displayName": "Codex Fake",
                                "inputModalities": ["text", "image"],
                                "defaultReasoningEffort": "medium",
                                "supportedReasoningEfforts": [
                                    {"reasoningEffort": "low"},
                                    {"reasoningEffort": "medium"},
                                ],
                            },
                        ],
                        "nextCursor": None,
                    },
                },
            )
        elif method == "account/rateLimits/read":
            await send(
                {
                    "id": request_id,
                    "result": {
                        "rateLimits": {
                            "limitId": "codex",
                            "planType": "plus",
                            "primary": {
                                "usedPercent": 25,
                                "windowDurationMins": 300,
                                "resetsAt": 1780000000,
                            },
                        },
                    },
                },
            )
        elif method == "thread/start":
            thread_counter += 1
            thread_id = f"thread-{thread_counter}"
            if params.get("dynamicTools"):
                dynamic_threads.add(thread_id)
            await send(
                {"id": request_id, "result": {"thread": {"id": thread_id}}},
            )
        elif method == "turn/start":
            turn_counter += 1
            turn_id = f"turn-{turn_counter}"
            thread_id = str(params.get("threadId"))
            await send(
                {"id": request_id, "result": {"turn": {"id": turn_id}}},
            )
            serialized_input = json.dumps(params.get("input", []))
            if "WAIT_FOREVER" in serialized_input:
                continue
            if thread_id in dynamic_threads:
                pending_tool_turn = (thread_id, turn_id)

                async def request_tool(
                    tool_thread_id: str = thread_id,
                    tool_turn_id: str = turn_id,
                ) -> None:
                    await asyncio.sleep(0.01)
                    await send(
                        {
                            "id": "tool-fake",
                            "method": "item/tool/call",
                            "params": {
                                "threadId": tool_thread_id,
                                "turnId": tool_turn_id,
                                "callId": "call-fake",
                                "tool": "lookup",
                                "arguments": {"query": "fixture"},
                            },
                        },
                    )

                asyncio.create_task(request_tool())
            else:
                asyncio.create_task(finish_turn(thread_id, turn_id))
        elif method in {"turn/interrupt", "thread/unsubscribe"}:
            await send({"id": request_id, "result": {}})
        elif method == "test/reverse":
            pending_reverse.append(message)
            if len(pending_reverse) == 2:
                for item in reversed(pending_reverse):
                    await send(
                        {
                            "id": item["id"],
                            "result": item.get("params", {}),
                        },
                    )
                pending_reverse.clear()
        elif method == "test/notify":
            await send({"method": "event/test", "params": message["params"]})
            await send({"id": request_id, "result": {}})
        elif method == "test/serverRequest":
            await send(
                {
                    "id": "server-1",
                    "method": "item/tool/call",
                    "params": message["params"],
                },
            )
            await send({"id": request_id, "result": {}})
        elif method == "test/unknownServerRequest":
            await send(
                {
                    "id": "server-unknown",
                    "method": "unknown/call",
                    "params": {},
                },
            )
            await send({"id": request_id, "result": {}})
        elif method == "test/error":
            await send(
                {
                    "id": request_id,
                    "error": {"code": -32000, "message": "fixture failure"},
                },
            )
        elif method == "test/stderr":
            sys.stderr.write("fixture diagnostic\n" * 10000)
            sys.stderr.flush()
            await send({"id": request_id, "result": {}})
        elif method == "test/malformed":
            sys.stdout.write("not-json\n")
            sys.stdout.flush()
        elif method == "test/oversize":
            await send(
                {"id": request_id, "result": {"data": "x" * 70000}},
            )
        elif method == "test/crash":
            raise SystemExit(7)


if __name__ == "__main__":
    asyncio.run(main())
