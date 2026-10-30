# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""DingTalk must deliver generated images through its native image API."""

import json
from unittest.mock import AsyncMock

import pytest
from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.dingtalk import DingTalkChannel
from vikingbot.config.schema import DingTalkChannelConfig, SessionKey

PNG = b"\x89PNG\r\n\x1a\nopenviking"
UPLOAD_URL = "https://oapi.dingtalk.com/media/upload"
SEND_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"


class _Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class _HTTP:
    def __init__(self, *, upload_error=False, image_error=False):
        self.calls = []
        self.upload_error = upload_error
        self.image_error = image_error

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == UPLOAD_URL:
            if self.upload_error:
                return _Response({"errcode": 40004, "errmsg": "invalid image"})
            return _Response({"errcode": 0, "errmsg": "ok", "media_id": "@media_1"})
        if kwargs["json"]["msgKey"] == "sampleImageMsg" and self.image_error:
            return _Response({"code": "send.failed"}, status_code=400)
        return _Response({"processQueryKey": "accepted"})


def _channel(http):
    config = DingTalkChannelConfig(client_id="robot-code", client_secret="unused")
    channel = DingTalkChannel(config, MessageBus())
    channel._http = http
    channel._get_access_token = AsyncMock(return_value="test-token")
    return channel


def _message(content):
    return OutboundMessage(
        session_key=SessionKey(
            type="dingtalk", channel_id="robot-code", chat_id="originating-staff-id"
        ),
        content=content,
    )


@pytest.fixture
def generated_image(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    (images / "result.png").write_bytes(PNG)
    monkeypatch.setattr("vikingbot.channels.base.get_data_path", lambda: tmp_path)


@pytest.mark.asyncio
async def test_dingtalk_delivers_generated_image_to_originating_user(generated_image):
    http = _HTTP()
    await _channel(http).send(_message("Generated result\nsend://result.png"))

    upload = next(kwargs for url, kwargs in http.calls if url == UPLOAD_URL)
    messages = [kwargs["json"] for url, kwargs in http.calls if url == SEND_URL]
    markdown = next(payload for payload in messages if payload["msgKey"] == "sampleMarkdown")
    image = next(payload for payload in messages if payload["msgKey"] == "sampleImageMsg")

    assert upload["params"] == {"access_token": "test-token", "type": "image"}
    assert upload["files"]["media"] == ("result.png", PNG, "image/png")
    assert json.loads(markdown["msgParam"])["text"] == "Generated result"
    assert image["userIds"] == ["originating-staff-id"]
    assert json.loads(image["msgParam"]) == {"photoURL": "@media_1"}


@pytest.mark.asyncio
async def test_dingtalk_image_only_skips_empty_markdown(generated_image):
    http = _HTTP()
    await _channel(http).send(_message("send://result.png"))

    messages = [kwargs["json"]["msgKey"] for url, kwargs in http.calls if url == SEND_URL]
    assert messages == ["sampleImageMsg"]


@pytest.mark.asyncio
async def test_dingtalk_preserves_text_without_generated_image():
    http = _HTTP()
    await _channel(http).send(_message("  text-only reply\n\n\n"))

    messages = [kwargs["json"] for url, kwargs in http.calls if url == SEND_URL]
    assert len(messages) == 1
    assert messages[0]["msgKey"] == "sampleMarkdown"
    assert json.loads(messages[0]["msgParam"])["text"] == "  text-only reply\n\n\n"


@pytest.mark.asyncio
async def test_dingtalk_rejects_unsafe_send_uri(tmp_path, monkeypatch):
    (tmp_path / "secret.png").write_bytes(PNG)
    (tmp_path / "images").mkdir()
    monkeypatch.setattr("vikingbot.channels.base.get_data_path", lambda: tmp_path)
    http = _HTTP()

    await _channel(http).send(_message("send://../secret.png"))

    assert all(url != UPLOAD_URL for url, _kwargs in http.calls)
    assert json.loads(http.calls[0][1]["json"]["msgParam"])["text"] == "send://../secret.png"


@pytest.mark.asyncio
async def test_dingtalk_rejects_image_over_20_mb():
    http = _HTTP()
    channel = _channel(http)
    channel._parse_data_uri = AsyncMock(
        return_value=(False, b"\x89PNG\r\n\x1a\n" + b"x" * (20 * 1024 * 1024))
    )

    await channel.send(_message("send://result.png"))

    assert all(url != UPLOAD_URL for url, _kwargs in http.calls)


@pytest.mark.asyncio
async def test_dingtalk_reports_upload_failure_in_chat(generated_image):
    http = _HTTP(upload_error=True)
    await _channel(http).send(_message("send://result.png"))

    messages = [kwargs["json"] for url, kwargs in http.calls if url == SEND_URL]
    assert [payload["msgKey"] for payload in messages] == ["sampleMarkdown"]
    assert json.loads(messages[0]["msgParam"])["text"] == "[image unavailable: result.png]"


@pytest.mark.asyncio
async def test_dingtalk_reports_image_send_failure_in_chat(generated_image):
    http = _HTTP(image_error=True)
    await _channel(http).send(_message("send://result.png"))

    messages = [kwargs["json"] for url, kwargs in http.calls if url == SEND_URL]
    assert [payload["msgKey"] for payload in messages] == [
        "sampleImageMsg",
        "sampleMarkdown",
    ]
    assert json.loads(messages[-1]["msgParam"])["text"] == "[image unavailable: result.png]"
