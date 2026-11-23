# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Outbound image delivery is confined to the bot's image directory."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.base import BaseChannel
from vikingbot.config.schema import SessionKey, TelegramChannelConfig
from vikingbot.utils import helpers


class _Channel(BaseChannel):
    """Minimal concrete channel exposing _parse_data_uri."""

    async def send(self, msg):  # pragma: no cover - abstract
        raise NotImplementedError

    async def start(self):  # pragma: no cover - abstract
        raise NotImplementedError

    async def stop(self):  # pragma: no cover - abstract
        raise NotImplementedError


@pytest.fixture
def images_dir(tmp_path: Path, monkeypatch) -> Path:
    images = tmp_path / "data" / "images"
    images.mkdir(parents=True)
    (images / "cat.png").write_bytes(b"PNG-fake-bytes")
    outside = tmp_path / "outside.png"
    outside.touch()
    (images / "linked.png").symlink_to(outside)
    monkeypatch.setattr(helpers, "_bot_data_path", images.parent)
    return images


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename, error",
    [
        ("../outside.png", "bare filename"),
        ("/outside.png", "bare filename"),
        ("sub/cat.png", "bare filename"),
        (r"sub\cat.png", "bare filename"),
        (".hidden", "bare filename"),
        ("", "bare filename"),
        ("linked.png", "outside the image directory"),
    ],
)
async def test_send_uri_rejects_unscoped_files(images_dir, filename, error):
    channel = _Channel.__new__(_Channel)
    with pytest.raises(ValueError, match=error):
        await channel._parse_data_uri(f"send://{filename}")


@pytest.mark.asyncio
async def test_telegram_sends_only_explicit_scoped_image_references(images_dir):
    pytest.importorskip("telegram")
    from vikingbot.channels.telegram import TelegramChannel

    (images_dir / "diagram.png").write_bytes(b"PNG-diagram")
    (images_dir / "mentioned.png").write_bytes(b"PNG-text-only")
    bot = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())
    channel = TelegramChannel(TelegramChannelConfig(), MessageBus())
    channel._app = SimpleNamespace(bot=bot)
    msg = OutboundMessage(
        session_key=SessionKey(type="telegram", channel_id="bot", chat_id="123"),
        content=(
            "See mentioned.png, ![link](linked.png), ![cat](cat.png), `cat.png`, "
            "![outside](../outside.png), and `diagram.png`."
        ),
    )

    await channel.send(msg)

    assert [
        (call.kwargs["chat_id"], call.kwargs["photo"].getvalue())
        for call in bot.send_photo.await_args_list
    ] == [(123, b"PNG-fake-bytes"), (123, b"PNG-diagram")]
    bot.send_message.assert_awaited_once()
    assert "See mentioned.png" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_send_uri_reads_managed_image_and_reports_missing_file(images_dir):
    channel = _Channel.__new__(_Channel)
    assert await channel._parse_data_uri("send://cat.png") == (False, b"PNG-fake-bytes")
    with pytest.raises(FileNotFoundError):
        await channel._parse_data_uri("send://nope.png")
