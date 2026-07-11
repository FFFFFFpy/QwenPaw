"""Built-in real raster image generation and editing tool."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentscope.message import DataBlock, TextBlock, ToolResultState, URLSource
from agentscope.tool import ToolChunk
from pydantic import ValidationError

from ...config.context import get_current_session_id, get_current_workspace_dir
from ...constant import SECRET_DIR, WORKING_DIR
from ...providers.codex_subscription.image_generation import (
    GeneratedImage,
    ImageGenerationService,
)
from ...providers.codex_subscription.errors import CodexSubscriptionError
from ...providers.codex_subscription.settings import (
    CodexSubscriptionSettings,
    ImageModelSettings,
)
from ...runtime.tool_registry import tool_descriptor
from ...tool_calls import cancellable_wait
from .file_io import _path_to_file_url


@dataclass
class _ImageTask:
    task_id: str
    session_id: str
    fingerprint: str
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    paths: list[Path] = field(default_factory=list)
    error: str | None = None
    task: asyncio.Task[list[GeneratedImage]] | None = None
    save_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_tasks: dict[str, _ImageTask] = {}
_fingerprints: dict[tuple[str, str], str] = {}
_registry_lock = asyncio.Lock()
_service: ImageGenerationService | None = None


def _get_service() -> ImageGenerationService:
    global _service
    if _service is None:
        _service = ImageGenerationService()
    return _service


def _safe_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned[:80] or fallback


def _resource_dir(workspace: Path, session_id: str) -> Path:
    return (
        workspace
        / "resources"
        / "sessions"
        / _safe_component(session_id, "default")
        / "images"
    )


def _task_payload(record: _ImageTask) -> dict[str, Any]:
    return {
        "task_id": record.task_id,
        "status": record.status,
        "files": [str(path) for path in record.paths],
        "error": record.error,
    }


def _text_result(payload: Any, *, error: bool = False) -> ToolChunk:
    return ToolChunk(
        is_last=True,
        state=ToolResultState.ERROR if error else ToolResultState.SUCCESS,
        content=[
            TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))
        ],
    )


def _media_result(record: _ImageTask, *, reused: bool = False) -> ToolChunk:
    blocks: list[Any] = []
    for path in record.paths:
        mime = (
            "image/jpeg"
            if path.suffix == ".jpeg"
            else f"image/{path.suffix[1:]}"
        )
        blocks.append(
            DataBlock(
                source=URLSource(
                    url=_path_to_file_url(str(path)), media_type=mime
                ),
                name=path.name,
            )
        )
    blocks.append(
        TextBlock(
            text=json.dumps(
                {**_task_payload(record), "reused": reused},
                ensure_ascii=False,
                indent=2,
            )
        )
    )
    return ToolChunk(
        is_last=True, state=ToolResultState.SUCCESS, content=blocks
    )


async def _save_images(
    record: _ImageTask,
    images: list[GeneratedImage],
    output_dir: Path,
    filename: str | None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = _safe_component(filename or "generated-image", "generated-image")
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    paths: list[Path] = []
    for index, image in enumerate(images, start=1):
        suffix = f"-{index}" if len(images) > 1 else ""
        path = (
            output_dir
            / f"{stem}-{record.task_id[:8]}{suffix}.{image.extension}"
        )
        temporary = path.with_suffix(path.suffix + ".tmp")
        await asyncio.to_thread(temporary.write_bytes, image.data)
        temporary.chmod(0o600)
        temporary.replace(path)
        paths.append(path)
    record.paths = paths


@tool_descriptor(
    requires_sandbox=("file_read", "file_write", "network"),
    async_execution=True,
    description=(
        "当用户要求创建、绘制、生成、渲染或编辑图片时，调用 image_generate。"
        "普通图片生成请求必须调用 image_generate。除非用户明确要求 SVG、"
        "矢量图、Mermaid、流程图、HTML Canvas 或 ASCII Art，否则不得用这些"
        "文本格式替代真实图片生成。"
    ),
)
async def image_generate(
    action: str = "generate",
    prompt: str = "",
    model: str = "gpt-image-2",
    image: str | None = None,
    images: list[str] | None = None,
    size: str | None = None,
    quality: str | None = None,
    output_format: str | None = None,
    background: str | None = None,
    count: int | None = None,
    filename: str | None = None,
    task_id: str | None = None,
) -> ToolChunk:
    """Generate or edit real raster images with GPT Image 2.

    Normal requests to create, draw, render, generate, or edit an image MUST
    call this tool. Unless the user explicitly requests SVG, vector graphics,
    Mermaid, a flowchart, HTML Canvas, or ASCII Art, never substitute those
    text formats for real image generation.

    Actions: ``generate``, ``edit``, ``status``, ``list``, and ``cancel``.
    Generated PNG/JPEG/WebP files are returned as media attachments.
    """
    action = action.lower().strip()
    session_id = get_current_session_id() or "default"
    workspace = Path(get_current_workspace_dir() or WORKING_DIR).resolve()

    if action == "list":
        return _text_result(
            [
                _task_payload(record)
                for record in _tasks.values()
                if record.session_id == session_id
            ]
        )
    if action in {"status", "cancel"}:
        record = _tasks.get(task_id or "")
        if record is None or record.session_id != session_id:
            return _text_result(
                {"ok": False, "error": "Image task was not found"},
                error=True,
            )
        if action == "cancel" and record.task and not record.task.done():
            record.task.cancel()
            record.status = "cancelled"
        return _text_result(_task_payload(record))
    if action not in {"generate", "edit"}:
        return _text_result(
            {"ok": False, "error": "Unsupported image action"}, error=True
        )
    if model != "gpt-image-2":
        return _text_result(
            {"ok": False, "error": "Only gpt-image-2 is supported"},
            error=True,
        )

    references = ([image] if image else []) + list(images or [])
    if action == "edit" and not references:
        return _text_result(
            {"ok": False, "error": "Image editing requires a reference"},
            error=True,
        )
    settings_path = SECRET_DIR / "codex_subscription" / "settings.json"
    settings = CodexSubscriptionSettings.load(settings_path).image_model(
        "gpt-image-2"
    )
    try:
        resolved = ImageModelSettings(
            size=size if size is not None else settings.size,
            quality=quality if quality is not None else settings.quality,
            output_format=(
                output_format
                if output_format is not None
                else settings.output_format
            ),
            background=(
                background if background is not None else settings.background
            ),
            count=count if count is not None else settings.count,
        )
    except ValidationError:
        return _text_result(
            {"ok": False, "error": "Invalid image generation options"},
            error=True,
        )
    options = resolved.model_dump()
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "action": action,
                "model": model,
                "prompt": prompt.strip(),
                "references": references,
                "filename": filename,
                **options,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()

    async with _registry_lock:
        existing_id = _fingerprints.get((session_id, fingerprint))
        existing = _tasks.get(existing_id or "")
        if (
            existing
            and existing.status == "completed"
            and all(path.exists() for path in existing.paths)
        ):
            return _media_result(existing, reused=True)
        if existing and existing.task and not existing.task.done():
            record = existing
        else:
            record = _ImageTask(
                task_id=uuid.uuid4().hex,
                session_id=session_id,
                fingerprint=fingerprint,
                status="running",
            )
            record.task = asyncio.create_task(
                _get_service().generate(
                    prompt=prompt,
                    references=references,
                    workspace=workspace,
                    **options,
                )
            )
            _tasks[record.task_id] = record
            _fingerprints[(session_id, fingerprint)] = record.task_id

    try:
        generated = await cancellable_wait(record.task, fallback_secs=600)
        async with record.save_lock:
            if record.status != "completed":
                await _save_images(
                    record,
                    generated,
                    _resource_dir(workspace, session_id),
                    filename,
                )
                record.status = "completed"
        return _media_result(record)
    except asyncio.CancelledError:
        record.status = "cancelled"
        raise
    except Exception as exc:  # stable tool boundary; never expose credentials
        record.status = "failed"
        record.error = (
            str(exc)[:500]
            if isinstance(exc, CodexSubscriptionError)
            else "Image generation failed"
        )
        return _text_result(_task_payload(record), error=True)
