# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for refusing to overwrite corrupt bot state."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.config import loader  # noqa: E402
from vikingbot.config.schema import SessionKey  # noqa: E402
from vikingbot.cron.service import CronService  # noqa: E402
from vikingbot.session.manager import Session, SessionManager  # noqa: E402


@pytest.mark.parametrize(
    "contents", ["{invalid", '{"bot":{"gateway":{"port":"not-a-port"}}}']
)
def test_invalid_existing_config_raises_with_path(monkeypatch, tmp_path, contents):
    path = tmp_path / "ov.conf"
    path.write_text(contents)
    monkeypatch.setattr(loader, "CONFIG_PATH", path)

    with pytest.raises(ValueError, match=str(path)):
        loader.load_config()


@pytest.mark.asyncio
async def test_corrupt_session_is_not_overwritten(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    key = SessionKey(type="cli", channel_id="default", chat_id="corrupt")
    path = manager._get_session_path(key)
    corrupt_bytes = b'{"_type":"metadata"}\n{invalid\n'
    path.write_bytes(corrupt_bytes)

    with pytest.raises(RuntimeError, match=str(path)):
        manager.get_or_create(key)
    with pytest.raises(RuntimeError, match=str(path)):
        await manager.save(Session(key=key))

    assert path.read_bytes() == corrupt_bytes


@pytest.mark.asyncio
async def test_corrupt_cron_store_is_not_overwritten(tmp_path):
    path = tmp_path / "jobs.json"
    corrupt_bytes = b'{"jobs": [invalid]}'
    path.write_bytes(corrupt_bytes)
    service = CronService(path)

    with pytest.raises(RuntimeError, match=str(path)):
        await service.start()

    assert not service._running
    assert path.read_bytes() == corrupt_bytes
