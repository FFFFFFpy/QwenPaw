"""Capability detection from the installed App Server schema."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import tempfile

from .errors import CodexSubscriptionError


@dataclass(frozen=True)
class CodexCapabilities:
    browser_login: bool
    device_code_login: bool
    model_list: bool
    rate_limits: bool
    image_input: bool
    dynamic_tools: bool
    turn_interrupt: bool
    thread_archive_or_unsubscribe: bool
    builtin_tools_disable_mode: str | None
    schema_fingerprint: str = ""

    def model_dump(self) -> dict[str, bool | str | None]:
        return asdict(self)

    @classmethod
    def from_schema_text(cls, text: str) -> "CodexCapabilities":
        has = text.__contains__
        return cls(
            browser_login=has("account/login/start") and has("chatgpt"),
            device_code_login=has("chatgptDeviceCode"),
            model_list=has("model/list"),
            rate_limits=has("account/rateLimits/read"),
            image_input=has('"image"') or has("inputModalities"),
            dynamic_tools=has("dynamicTools") and has("item/tool/call"),
            turn_interrupt=has("turn/interrupt"),
            thread_archive_or_unsubscribe=(
                has("thread/unsubscribe") or has("thread/archive")
            ),
            builtin_tools_disable_mode=(
                "approval-never+sandbox-read-only"
                if has("approvalPolicy") and has("read-only")
                else None
            ),
            schema_fingerprint=hashlib.sha256(text.encode()).hexdigest(),
        )

    @classmethod
    def focused_contract(cls) -> "CodexCapabilities":
        """Capabilities used by the protocol fake, not by real detection."""

        return cls(
            browser_login=True,
            device_code_login=True,
            model_list=True,
            rate_limits=True,
            image_input=True,
            dynamic_tools=True,
            turn_interrupt=True,
            thread_archive_or_unsubscribe=True,
            builtin_tools_disable_mode="approval-never+sandbox-read-only",
            schema_fingerprint="fake-contract",
        )

    def validate_required_surface(self) -> None:
        missing = []
        if not self.browser_login:
            missing.append("account/login/start")
        if not self.model_list:
            missing.append("model/list")
        if not self.turn_interrupt:
            missing.append("turn/interrupt")
        if missing:
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "The installed Codex App Server is missing required methods",
                details={"missing_methods": missing},
            )


async def detect_binary_capabilities(binary: str) -> CodexCapabilities:
    """Generate and inspect the installed binary's schema without auth IO."""

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
        bundles = [
            output / "codex_app_server_protocol.v2.schemas.json",
            output / "codex_app_server_protocol.schemas.json",
        ]
        if any(not bundle.is_file() for bundle in bundles):
            raise CodexSubscriptionError(
                "CODEX_PROTOCOL_INCOMPATIBLE",
                "Codex App Server did not generate its protocol schemas",
            )
        capabilities = CodexCapabilities.from_schema_text(
            "\n".join(
                bundle.read_text(encoding="utf-8") for bundle in bundles
            ),
        )
        capabilities.validate_required_surface()
        return capabilities
