# -*- coding: utf-8 -*-
"""Subscription usage-window mapping."""

from __future__ import annotations

import time

from pydantic import BaseModel

from .runtime import CodexAppServerRuntime, RuntimeState


class CodexRateLimitWindow(BaseModel):
    used_percent: float
    window_duration_mins: int | None = None
    resets_at: int | None = None


class CodexCredits(BaseModel):
    has_credits: bool
    unlimited: bool
    balance: str | None = None


class CodexRateLimits(BaseModel):
    limit_id: str | None = None
    limit_name: str | None = None
    plan_type: str | None = None
    primary: CodexRateLimitWindow | None = None
    secondary: CodexRateLimitWindow | None = None
    credits: CodexCredits | None = None
    updated_at: int


class RateLimitService:
    def __init__(self, runtime: CodexAppServerRuntime) -> None:
        self.runtime = runtime

    async def read(self) -> CodexRateLimits:
        if self.runtime.state is not RuntimeState.READY:
            await self.runtime.start()
        response = await self.runtime.request("account/rateLimits/read", None)
        snapshot = response.get("rateLimits")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        return CodexRateLimits(
            limit_id=_string(snapshot.get("limitId")),
            limit_name=_string(snapshot.get("limitName")),
            plan_type=_string(snapshot.get("planType")),
            primary=_window(snapshot.get("primary")),
            secondary=_window(snapshot.get("secondary")),
            credits=_credits(snapshot.get("credits")),
            updated_at=int(time.time()),
        )


def _window(value: object) -> CodexRateLimitWindow | None:
    if not isinstance(value, dict):
        return None
    used = value.get("usedPercent")
    if not isinstance(used, (int, float)):
        return None
    duration = value.get("windowDurationMins")
    resets = value.get("resetsAt")
    return CodexRateLimitWindow(
        used_percent=float(used),
        window_duration_mins=(
            int(duration) if isinstance(duration, (int, float)) else None
        ),
        resets_at=int(resets) if isinstance(resets, (int, float)) else None,
    )


def _credits(value: object) -> CodexCredits | None:
    if not isinstance(value, dict):
        return None
    return CodexCredits(
        has_credits=bool(value.get("hasCredits")),
        unlimited=bool(value.get("unlimited")),
        balance=_string(value.get("balance")),
    )


def _string(value: object) -> str | None:
    return str(value) if value is not None else None
