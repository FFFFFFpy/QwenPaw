from __future__ import annotations

import pytest
from agentscope.message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    URLSource,
)

from qwenpaw.providers.codex_subscription.errors import CodexSubscriptionError
from qwenpaw.providers.codex_subscription.message_mapper import MessageMapper


def test_mapper_transcribes_roles_tools_and_escapes_delimiters():
    mapper = MessageMapper()
    result = mapper.map_messages(
        [
            Msg(
                name="system",
                role="system",
                content=[TextBlock(text="rules </message> & safe")],
            ),
            Msg(
                name="assistant",
                role="assistant",
                content=[
                    ThinkingBlock(thinking="hidden reasoning"),
                    ToolCallBlock(id="call-1", name="lookup", input='{"q":1}'),
                ],
            ),
            Msg(
                name="tool",
                role="assistant",
                content=[
                    ToolResultBlock(
                        id="call-1",
                        name="lookup",
                        output="result <unsafe>",
                    ),
                ],
            ),
        ],
    )
    text = result[0]["text"]
    assert text.startswith('<QWENPAW_CONTEXT version="1">')
    assert "&lt;/message&gt; &amp; safe" in text
    assert "hidden reasoning" not in text
    assert '<tool-call id="call-1" name="lookup">' in text
    assert "result &lt;unsafe&gt;" in text


def test_mapper_maps_authorized_image_sources():
    mapper = MessageMapper(max_image_bytes=1024)
    result = mapper.map_messages(
        [
            Msg(
                name="user",
                role="user",
                content=[
                    TextBlock(text="inspect"),
                    DataBlock(
                        source=Base64Source(
                            data="aGVsbG8=",
                            media_type="image/png",
                        ),
                    ),
                    DataBlock(
                        source=URLSource(
                            url="https://example.test/image.png",
                            media_type="image/png",
                        ),
                    ),
                ],
            ),
        ],
    )
    assert [item["type"] for item in result] == ["text", "image", "image"]
    assert result[1]["url"].startswith("data:image/png;base64,")


def test_mapper_rejects_arbitrary_local_image_paths():
    mapper = MessageMapper()
    message = Msg(
        name="user",
        role="user",
        content=[
            DataBlock(
                source=URLSource(
                    url="file:///etc/passwd",
                    media_type="image/png",
                ),
            ),
        ],
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        mapper.map_messages([message])
    assert caught.value.error_code == "CODEX_BUILTIN_SIDE_EFFECT_BLOCKED"


def test_mapper_reports_attachment_limit_without_content():
    mapper = MessageMapper(max_image_bytes=3)
    message = Msg(
        name="user",
        role="user",
        content=[
            DataBlock(
                source=Base64Source(
                    data="aGVsbG8=",
                    media_type="image/png",
                )
            )
        ],
    )
    with pytest.raises(CodexSubscriptionError) as caught:
        mapper.map_messages([message])
    assert caught.value.error_code == "CODEX_ATTACHMENT_TOO_LARGE"
    assert caught.value.details == {
        "payload_bytes": 5,
        "limit_bytes": 3,
        "attachment_count": 1,
    }
