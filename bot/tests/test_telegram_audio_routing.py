# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for Telegram voice-note and audio-file routing."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.telegram import TelegramChannel
from vikingbot.config.schema import TelegramChannelConfig


class _TelegramFile:
    async def download_to_drive(self, path):
        Path(path).write_bytes(b"audio")


class _TelegramBot:
    async def get_file(self, file_id):
        return _TelegramFile()


@pytest.mark.parametrize(
    ("media_type", "mime_type", "expected_calls"),
    [
        pytest.param("voice", "audio/ogg", 1, id="voice-note"),
        pytest.param("audio", "audio/mpeg", 0, id="audio-file"),
    ],
)
async def test_telegram_transcribes_voice_notes_only(
    tmp_path, monkeypatch, media_type, mime_type, expected_calls
):
    transcribed = []
    transcriber_keys = []

    class FakeTranscriber:
        def __init__(self, api_key=None):
            transcriber_keys.append(api_key)

        async def transcribe(self, file_path):
            transcribed.append(file_path)
            return "spoken words"

    monkeypatch.setattr(
        "vikingbot.providers.transcription.GroqTranscriptionProvider", FakeTranscriber
    )

    bus = MessageBus()
    channel = TelegramChannel(
        TelegramChannelConfig(token="123:token", groq_api_key="groq-key"),
        bus,
        workspace_path=tmp_path,
    )
    channel._app = SimpleNamespace(bot=_TelegramBot())
    monkeypatch.setattr(channel, "_start_typing", lambda _chat_id: None)

    media = SimpleNamespace(file_id=f"{media_type}-file-id", mime_type=mime_type)
    message = SimpleNamespace(
        text=None,
        caption=None,
        photo=None,
        voice=media if media_type == "voice" else None,
        audio=media if media_type == "audio" else None,
        document=None,
        chat_id=12345,
        message_id=67890,
        chat=SimpleNamespace(type="private"),
    )
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(
            id=24680,
            username="test-user",
            full_name="Test User",
            first_name="Test",
        ),
    )

    await channel._on_message(update, None)

    inbound = await bus.consume_inbound()
    suffix = ".ogg" if media_type == "voice" else ".mp3"
    media_path = tmp_path / "media" / f"{media.file_id[:16]}{suffix}"
    expected_content = (
        "[transcription: spoken words]" if media_type == "voice" else f"[audio: {media_path}]"
    )

    assert transcriber_keys == ["groq-key"] * expected_calls
    assert transcribed == [media_path] * expected_calls
    assert inbound.content == expected_content
    assert inbound.media == [str(media_path)]
    assert media_path.read_bytes() == b"audio"
