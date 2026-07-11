#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark Codex subscription latency with and without QwenPaw tools."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from contextlib import nullcontext
import json
import logging
from pathlib import Path
import statistics
import sys
from typing import Any
import uuid

from agentscope.message import Msg, TextBlock
from agentscope.tool import Toolkit

from qwenpaw.agents.tools import discover_builtin_tool_funcs
from qwenpaw.governance import PolicyGuardedTool
from qwenpaw.providers.codex_subscription.chat_model import (
    CodexSubscriptionChatModel,
)
from qwenpaw.providers.codex_subscription.credential import (
    CodexSubscriptionCredential,
)
from qwenpaw.providers.codex_subscription.runtime import get_codex_runtime

PROMPT = "Reply with exactly: PONG"
GROUPS = {
    "A": {"effort": "low", "tools": False},
    "B": {"effort": "medium", "tools": False},
    "C": {"effort": "low", "tools": True},
    "D": {"effort": "medium", "tools": True},
}
METRICS = (
    "thread_start_ms",
    "turn_start_ms",
    "time_to_first_reasoning_ms",
    "time_to_first_text_ms",
    "total_ms",
    "cleanup_ms",
)


class _DiagnosticCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        marker = "codex_latency "
        if marker not in message:
            return
        try:
            self.rows.append(json.loads(message.split(marker, 1)[1]))
        except (TypeError, ValueError):
            return


async def _current_agent_tool_schemas() -> list[dict[str, Any]]:
    """Load QwenPaw's complete current agent tool catalog."""

    tools = [
        PolicyGuardedTool(
            func,
            governor=None,
            request_context={"session_id": "codex-latency-benchmark"},
        )
        for func in discover_builtin_tool_funcs()
    ]
    return await Toolkit(tools=tools).get_tool_schemas()


def _elapsed(events: dict[str, dict[str, Any]], name: str) -> float | None:
    row = events.get(name)
    value = row.get("elapsed_ms") if row else None
    return float(value) if isinstance(value, (int, float)) else None


def _duration(
    events: dict[str, dict[str, Any]],
    start: str,
    end: str,
) -> float | None:
    start_ms = _elapsed(events, start)
    end_ms = _elapsed(events, end)
    if start_ms is None or end_ms is None:
        return None
    return round(max(0.0, end_ms - start_ms), 3)


def _result_from_rows(
    rows: list[dict[str, Any]],
    *,
    group: str,
    run: int,
    warmup: bool,
    tool_count: int,
    error: str | None,
) -> dict[str, Any]:
    events = {row["event"]: row for row in rows}
    completed = _elapsed(events, "turn_completed")
    cleanup = _elapsed(events, "cleanup_completed")
    return {
        "type": "run",
        "group": group,
        "run": run,
        "warmup": warmup,
        "effort": GROUPS[group]["effort"],
        "thread_start_ms": _duration(
            events,
            "thread_start_sent",
            "thread_start_ack",
        ),
        "turn_start_ms": _duration(
            events,
            "turn_start_sent",
            "turn_start_ack",
        ),
        "time_to_first_reasoning_ms": _elapsed(
            events,
            "first_reasoning_delta",
        ),
        "time_to_first_text_ms": _elapsed(events, "first_text_delta"),
        "total_ms": completed if completed is not None else cleanup,
        "cleanup_ms": (
            round(max(0.0, cleanup - completed), 3)
            if cleanup is not None and completed is not None
            else None
        ),
        "tool_count": tool_count,
        "turn_count": sum(
            row.get("event") == "turn_start_sent" for row in rows
        ),
        "success": error is None and completed is not None,
        "error": error,
    }


