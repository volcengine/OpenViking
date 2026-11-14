# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, Mock

import pytest

from openviking.message import ImagePart, Message, TextPart
from openviking.message.part import Part
from openviking.session.memory.vision_message_normalizer import (
    replace_image_parts_with_descriptions,
)


@pytest.mark.parametrize(
    "url",
    [
        "/srv/agent-cache/images/sample.jpg",
        "images/sample.jpg",
        r"C:\images\sample.jpg",
        "file:///tmp/sample.jpg",
        "ftp://example.com/sample.jpg",
        "https:///sample.jpg",
        "data:text/plain;base64,aGVsbG8=",
    ],
)
@pytest.mark.parametrize("with_text", [False, True])
async def test_unsupported_image_source_never_reaches_vlm(url, with_text):
    parts: list[Part] = [TextPart(text="Keep this fact.")] if with_text else []
    # Include a valid image to ensure we do not describe only part of a message.
    parts += [ImagePart(url="https://example.com/valid.png"), ImagePart(url=url)]
    message = Message(id="m1", role="user", peer_id="alice", parts=parts)
    vlm = Mock(get_vision_completion_async=AsyncMock(return_value="Invented description"))
    logger = Mock()

    result = await replace_image_parts_with_descriptions(
        [message], get_vlm=lambda: vlm, logger=logger
    )

    vlm.get_vision_completion_async.assert_not_awaited()
    logger.warning.assert_called_once()
    assert "HTTP(S) URL or an image data URI" in str(logger.warning.call_args)
    if with_text:
        assert len(result) == 1
        assert result[0].parts == [TextPart(text="Keep this fact.")]
        assert (result[0].id, result[0].role, result[0].peer_id, result[0].created_at) == (
            message.id,
            message.role,
            message.peer_id,
            message.created_at,
        )
    else:
        assert result == []
    assert message.parts == parts


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/sample.jpg",
        "https://example.com/sample.jpg",
        "data:image/png;base64,iVBORw0KGgo=",
    ],
)
async def test_supported_image_source_and_detail_are_preserved(url):
    message = Message(id="m1", role="user", parts=[ImagePart(url=url, detail="high")])
    vlm = Mock(get_vision_completion_async=AsyncMock(return_value="Visible scene"))

    result = await replace_image_parts_with_descriptions([message], get_vlm=lambda: vlm)

    vlm.get_vision_completion_async.assert_awaited_once()
    content = vlm.get_vision_completion_async.call_args.kwargs["messages"][0]["content"]
    assert content[-1] == {"type": "image_url", "image_url": {"url": url, "detail": "high"}}
    assert result[0].parts == [TextPart(text="[Image description]: Visible scene")]
