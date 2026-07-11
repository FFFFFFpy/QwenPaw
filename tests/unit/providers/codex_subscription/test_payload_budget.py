# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.payload_budget import (
    TurnPayloadBudget,
)


def _plan() -> tuple[dict, dict]:
    thread = {
        "model": "codex-test",
        "dynamicTools": [
            {
                "name": "lookup",
                "description": "d" * 40,
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
        ],
    }
    turn = {
        "input": [
            {"type": "text", "text": "history" * 10},
            {"type": "image", "url": "data:image/png;base64," + "a" * 80},
            {"type": "image", "url": "data:image/png;base64," + "b" * 80},
        ],
    }
    return thread, turn


def test_payload_budget_counts_text_tools_images_and_json_overhead():
    _thread, turn = _plan()
    measured = TurnPayloadBudget.measure("turn/start", turn)
    assert measured > len("history" * 10) + 160

    TurnPayloadBudget(measured).validate_rpc_request(
        "turn/start",
        turn,
        attachment_count=2,
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        TurnPayloadBudget(measured - 1).validate_rpc_request(
            "turn/start",
            turn,
            attachment_count=2,
        )
    assert caught.value.error_code == "CODEX_REQUEST_TOO_LARGE"
    assert caught.value.details == {
        "method": "turn/start",
        "payload_bytes": measured,
        "limit_bytes": measured - 1,
        "attachment_count": 2,
    }


def test_multiple_images_are_budgeted_cumulatively():
    _thread, turn = _plan()
    one_image = {"input": turn["input"][:-1]}
    assert TurnPayloadBudget.measure(
        "turn/start",
        turn,
    ) > TurnPayloadBudget.measure(
        "turn/start",
        one_image,
    )


def test_payload_error_details_never_contain_request_content():
    _thread, turn = _plan()
    secret_marker = "must-not-leak"
    turn["input"][0]["text"] = secret_marker * 20
    with pytest.raises(CodexSubscriptionError) as caught:
        TurnPayloadBudget(10).validate_rpc_request(
            "turn/start",
            turn,
            attachment_count=2,
        )
    serialized = str(caught.value.details)
    assert secret_marker not in serialized
    assert set(caught.value.details) == {
        "method",
        "payload_bytes",
        "limit_bytes",
        "attachment_count",
    }