async def _one_run(
    *,
    runtime: Any,
    capture: _DiagnosticCapture,
    model_id: str,
    group: str,
    run: int,
    warmup: bool,
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    runtime.settings.codex_dynamic_tool_mode = (
        "all" if GROUPS[group]["tools"] else "off"
    )
    model = CodexSubscriptionChatModel(
        credential=CodexSubscriptionCredential(id="codex-latency-benchmark"),
        model=model_id,
        parameters=CodexSubscriptionChatModel.Parameters(
            reasoning_effort=str(GROUPS[group]["effort"]),
        ),
        runtime=runtime,
    )
    session_id = f"codex-bench-{group}-{run}-{uuid.uuid4().hex}"
    offset = len(capture.rows)
    error: str | None = None
    try:
        response = await model(
            [
                Msg(
                    name="user",
                    role="user",
                    content=[TextBlock(text=PROMPT)],
                ),
            ],
            tools=tools if GROUPS[group]["tools"] else None,
            tool_choice="auto" if GROUPS[group]["tools"] else "none",
            session_id=session_id,
        )
        _ = [chunk async for chunk in response]
        await runtime.wait_background_tasks()
    except Exception as exc:  # benchmark must preserve subsequent groups
        error = f"{type(exc).__name__}: {exc}"
        await runtime.wait_background_tasks()
    rows = [
        row
        for row in capture.rows[offset:]
        if row.get("session_id") == session_id
    ]
    return _result_from_rows(
        rows,
        group=group,
        run=run,
        warmup=warmup,
        tool_count=len(tools) if GROUPS[group]["tools"] else 0,
        error=error,
    )


def _median(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return round(statistics.median(present), 3) if present else None


def _summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row["warmup"]:
            by_group[row["group"]].append(row)
    summaries = []
    for group, settings in GROUPS.items():
        group_rows = by_group[group]
        summaries.append(
            {
                "type": "summary",
                "group": group,
                "effort": settings["effort"],
                "tool_count": (
                    group_rows[0]["tool_count"] if group_rows else 0
                ),
                "runs": len(group_rows),
                "successes": sum(row["success"] for row in group_rows),
                "medians": {
                    metric: _median([row[metric] for row in group_rows])
                    for metric in METRICS
                },
            },
        )
    medians = {row["group"]: row["medians"] for row in summaries}

    def delta(left: str, right: str) -> float | None:
        a = medians[left]["total_ms"]
        b = medians[right]["total_ms"]
        return round(b - a, 3) if a is not None and b is not None else None

    summaries.append(
        {
            "type": "differences",
            "total_ms": {
                "medium_vs_low_tools_0_B_minus_A": delta("A", "B"),
                "tools_all_vs_0_low_C_minus_A": delta("A", "C"),
                "tools_all_vs_0_medium_D_minus_B": delta("B", "D"),
                "medium_vs_low_tools_all_D_minus_C": delta("C", "D"),
            },
        },
    )
    return summaries


async def _run(args: argparse.Namespace) -> int:
    capture = _DiagnosticCapture()
    diagnostic_logger = logging.getLogger(
        "qwenpaw.providers.codex_subscription.diagnostics",
    )
    diagnostic_logger.addHandler(capture)
    diagnostic_logger.setLevel(logging.INFO)
    runtime = get_codex_runtime()
    if args.binary:
        runtime.settings.binary_path = str(Path(args.binary).expanduser())
    runtime.settings.request_timeout_seconds = args.timeout
    tools = await _current_agent_tool_schemas()
    rows: list[dict[str, Any]] = []
    output = Path(args.output).expanduser().resolve() if args.output else None
    try:
        # The returned handle is entered immediately below.
        # pylint: disable=consider-using-with
        stream_context = (
            output.open("w", encoding="utf-8")
            if output
            else nullcontext(sys.stdout)
        )
        # pylint: enable=consider-using-with
        with stream_context as stream:
            await runtime.start()
            for group in GROUPS:
                for run in range(args.warmup):
                    row = await _one_run(
                        runtime=runtime,
                        capture=capture,
                        model_id=args.model,
                        group=group,
                        run=run + 1,
                        warmup=True,
                        tools=tools,
                    )
                    rows.append(row)
                    print(
                        json.dumps(row, sort_keys=True),
                        file=stream,
                        flush=True,
                    )
                for run in range(args.runs):
                    row = await _one_run(
                        runtime=runtime,
                        capture=capture,
                        model_id=args.model,
                        group=group,
                        run=run + 1,
                        warmup=False,
                        tools=tools,
                    )
                    rows.append(row)
                    print(
                        json.dumps(row, sort_keys=True),
                        file=stream,
                        flush=True,
                    )
            for summary in _summaries(rows):
                print(
                    json.dumps(summary, sort_keys=True),
                    file=stream,
                    flush=True,
                )
    finally:
        diagnostic_logger.removeHandler(capture)
        await runtime.stop()
    return 0 if all(row["success"] for row in rows) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--binary", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if args.warmup < 0 or args.runs < 1:
        parser.error("--warmup must be >= 0 and --runs must be >= 1")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
