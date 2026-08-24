# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Lone-surrogate (U+D800-U+DFFF) safety when persisting sessions (#4238)."""

from pathlib import Path

import pytest

from vikingbot.config.schema import SessionKey
from vikingbot.session.manager import SessionManager


@pytest.mark.asyncio
async def test_save_session_with_surrogate_in_message_does_not_crash(tmp_path: Path):
    manager = SessionManager(bot_data_path=tmp_path)
    key = SessionKey(type="test", channel_id="channel", chat_id="chat")
    session = manager.get_or_create(key)
    session.add_message("tool", "listed viking://resources/\ud800bad_name")

    await manager.save(session)

    persisted = manager._load(key)
    assert persisted is not None
    content = persisted.messages[0]["content"]
    assert "\ud800" not in content
    assert "�" in content


@pytest.mark.asyncio
async def test_save_session_with_surrogate_in_metadata_does_not_crash(tmp_path: Path):
    manager = SessionManager(bot_data_path=tmp_path)
    key = SessionKey(type="test", channel_id="channel", chat_id="chat")
    session = manager.get_or_create(key)
    session.metadata["note"] = "file \udfff found"
    session.add_message("user", "hello")

    await manager.save(session)

    persisted = manager._load(key)
    assert persisted is not None
    assert "\udfff" not in persisted.metadata["note"]


@pytest.mark.asyncio
async def test_save_session_roundtrip_preserves_valid_non_bmp(tmp_path: Path):
    manager = SessionManager(bot_data_path=tmp_path)
    key = SessionKey(type="test", channel_id="channel", chat_id="chat")
    session = manager.get_or_create(key)
    session.add_message("user", "wave \U0001F600")

    await manager.save(session)

    persisted = manager._load(key)
    assert persisted.messages[0]["content"] == "wave \U0001F600"
