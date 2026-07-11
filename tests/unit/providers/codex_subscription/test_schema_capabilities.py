# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from qwenpaw.providers.codex_subscription.errors import (
    CodexSubscriptionError,
)
from qwenpaw.providers.codex_subscription.schema_capabilities import (
    AppServerSchemaCatalog,
    CodexCapabilities,
    build_restricted_sandbox_policy,
)


def _request_variant(method: str, params_ref: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": [method]},
            "params": {"$ref": f"#/definitions/{params_ref}"},
        },
    }


def _notification_variant(method: str, params_ref: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": [method]},
            "params": {"$ref": f"#/definitions/{params_ref}"},
        },
    }


def protocol_documents() -> dict[str, dict]:
    request_methods = {
        "initialize": "InitializeParams",
        "account/read": "EmptyParams",
        "account/login/start": "LoginParams",
        "account/login/cancel": "EmptyParams",
        "account/logout": "EmptyParams",
        "account/rateLimits/read": "EmptyParams",
        "model/list": "EmptyParams",
        "mcpServerStatus/list": "EmptyParams",
        "thread/start": "ThreadStartParams",
        "thread/unsubscribe": "EmptyParams",
        "turn/start": "TurnStartParams",
        "turn/interrupt": "EmptyParams",
    }
    client_definitions = {
        "EmptyParams": {"type": "object"},
        "InitializeParams": {"type": "object"},
        "LoginParams": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["chatgpt", "chatgptDeviceCode"],
                },
            },
        },
        "ThreadStartParams": {
            "type": "object",
            "properties": {
                "sandbox": {"$ref": "#/definitions/SandboxMode"},
                "config": {"type": ["object", "null"]},
                "dynamicTools": {"type": "array"},
                "environments": {"type": "array"},
            },
        },
        "TurnStartParams": {
            "type": "object",
            "properties": {
                "threadId": {"type": "string"},
                "input": {"type": "array"},
                "effort": {"type": ["string", "null"]},
                "sandboxPolicy": {"$ref": "#/definitions/SandboxPolicy"},
            },
        },
        "SandboxMode": {
            "type": "string",
            "enum": ["read-only", "workspace-write"],
        },
        "SandboxPolicy": {
            "oneOf": [
                {
                    "type": "object",
                    "required": ["type"],
                    "properties": {
                        "type": {"type": "string", "enum": ["readOnly"]},
                        "networkAccess": {"type": "boolean"},
                    },
                },
            ],
        },
    }
    client_request = {
        "oneOf": [
            _request_variant(method, params_ref)
            for method, params_ref in request_methods.items()
        ],
        "definitions": client_definitions,
    }
    client_notification = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["initialized"]},
                },
            },
        ],
        "definitions": {"EmptyParams": {"type": "object"}},
    }
    server_methods = {
        "item/commandExecution/requestApproval": "ApprovalParams",
        "item/fileChange/requestApproval": "ApprovalParams",
        "item/permissions/requestApproval": "ApprovalParams",
        "mcpServer/elicitation/request": "ApprovalParams",
        "item/tool/call": "DynamicToolCallParams",
    }
    server_request = {
        "oneOf": [
            _request_variant(method, params_ref)
            for method, params_ref in server_methods.items()
        ],
        "definitions": {
            "ApprovalParams": {"type": "object"},
            "DynamicToolCallParams": {
                "type": "object",
                "required": [
                    "threadId",
                    "turnId",
                    "callId",
                    "tool",
                    "arguments",
                ],
                "properties": {
                    "threadId": {"type": "string"},
                    "turnId": {"type": "string"},
                    "callId": {"type": "string"},
                    "namespace": {"type": ["string", "null"]},
                    "tool": {"type": "string"},
                    "arguments": True,
                },
            },
        },
    }
    server_notification = {
        "oneOf": [
            _notification_variant(
                "item/agentMessage/delta",
                "AgentMessageDeltaNotification",
            ),
            _notification_variant(
                "turn/completed",
                "TurnCompletedNotification",
            ),
        ],
        "definitions": {
            "AgentMessageDeltaNotification": {"type": "object"},
            "TurnCompletedNotification": {"type": "object"},
        },
    }
    client_responses: dict[str, dict] = {
        method: {"type": "object"} for method in request_methods
    }
    client_responses["mcpServerStatus/list"] = {
        "type": "object",
        "required": ["data"],
        "properties": {
            "data": {"type": "array"},
            "nextCursor": {"type": ["string", "null"]},
        },
    }
    server_responses = {
        "item/tool/call": {
            "type": "object",
            "required": ["contentItems", "success"],
            "properties": {
                "contentItems": {"type": "array"},
                "success": {"type": "boolean"},
            },
        },
    }
    return {
        "client_request": client_request,
        "client_notification": client_notification,
        "server_request": server_request,
        "server_notification": server_notification,
        "client_responses": client_responses,
        "server_responses": server_responses,
    }


