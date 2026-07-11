import asyncio
import importlib
import json

import pytest
from agentscope.message import DataBlock, TextBlock

from qwenpaw.agents.tools import discover_builtin_tool_funcs
from qwenpaw.agents.tools import image_generate as image_tool
from qwenpaw.agents.tools import image_generate as exported_image_generate
from qwenpaw.config.context import (
    set_current_session_id,
    set_current_workspace_dir,
)
from qwenpaw.providers.codex_subscription.image_generation import (
    GeneratedImage,
)

image_module = importlib.import_module("qwenpaw.agents.tools.image_generate")

PNG = b"\x89PNG\r\n\x1a\n" b"test-raster-payload"


class FakeService:
    def __init__(self):
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        return [GeneratedImage(PNG, "image/png", "png", "refined")]


@pytest.fixture(autouse=True)
def reset_image_tasks(tmp_path, monkeypatch):
    image_module._tasks.clear()
    image_module._fingerprints.clear()
    fake = FakeService()
    monkeypatch.setattr(image_module, "_service", fake)
    monkeypatch.setattr(image_module, "SECRET_DIR", tmp_path / "secrets")
    set_current_workspace_dir(tmp_path)
    set_current_session_id("session/one")
    yield fake
    set_current_workspace_dir(None)
    set_current_session_id(None)


@pytest.mark.asyncio
async def test_tool_saves_attachment_without_base64_history(reset_image_tasks):
    result = await image_tool(prompt="draw a cat", filename="../cat.png")
    data = next(
        block for block in result.content if isinstance(block, DataBlock)
    )
    text = next(
        block for block in result.content if isinstance(block, TextBlock)
    )
    payload = json.loads(text.text)
    path = image_module.Path(payload["files"][0])
    assert path.exists()
    assert path.read_bytes() == PNG
    assert "resources/sessions/session-one/images" in str(path)
    assert data.source.media_type == "image/png"
    assert "base64" not in text.text.lower()
    assert "../" not in path.name


@pytest.mark.asyncio
async def test_identical_request_is_deduplicated(reset_image_tasks):
    first = await image_tool(prompt="draw a cat")
    second = await image_tool(prompt="draw a cat")
    assert reset_image_tasks.calls == 1
    payload = json.loads(
        next(
            block for block in second.content if isinstance(block, TextBlock)
        ).text
    )
    assert payload["reused"] is True
    assert len(first.content) == len(second.content)


@pytest.mark.asyncio
async def test_status_list_and_edit_validation(reset_image_tasks):
    generated = await image_tool(prompt="draw a cat")
    task_id = json.loads(
        next(
            block
            for block in generated.content
            if isinstance(block, TextBlock)
        ).text
    )["task_id"]
    status = await image_tool(action="status", task_id=task_id)
    assert json.loads(status.content[0].text)["status"] == "completed"
    listed = await image_tool(action="list")
    assert json.loads(listed.content[0].text)[0]["task_id"] == task_id
    invalid = await image_tool(action="edit", prompt="change it")
    assert json.loads(invalid.content[0].text)["ok"] is False
    invalid_options = await image_tool(prompt="draw", size="999x999", count=5)
    assert json.loads(invalid_options.content[0].text)["ok"] is False


@pytest.mark.asyncio
async def test_cancel_running_task(monkeypatch):
    started = asyncio.Event()

    class SlowService:
        async def generate(self, **kwargs):
            started.set()
            await asyncio.Event().wait()
            return []

    monkeypatch.setattr(image_module, "_service", SlowService())
    call = asyncio.create_task(image_tool(prompt="draw slowly"))
    await started.wait()
    task_id = next(iter(image_module._tasks))
    cancelled = await image_tool(action="cancel", task_id=task_id)
    assert json.loads(cancelled.content[0].text)["status"] == "cancelled"
    with pytest.raises(asyncio.CancelledError):
        await call


def test_tool_is_registered_with_mandatory_raster_description():
    funcs = {func.__name__: func for func in discover_builtin_tool_funcs()}
    assert funcs["image_generate"] is exported_image_generate
    descriptor = getattr(exported_image_generate, "_tool_descriptor")
    assert "普通图片生成请求必须调用 image_generate" in descriptor.description
    assert "SVG" in descriptor.description
