# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for Telegram inbound media storage."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from vikingbot.channels.telegram import TelegramChannel


@pytest.mark.asyncio
async def test_distinct_file_ids_with_same_prefix_keep_both_payloads(tmp_path):
    payloads = {
        "AAAAAAAAAAAAAAAA-file-one": b"first telegram document",
        "AAAAAAAAAAAAAAAA-file-two": b"second telegram document",
    }

    class Download:
        def __init__(self, payload: bytes):
            self.payload = payload

        async def download_to_drive(self, path: str) -> None:
            Path(path).write_bytes(self.payload)

    class Bot:
        async def get_file(self, file_id: str) -> Download:
            return Download(payloads[file_id])

    class Channel:
        workspace_path = tmp_path
        _app = SimpleNamespace(bot=Bot())
        groq_api_key = None

        def __init__(self):
            self._chat_ids = {}
            self.published = []

        @staticmethod
        def _get_extension(media_type, mime_type):
            return ""

        @staticmethod
        def _start_typing(chat_id):
            return None

        async def _handle_message(self, **kwargs):
            self.published.append(kwargs)

    channel = Channel()
    user = SimpleNamespace(
        id=7,
        username="tester",
        full_name="Test User",
        first_name="Test",
    )

    for message_id, file_id in enumerate(payloads, start=1):
        message = SimpleNamespace(
            text=f"message {message_id}",
            caption=None,
            photo=[],
            voice=None,
            audio=None,
            document=SimpleNamespace(file_id=file_id, mime_type=None),
            chat_id=99,
            message_id=message_id,
            chat=SimpleNamespace(type="private"),
        )
        update = SimpleNamespace(message=message, effective_user=user)
        await TelegramChannel._on_message(channel, update, None)

    paths = [Path(entry["media"][0]) for entry in channel.published]
    assert len(set(paths)) == 2
    assert [path.read_bytes() for path in paths] == list(payloads.values())
