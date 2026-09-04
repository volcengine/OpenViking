# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""send:// outbound images are delivered on Telegram, Discord, and Slack."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.bus.events import OutboundMessage  # noqa: E402
from vikingbot.bus.queue import MessageBus  # noqa: E402
from vikingbot.channels.discord import DiscordChannel  # noqa: E402
from vikingbot.channels.slack import SlackChannel  # noqa: E402
from vikingbot.channels.telegram import TelegramChannel  # noqa: E402
from vikingbot.config.schema import (  # noqa: E402
    DiscordChannelConfig,
    SessionKey,
    SlackChannelConfig,
    TelegramChannelConfig,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _write_image(tmp_path: Path, name: str = "pic.png") -> None:
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    (images / name).write_bytes(_PNG)


@pytest.mark.asyncio
async def test_telegram_sends_send_uri_as_photo(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.channels.base.get_data_path", lambda: tmp_path)
    _write_image(tmp_path)
    sent = []

    class FakeBot:
        async def send_photo(self, chat_id, photo):
            sent.append(("photo", chat_id, photo.getvalue()))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    channel = TelegramChannel(TelegramChannelConfig(token="1:x"), MessageBus())
    channel._app = SimpleNamespace(bot=FakeBot())

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="telegram", channel_id="1", chat_id="99"),
            content="send://pic.png",
        )
    )

    assert sent == [("photo", 99, _PNG)]


@pytest.mark.asyncio
async def test_discord_sends_send_uri_as_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.channels.base.get_data_path", lambda: tmp_path)
    _write_image(tmp_path)
    calls = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    class FakeHttp:
        async def post(self, url, headers=None, json=None, data=None, files=None):
            calls.append({"json": json, "data": data, "files": files})
            return FakeResponse()

    channel = DiscordChannel(DiscordChannelConfig(token="tok"), MessageBus())
    channel._http = FakeHttp()

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="discord", channel_id="tok", chat_id="chan"),
            content="send://pic.png",
        )
    )

    assert calls[0]["json"] is None
    assert calls[0]["files"]["files[0]"] == ("pic.png", _PNG)


@pytest.mark.asyncio
async def test_slack_uploads_send_uri_and_skips_empty_text(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.channels.base.get_data_path", lambda: tmp_path)
    _write_image(tmp_path)
    uploads = []
    messages = []

    class FakeWeb:
        async def files_upload_v2(self, **kwargs):
            uploads.append(kwargs)

        async def chat_postMessage(self, **kwargs):
            messages.append(kwargs)

    channel = SlackChannel(SlackChannelConfig(bot_token="xoxb"), MessageBus())
    channel._web_client = FakeWeb()

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="slack", channel_id="xoxb", chat_id="C1"),
            content="send://pic.png",
        )
    )

    assert uploads[0]["channel"] == "C1"
    assert uploads[0]["filename"] == "pic.png"
    assert uploads[0]["file"].getvalue() == _PNG
    assert messages == []


@pytest.mark.asyncio
async def test_telegram_sends_markdown_image_send_uri(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.channels.base.get_data_path", lambda: tmp_path)
    _write_image(tmp_path)
    sent = []

    class FakeBot:
        async def send_photo(self, chat_id, photo):
            sent.append(("photo", chat_id, photo.getvalue()))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    channel = TelegramChannel(TelegramChannelConfig(token="1:x"), MessageBus())
    channel._app = SimpleNamespace(bot=FakeBot())

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="telegram", channel_id="1", chat_id="99"),
            content="![pic](send://pic.png)",
        )
    )

    assert sent == [("photo", 99, _PNG)]


@pytest.mark.asyncio
async def test_markdown_link_send_uri_is_fully_stripped(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.channels.base.get_data_path", lambda: tmp_path)
    _write_image(tmp_path)
    sent = []

    class FakeBot:
        async def send_photo(self, chat_id, photo):
            sent.append(("photo", chat_id, photo.getvalue()))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    channel = TelegramChannel(TelegramChannelConfig(token="1:x"), MessageBus())
    channel._app = SimpleNamespace(bot=FakeBot())

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="telegram", channel_id="1", chat_id="99"),
            content="see [diagram](send://pic.png) please",
        )
    )

    assert sent[0] == ("photo", 99, _PNG)
    text = sent[1][1]["text"]
    assert "send://" not in text
    assert "[" not in text
    assert "]" not in text
    assert "see" in text and "please" in text


@pytest.mark.asyncio
async def test_slack_uploads_multiple_send_uris_in_one_call(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.channels.base.get_data_path", lambda: tmp_path)
    _write_image(tmp_path, "a.png")
    _write_image(tmp_path, "b.png")
    uploads = []

    class FakeWeb:
        async def files_upload_v2(self, **kwargs):
            uploads.append(kwargs)

        async def chat_postMessage(self, **kwargs):
            raise AssertionError("text should be empty after image URIs")

    channel = SlackChannel(SlackChannelConfig(bot_token="xoxb"), MessageBus())
    channel._web_client = FakeWeb()

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="slack", channel_id="xoxb", chat_id="C1"),
            content="send://a.png\nsend://b.png",
        )
    )

    assert len(uploads) == 1
    uploaded = uploads[0]["file_uploads"]
    assert [item["filename"] for item in uploaded] == ["a.png", "b.png"]
    assert [item["file"].getvalue() for item in uploaded] == [_PNG, _PNG]
