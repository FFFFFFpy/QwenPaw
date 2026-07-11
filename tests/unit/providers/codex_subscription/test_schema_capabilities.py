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


def protocol_documents(*, restricted_sandbox: bool = True) -> dict[str, dict]:
    if restricted_sandbox:
        sandbox_schema: dict = {
            "oneOf": [
                {
                    "type": "object",
                    "required": ["type", "networkAccess", "access"],
                    "properties": {
                        "type": {"type": "string", "enum": ["readOnly"]},
                        "networkAccess": {"type": "boolean"},
                        "access": {
                            "type": "object",
                            "required": ["type", "readableRoots"],
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["restricted"],
                                },
                                "readableRoots": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            ],
        }
    else:
        sandbox_schema = {
            "type": "string",
            "enum": ["read-only", "workspace-write"],
        }

    request_methods = {
        "initialize": "InitializeParams",
        "account/read": "EmptyParams",
        "account/login/start": "LoginParams",
        "account/login/cancel": "EmptyParams",
        "account/logout": "EmptyParams",
        "account/rateLimits/read": "EmptyParams",
        "model/list": "EmptyParams",
        "thread/start": "ThreadStartParams",
        "thread/unsubscribe": "EmptyParams",
        "turn/start": "EmptyParams",
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
                "sandbox": {"$ref": "#/definitions/SandboxPolicy"},
                "dynamicTools": {"type": "array"},
            },
        },
        "SandboxPolicy": sandbox_schema,
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
            _notification_variant("initialized", "EmptyParams"),
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
                    "arguments": {},
                },
            },
        },
    }
    server_notification = {"oneOf": [], "definitions": {}}
    return {
        "client_request": client_request,
        "client_notification": client_notification,
        "server_request": server_request,
        "server_notification": server_notification,
    }


def test_structured_schema_detects_restricted_sandbox_and_requests():
    catalog = AppServerSchemaCatalog.from_documents(**protocol_documents())
    capabilities = CodexCapabilities.from_catalog(catalog)
    assert capabilities.restricted_read_sandbox is True
    assert capabilities.network_disabled_sandbox is True
    assert capabilities.dynamic_tools is True
    assert capabilities.command_approval_requests is True
    assert capabilities.file_approval_requests is True
    assert capabilities.permission_approval_requests is True
    assert capabilities.mcp_approval_requests is True
    capabilities.validate_required_surface()

    assert build_restricted_sandbox_policy(capabilities, "/tmp/isolated") == {
        "type": "readOnly",
        "networkAccess": False,
        "access": {
            "type": "restricted",
            "readableRoots": ["/tmp/isolated"],
        },
    }


def test_legacy_read_only_string_is_not_a_security_capability():
    catalog = AppServerSchemaCatalog.from_documents(
        **protocol_documents(restricted_sandbox=False),
    )
    capabilities = CodexCapabilities.from_catalog(catalog)
    assert capabilities.restricted_read_sandbox is False
    assert capabilities.network_disabled_sandbox is False
    with pytest.raises(CodexSubscriptionError) as caught:
        capabilities.validate_required_surface()
    assert caught.value.error_code == "CODEX_SANDBOX_UNSUPPORTED"


def test_security_words_outside_the_field_shape_do_not_count():
    documents = protocol_documents(restricted_sandbox=False)
    documents["server_notification"][
        "description"
    ] = "readOnly restricted readableRoots networkAccess approvalPolicy"
    capabilities = CodexCapabilities.from_catalog(
        AppServerSchemaCatalog.from_documents(**documents),
    )
    assert capabilities.restricted_read_sandbox is False
    assert capabilities.network_disabled_sandbox is False


def test_missing_required_method_is_incompatible():
    documents = protocol_documents()
    documents["client_request"]["oneOf"] = [
        variant
        for variant in documents["client_request"]["oneOf"]
        if variant["properties"]["method"]["enum"] != ["turn/interrupt"]
    ]
    capabilities = CodexCapabilities.from_catalog(
        AppServerSchemaCatalog.from_documents(**documents),
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        capabilities.validate_required_surface()
    assert caught.value.error_code == "CODEX_PROTOCOL_INCOMPATIBLE"