def test_structured_schema_detects_real_sandbox_and_requests():
    catalog = AppServerSchemaCatalog.from_documents(**protocol_documents())
    capabilities = CodexCapabilities.from_catalog(catalog)
    assert capabilities.thread_sandbox_mode is True
    assert capabilities.turn_sandbox_policy is True
    assert capabilities.sandbox_network_disable is True
    assert capabilities.config_overrides is True
    assert capabilities.tool_isolation_verified is False
    assert capabilities.dynamic_tools is True
    assert capabilities.dynamic_tool_namespace is True
    assert capabilities.required_protocol_methods is True
    assert not capabilities.missing_required_methods
    assert capabilities.command_approval_requests is True
    assert capabilities.file_approval_requests is True
    assert capabilities.permission_approval_requests is True
    assert capabilities.mcp_approval_requests is True
    capabilities.validate_required_surface()

    assert catalog.request_schema("thread/start") is not None
    assert catalog.response_schema("thread/start") is not None
    assert catalog.notification_schema("initialized") is not None
    assert catalog.notification_schema("turn/completed") is not None
    assert catalog.server_request_schema("item/tool/call") is not None
    assert catalog.supports_field("thread/start", "sandbox")
    assert catalog.supports_enum(
        "account/login/start",
        "type",
        "chatgpt",
    )

    assert build_restricted_sandbox_policy(capabilities) == {
        "type": "readOnly",
        "networkAccess": False,
    }


def test_missing_turn_sandbox_policy_is_not_compatible():
    documents = protocol_documents()
    definitions = documents["client_request"]["definitions"]
    definitions["TurnStartParams"]["properties"].pop("sandboxPolicy")
    catalog = AppServerSchemaCatalog.from_documents(**documents)
    capabilities = CodexCapabilities.from_catalog(catalog)
    assert capabilities.thread_sandbox_mode is True
    assert capabilities.turn_sandbox_policy is False
    with pytest.raises(CodexSubscriptionError) as caught:
        capabilities.validate_required_surface()
    assert caught.value.error_code == "CODEX_SANDBOX_UNSUPPORTED"


def test_missing_empty_environment_isolation_is_not_compatible():
    documents = protocol_documents()
    definitions = documents["client_request"]["definitions"]
    definitions["ThreadStartParams"]["properties"].pop("environments")
    capabilities = CodexCapabilities.from_catalog(
        AppServerSchemaCatalog.from_documents(**documents),
    )

    assert capabilities.environments_field is False
    with pytest.raises(CodexSubscriptionError) as caught:
        capabilities.validate_required_surface()
    assert caught.value.error_code == "CODEX_SANDBOX_UNSUPPORTED"


def test_security_words_outside_the_field_shape_do_not_count():
    documents = protocol_documents()
    definitions = documents["client_request"]["definitions"]
    definitions["TurnStartParams"]["properties"].pop("sandboxPolicy")
    documents["server_notification"][
        "description"
    ] = "readOnly restricted readableRoots networkAccess approvalPolicy"
    capabilities = CodexCapabilities.from_catalog(
        AppServerSchemaCatalog.from_documents(**documents),
    )
    assert capabilities.turn_sandbox_policy is False
    assert capabilities.sandbox_network_disable is False


@pytest.mark.parametrize(
    ("surface", "method"),
    [
        ("client_request", "initialize"),
        ("client_request", "account/read"),
        ("client_request", "account/login/start"),
        ("client_request", "account/logout"),
        ("client_request", "model/list"),
        ("client_request", "thread/start"),
        ("client_request", "turn/start"),
        ("client_request", "turn/interrupt"),
        ("client_request", "thread/unsubscribe"),
        ("client_notification", "initialized"),
        ("server_notification", "item/agentMessage/delta"),
        ("server_notification", "turn/completed"),
    ],
)
def test_missing_required_method_is_incompatible(surface, method):
    documents = protocol_documents()
    documents[surface]["oneOf"] = [
        variant
        for variant in documents[surface]["oneOf"]
        if variant["properties"]["method"]["enum"] != [method]
    ]
    capabilities = CodexCapabilities.from_catalog(
        AppServerSchemaCatalog.from_documents(**documents),
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        capabilities.validate_required_surface()
    assert caught.value.error_code == "CODEX_PROTOCOL_INCOMPATIBLE"
    assert method in caught.value.details["missing_methods"]


@pytest.mark.parametrize("missing_field", ["contentItems", "success"])
def test_dynamic_tools_require_response_contract(missing_field):
    documents = protocol_documents()
    response = documents["server_responses"]["item/tool/call"]
    response["properties"].pop(missing_field)
    capabilities = CodexCapabilities.from_catalog(
        AppServerSchemaCatalog.from_documents(**documents),
    )
    assert capabilities.dynamic_tools is False


def test_login_method_without_chatgpt_variant_is_incompatible():
    documents = protocol_documents()
    definitions = documents["client_request"]["definitions"]
    definitions["LoginParams"]["properties"]["type"]["enum"] = [
        "chatgptDeviceCode",
    ]
    capabilities = CodexCapabilities.from_catalog(
        AppServerSchemaCatalog.from_documents(**documents),
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        capabilities.validate_required_surface()
    assert caught.value.error_code == "CODEX_PROTOCOL_INCOMPATIBLE"
    assert (
        "account/login/start[type=chatgpt]"
        in caught.value.details["missing_methods"]
    )
