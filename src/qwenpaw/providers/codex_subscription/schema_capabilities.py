"""Structured capability detection from generated App Server schemas."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Iterable

from .errors import CodexSubscriptionError

Schema = dict[str, Any]

_CLIENT_RESPONSE_FILES = {
    "initialize": "v1/InitializeResponse.json",
    "account/read": "v2/GetAccountResponse.json",
    "account/login/start": "v2/LoginAccountResponse.json",
    "account/logout": "v2/LogoutAccountResponse.json",
    "model/list": "v2/ModelListResponse.json",
    "thread/start": "v2/ThreadStartResponse.json",
    "thread/unsubscribe": "v2/ThreadUnsubscribeResponse.json",
    "turn/start": "v2/TurnStartResponse.json",
    "turn/interrupt": "v2/TurnInterruptResponse.json",
}
_SERVER_RESPONSE_FILES = {
    "item/tool/call": "DynamicToolCallResponse.json",
}
_REQUIRED_METHODS = (
    ("request", "initialize"),
    ("client_notification", "initialized"),
    ("request", "account/read"),
    ("request", "account/login/start"),
    ("request", "account/logout"),
    ("request", "model/list"),
    ("request", "thread/start"),
    ("request", "turn/start"),
    ("request", "turn/interrupt"),
    ("request", "thread/unsubscribe"),
    ("notification", "item/agentMessage/delta"),
    ("notification", "turn/completed"),
)


@dataclass(frozen=True)
class _SchemaEntry:
    schema: Schema
    definitions: Schema


class AppServerSchemaCatalog:
    """Method-indexed view of the four generated protocol envelopes."""

    def __init__(
        self,
        *,
        client_requests: dict[str, _SchemaEntry],
        client_notifications: dict[str, _SchemaEntry],
        server_requests: dict[str, _SchemaEntry],
        server_notifications: dict[str, _SchemaEntry],
        client_responses: dict[str, _SchemaEntry],
        server_responses: dict[str, _SchemaEntry],
        fingerprint: str,
    ) -> None:
        self._client_requests = client_requests
        self._client_notifications = client_notifications
        self._server_requests = server_requests
        self._server_notifications = server_notifications
        self._client_responses = client_responses
        self._server_responses = server_responses
        self.fingerprint = fingerprint

    @classmethod
    def from_documents(
        cls,
        *,
        client_request: Schema,
        client_notification: Schema,
        server_request: Schema,
        server_notification: Schema,
        client_responses: dict[str, Schema] | None = None,
        server_responses: dict[str, Schema] | None = None,
    ) -> "AppServerSchemaCatalog":
        documents = {
            "client_request": client_request,
            "client_notification": client_notification,
            "server_request": server_request,
            "server_notification": server_notification,
            "client_responses": client_responses or {},
            "server_responses": server_responses or {},
        }
        canonical = json.dumps(
            documents,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            client_requests=_index_envelope(client_request),
            client_notifications=_index_envelope(client_notification),
            server_requests=_index_envelope(server_request),
            server_notifications=_index_envelope(server_notification),
            client_responses=_index_responses(client_responses or {}),
            server_responses=_index_responses(server_responses or {}),
            fingerprint=hashlib.sha256(canonical).hexdigest(),
        )

    @classmethod
    def from_directory(cls, directory: Path) -> "AppServerSchemaCatalog":
        names = {
            "client_request": "ClientRequest.json",
            "client_notification": "ClientNotification.json",
            "server_request": "ServerRequest.json",
            "server_notification": "ServerNotification.json",
        }
        documents: dict[str, Schema] = {}
        for key, name in names.items():
            path = directory / name
            if not path.is_file():
                raise CodexSubscriptionError(
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    f"Codex App Server did not generate {name}",
                )
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CodexSubscriptionError(
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    f"Codex App Server generated invalid {name}",
                ) from exc
            if not isinstance(value, dict):
                raise CodexSubscriptionError(
                    "CODEX_PROTOCOL_INCOMPATIBLE",
                    f"Codex App Server generated invalid {name}",
                )
            documents[key] = value
        client_responses = _read_response_documents(
            directory,
            _CLIENT_RESPONSE_FILES,
        )
        server_responses = _read_response_documents(
            directory,
            _SERVER_RESPONSE_FILES,
        )
        return cls.from_documents(
            **documents,
            client_responses=client_responses,
            server_responses=server_responses,
        )

    def has_method(self, name: str) -> bool:
        return any(
            name in collection
            for collection in (
                self._client_requests,
                self._client_notifications,
                self._server_requests,
                self._server_notifications,
            )
        )

    def request_schema(self, name: str) -> Schema | None:
        entry = self._client_requests.get(name)
        return _resolved_copy(entry) if entry else None

    def response_schema(self, name: str) -> Schema | None:
        entry = self._client_responses.get(name)
        if entry is None:
            entry = self._server_responses.get(name)
        return _resolved_copy(entry) if entry else None

    def notification_schema(self, name: str) -> Schema | None:
        entry = self._server_notifications.get(name)
        if entry is None:
            entry = self._client_notifications.get(name)
        return _resolved_copy(entry) if entry else None

    def server_request_schema(self, name: str) -> Schema | None:
        entry = self._server_requests.get(name)
        return _resolved_copy(entry) if entry else None

    def has_client_request(self, name: str) -> bool:
        return name in self._client_requests

    def has_client_notification(self, name: str) -> bool:
        return name in self._client_notifications

    def has_server_request(self, name: str) -> bool:
        return name in self._server_requests

    def has_server_notification(self, name: str) -> bool:
        return name in self._server_notifications

    def supports_field(
        self,
        method: str,
        path: str | Iterable[str],
        *,
        surface: str = "request",
    ) -> bool:
        entry = self._entry(method, surface)
        return bool(entry and _nodes_at(entry, _normalize_path(path)))

    def supports_enum(
        self,
        method: str,
        path: str | Iterable[str],
        value: Any,
        *,
        surface: str = "request",
    ) -> bool:
        entry = self._entry(method, surface)
        if entry is None:
            return False
        return any(
            value in _enum_values(node, entry.definitions)
            for node in _nodes_at(entry, _normalize_path(path))
        )

    def supports_restricted_read_sandbox(self) -> tuple[bool, bool]:
        entry = self._client_requests.get("thread/start")
        if entry is None:
            return False, False
        for sandbox_node in _nodes_at(entry, ("sandbox",)):
            for branch in _branches(sandbox_node, entry.definitions):
                if not _node_supports_enum(
                    branch,
                    entry.definitions,
                    ("type",),
                    "readOnly",
                ):
                    continue
                network_disabled = _node_supports_type(
                    branch,
                    entry.definitions,
                    ("networkAccess",),
                    "boolean",
                )
                for access in _nodes_at(
                    _SchemaEntry(branch, entry.definitions),
                    ("access",),
                ):
                    if not _node_supports_enum(
                        access,
                        entry.definitions,
                        ("type",),
                        "restricted",
                    ):
                        continue
                    roots = _nodes_at(
                        _SchemaEntry(access, entry.definitions),
                        ("readableRoots",),
                    )
                    if any(
                        _array_accepts_type(root, entry.definitions, "string")
                        for root in roots
                    ):
                        return True, network_disabled
        return False, False

    def _entry(self, method: str, surface: str) -> _SchemaEntry | None:
        if surface == "request":
            return self._client_requests.get(method)
        if surface == "client_notification":
            return self._client_notifications.get(method)
        if surface == "server_request":
            return self._server_requests.get(method)
        if surface == "notification":
            return self._server_notifications.get(method)
        if surface == "response":
            return self._client_responses.get(
                method,
            ) or self._server_responses.get(method)
        if surface == "server_response":
            return self._server_responses.get(method)
        raise ValueError(f"Unknown schema surface: {surface}")


@dataclass(frozen=True)
class CodexCapabilities:
    browser_login: bool
    device_code_login: bool
    model_list: bool
    rate_limits: bool
    image_input: bool
    dynamic_tools: bool
    dynamic_tool_namespace: bool
    turn_interrupt: bool
    thread_archive_or_unsubscribe: bool
    restricted_read_sandbox: bool
    network_disabled_sandbox: bool
    runtime_workspace_roots: bool
    environments_field: bool
    command_approval_requests: bool
    file_approval_requests: bool
    permission_approval_requests: bool
    mcp_approval_requests: bool
    builtin_tools_disable_mode: str | None
    required_protocol_methods: bool
    missing_required_methods: tuple[str, ...]
    schema_fingerprint: str = ""

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_catalog(
        cls,
        catalog: AppServerSchemaCatalog,
    ) -> "CodexCapabilities":
        restricted, network_disabled = (
            catalog.supports_restricted_read_sandbox()
        )
        browser_login = catalog.has_client_request(
            "account/login/start"
        ) and catalog.supports_enum(
            "account/login/start", ("type",), "chatgpt"
        )
        missing = [
            method
            for surface, method in _REQUIRED_METHODS
            if catalog._entry(method, surface) is None
        ]
        if not browser_login and "account/login/start" not in missing:
            missing.append("account/login/start[type=chatgpt]")
        missing_required_methods = tuple(missing)
        dynamic_request_fields = (
            "threadId",
            "turnId",
            "callId",
            "tool",
            "arguments",
        )
        dynamic_response_fields = ("contentItems", "success")
        return cls(
            browser_login=browser_login,
            device_code_login=catalog.supports_enum(
                "account/login/start", ("type",), "chatgptDeviceCode"
            ),
            model_list=catalog.has_client_request("model/list"),
            rate_limits=catalog.has_client_request("account/rateLimits/read"),
            image_input=False,
            dynamic_tools=(
                catalog.supports_field("thread/start", ("dynamicTools",))
                and catalog.has_server_request("item/tool/call")
                and all(
                    catalog.supports_field(
                        "item/tool/call",
                        field,
                        surface="server_request",
                    )
                    for field in dynamic_request_fields
                )
                and all(
                    catalog.supports_field(
                        "item/tool/call",
                        field,
                        surface="server_response",
                    )
                    for field in dynamic_response_fields
                )
            ),
            dynamic_tool_namespace=catalog.supports_field(
                "item/tool/call",
                "namespace",
                surface="server_request",
            ),
            turn_interrupt=catalog.has_client_request("turn/interrupt"),
            thread_archive_or_unsubscribe=(
                catalog.has_client_request("thread/unsubscribe")
                or catalog.has_client_request("thread/archive")
            ),
            restricted_read_sandbox=restricted,
            network_disabled_sandbox=network_disabled,
            runtime_workspace_roots=catalog.supports_field(
                "thread/start", ("runtimeWorkspaceRoots",)
            ),
            environments_field=catalog.supports_field(
                "thread/start", ("environments",)
            ),
            command_approval_requests=catalog.has_server_request(
                "item/commandExecution/requestApproval"
            ),
            file_approval_requests=catalog.has_server_request(
                "item/fileChange/requestApproval"
            ),
            permission_approval_requests=catalog.has_server_request(
                "item/permissions/requestApproval"
            ),
            mcp_approval_requests=catalog.has_server_request(
                "mcpServer/elicitation/request"
            ),
            builtin_tools_disable_mode=(
                "restricted-read+network-disabled"
                if restricted and network_disabled
                else None
            ),
            required_protocol_methods=not missing_required_methods,
            missing_required_methods=missing_required_methods,
            schema_fingerprint=catalog.fingerprint,
        )

    @classmethod
    def focused_contract(
        cls,
        *,
        restricted_read_sandbox: bool = True,
        network_disabled_sandbox: bool | None = None,
    ) -> "CodexCapabilities":
        """Capabilities used by the protocol fake, not real detection."""

        if network_disabled_sandbox is None:
            network_disabled_sandbox = restricted_read_sandbox
        return cls(
            browser_login=True,
            device_code_login=True,
            model_list=True,
            rate_limits=True,
            image_input=True,
            dynamic_tools=True,
            dynamic_tool_namespace=True,
            turn_interrupt=True,
            thread_archive_or_unsubscribe=True,
            restricted_read_sandbox=restricted_read_sandbox,
            network_disabled_sandbox=network_disabled_sandbox,
            runtime_workspace_roots=True,
            environments_field=True,
            command_approval_requests=True,
            file_approval_requests=True,
            permission_approval_requests=True,
            mcp_approval_requests=True,
            builtin_tools_disable_mode=(
                "restricted-read+network-disabled"
                if restricted_read_sandbox and network_disabled_sandbox
                else None
            ),
            required_protocol_methods=True,
            missing_required_methods=(),
            schema_fingerprint="fake-contract",
        )

    def validate_required_surface(self) -> None:
        if self.missing_required_methods:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "The installed Codex App Server is missing required methods",
                details={
                    "missing_methods": list(self.missing_required_methods),
                },
            )
        if not (
            self.restricted_read_sandbox and self.network_disabled_sandbox
        ):
            raise CodexSubscriptionError(
                "CODEX_SANDBOX_UNSUPPORTED",
                "The installed Codex App Server cannot restrict file reads "
                "to QwenPaw's isolated workspace",
                remediation=(
                    "Upgrade the official Codex CLI to a version with "
                    "restricted readable-root sandbox support."
                ),
            )


def build_restricted_sandbox_policy(
    capabilities: CodexCapabilities,
    temporary_cwd: str,
) -> dict[str, Any]:
    """Build the only App Server sandbox policy accepted by QwenPaw."""

    if not (
        capabilities.restricted_read_sandbox
        and capabilities.network_disabled_sandbox
    ):
        raise CodexSubscriptionError(
            "CODEX_SANDBOX_UNSUPPORTED",
            "Codex restricted readable-root sandbox support is required",
        )
    path = Path(temporary_cwd)
    if not path.is_absolute():
        raise CodexSubscriptionError(
            "CODEX_PROTOCOL_INCOMPATIBLE",
            "Codex isolated workspace path must be absolute",
        )
    return {
        "type": "readOnly",
        "networkAccess": False,
        "access": {
            "type": "restricted",
            "readableRoots": [str(path)],
        },
    }


async def detect_binary_capabilities(binary: str) -> CodexCapabilities:
    """Generate and structurally inspect schemas without authentication IO."""

    with tempfile.TemporaryDirectory(prefix="qwenpaw-codex-schema-") as tmp:
        output = Path(tmp)
        process = await asyncio.create_subprocess_exec(
            binary,
            "app-server",
            "generate-json-schema",
            "--experimental",
            "--out",
            str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            code = await asyncio.wait_for(process.wait(), 20.0)
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "Timed out while detecting Codex App Server capabilities",
            ) from None
        if code != 0:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "Codex App Server schema generation failed",
            )
        catalog = AppServerSchemaCatalog.from_directory(output)
        capabilities = CodexCapabilities.from_catalog(catalog)
        capabilities.validate_required_surface()
        return capabilities


def _index_envelope(document: Schema) -> dict[str, _SchemaEntry]:
    definitions = document.get("definitions")
    definitions = definitions if isinstance(definitions, dict) else {}
    indexed: dict[str, _SchemaEntry] = {}
    for variant in _branches(document, definitions):
        properties = variant.get("properties")
        if not isinstance(properties, dict):
            continue
        method_schema = properties.get("method")
        params_schema = properties.get("params")
        if not isinstance(method_schema, dict):
            continue
        if not isinstance(params_schema, dict):
            params_schema = {}
        methods = _enum_values(method_schema, definitions)
        for method in methods:
            if isinstance(method, str):
                indexed[method] = _SchemaEntry(params_schema, definitions)
    return indexed


def _index_responses(
    documents: dict[str, Schema],
) -> dict[str, _SchemaEntry]:
    indexed: dict[str, _SchemaEntry] = {}
    for method, document in documents.items():
        definitions = document.get("definitions")
        definitions = definitions if isinstance(definitions, dict) else {}
        indexed[method] = _SchemaEntry(document, definitions)
    return indexed


def _read_response_documents(
    directory: Path,
    filenames: dict[str, str],
) -> dict[str, Schema]:
    documents: dict[str, Schema] = {}
    for method, filename in filenames.items():
        path = directory / filename
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                f"Codex App Server generated invalid {filename}",
            ) from exc
        if not isinstance(value, dict):
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                f"Codex App Server generated invalid {filename}",
            )
        documents[method] = value
    return documents


def _normalize_path(path: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(path, str):
        return tuple(segment for segment in path.split(".") if segment)
    return tuple(path)


def _resolved_copy(entry: _SchemaEntry) -> Schema:
    return dict(_resolve(entry.schema, entry.definitions))


def _resolve(schema: Schema, definitions: Schema) -> Schema:
    current = schema
    seen: set[str] = set()
    while isinstance(current.get("$ref"), str):
        reference = current["$ref"]
        if reference in seen or not reference.startswith("#/definitions/"):
            break
        seen.add(reference)
        name = reference.rsplit("/", 1)[-1]
        target = definitions.get(name)
        if not isinstance(target, dict):
            break
        current = target
    return current


def _branches(schema: Schema, definitions: Schema) -> list[Schema]:
    resolved = _resolve(schema, definitions)
    branches = [resolved]
    for keyword in ("oneOf", "anyOf", "allOf"):
        values = resolved.get(keyword)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    branches.extend(_branches(value, definitions))
    return branches


def _nodes_at(entry: _SchemaEntry, path: tuple[str, ...]) -> list[Schema]:
    nodes = [entry.schema]
    for segment in path:
        next_nodes: list[Schema] = []
        for node in nodes:
            for branch in _branches(node, entry.definitions):
                properties = branch.get("properties")
                if not isinstance(properties, dict):
                    continue
                child = properties.get(segment)
                if isinstance(child, dict):
                    next_nodes.append(child)
        nodes = next_nodes
        if not nodes:
            break
    expanded: list[Schema] = []
    for node in nodes:
        expanded.extend(_branches(node, entry.definitions))
    return expanded


def _enum_values(schema: Schema, definitions: Schema) -> list[Any]:
    values: list[Any] = []
    for branch in _branches(schema, definitions):
        enum = branch.get("enum")
        if isinstance(enum, list):
            values.extend(enum)
        if "const" in branch:
            values.append(branch["const"])
    return values


def _node_supports_enum(
    schema: Schema,
    definitions: Schema,
    path: tuple[str, ...],
    value: Any,
) -> bool:
    entry = _SchemaEntry(schema, definitions)
    return any(
        value in _enum_values(node, definitions)
        for node in _nodes_at(entry, path)
    )


def _node_supports_type(
    schema: Schema,
    definitions: Schema,
    path: tuple[str, ...],
    expected: str,
) -> bool:
    entry = _SchemaEntry(schema, definitions)
    for node in _nodes_at(entry, path):
        for branch in _branches(node, definitions):
            value = branch.get("type")
            if value == expected or (
                isinstance(value, list) and expected in value
            ):
                return True
    return False


def _array_accepts_type(
    schema: Schema,
    definitions: Schema,
    expected: str,
) -> bool:
    for branch in _branches(schema, definitions):
        if branch.get("type") != "array":
            continue
        items = branch.get("items")
        if not isinstance(items, dict):
            continue
        for item in _branches(items, definitions):
            value = item.get("type")
            if value == expected or (
                isinstance(value, list) and expected in value
            ):
                return True
    return False
