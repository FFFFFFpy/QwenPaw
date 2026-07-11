#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely probe the focused Codex App Server contract over stdio.

The probe never reads Codex authentication files and never prints credentials.
By default it performs only the initialize handshake. Model discovery and
redacted account checks are explicit because the official App Server may use
the operating system's credential store for those operations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any


class ProbeError(RuntimeError):
    pass


class ProbeClient:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._next_id = 0

    async def send(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise ProbeError("app-server stdin is unavailable")
        self.process.stdin.write(
            json.dumps(payload, separators=(",", ":")).encode() + b"\n",
        )
        await self.process.stdin.drain()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        await self.send(payload)
        if self.process.stdout is None:
            raise ProbeError("app-server stdout is unavailable")
        while True:
            line = await asyncio.wait_for(
                self.process.stdout.readline(),
                timeout,
            )
            if not line:
                raise ProbeError("app-server closed stdout")
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProbeError("app-server emitted malformed JSON") from exc
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                code = error.get("code") if isinstance(error, dict) else None
                raise ProbeError(f"{method} failed (code={code})")
            result = message.get("result")
            return result if isinstance(result, dict) else {}


def _resolve_binary(value: str | None) -> str:
    candidate = value or shutil.which("codex")
    if not candidate:
        raise ProbeError("codex is not installed or not on PATH")
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        raise ProbeError("custom codex path must be absolute")
    path = path.resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ProbeError("codex path is not an executable file")
    return str(path)


async def _run(args: argparse.Namespace) -> int:
    binary = _resolve_binary(args.binary)
    process = await asyncio.create_subprocess_exec(
        binary,
        "app-server",
        "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    client = ProbeClient(process)
    try:
        initialized = await client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "qwenpaw_protocol_probe",
                    "title": "QwenPaw protocol probe",
                    "version": "1",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "requestAttestation": False,
                },
            },
            args.timeout,
        )
        await client.send({"method": "initialized", "params": {}})
        print(
            json.dumps(
                {
                    "initialized": True,
                    "platformFamily": initialized.get("platformFamily"),
                    "platformOs": initialized.get("platformOs"),
                },
                sort_keys=True,
            ),
        )

        if args.models:
            models = await client.request(
                "model/list",
                {"limit": 100, "includeHidden": False},
                args.timeout,
            )
            rows = models.get("data")
            print(
                json.dumps(
                    {
                        "modelList": isinstance(rows, list),
                        "modelCount": (
                            len(rows) if isinstance(rows, list) else 0
                        ),
                    },
                    sort_keys=True,
                ),
            )

        if args.account:
            account_result = await client.request(
                "account/read",
                {"refreshToken": False},
                args.timeout,
            )
            account = account_result.get("account")
            print(
                json.dumps(
                    {
                        "connected": isinstance(account, dict),
                        "authType": (
                            account.get("type")
                            if isinstance(account, dict)
                            else None
                        ),
                        "planType": (
                            account.get("planType")
                            if isinstance(account, dict)
                            else None
                        ),
                    },
                    sort_keys=True,
                ),
            )
            limits = await client.request(
                "account/rateLimits/read",
                None,
                args.timeout,
            )
            print(json.dumps({"rateLimits": bool(limits)}, sort_keys=True))
        return 0
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 3.0)
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", help="absolute path to codex")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--models",
        action="store_true",
        help=("probe model discovery (may access the OS credential store)"),
    )
    parser.add_argument(
        "--account",
        action="store_true",
        help=(
            "probe redacted account and rate limits (may access the OS "
            "credential store)"
        ),
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except (ProbeError, asyncio.TimeoutError) as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
