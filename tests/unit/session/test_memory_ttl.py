# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timedelta, timezone

import pytest

from openviking.session.memory.dataclass import MemoryData
from openviking_cli.utils.config.memory_config import MemoryConfig


def test_memory_data_is_expired_with_no_expires_at() -> None:
    memory = MemoryData(memory_type="events", fields={"k": "v"})
    assert memory.is_expired() is False


def test_memory_data_is_expired_with_future_expires_at() -> None:
    memory = MemoryData(
        memory_type="events",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert memory.is_expired() is False


def test_memory_data_is_expired_with_past_expires_at() -> None:
    memory = MemoryData(
        memory_type="events",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    assert memory.is_expired() is True


def test_memory_config_parse_ttl() -> None:
    assert MemoryConfig.parse_ttl(None) is None
    assert MemoryConfig.parse_ttl("7d") == timedelta(days=7)
    assert MemoryConfig.parse_ttl("24h") == timedelta(hours=24)
    assert MemoryConfig.parse_ttl("30m") == timedelta(minutes=30)


@pytest.mark.parametrize("invalid_ttl", ["", "abc", "10x"])
def test_memory_config_parse_ttl_invalid(invalid_ttl: str) -> None:
    with pytest.raises(ValueError):
        MemoryConfig.parse_ttl(invalid_ttl)


def test_memory_data_backward_compatibility_without_expires_at() -> None:
    memory = MemoryData(memory_type="preferences", abstract="pref")
    dumped = memory.model_dump()
    assert "expires_at" in dumped
    assert dumped["expires_at"] is None
    assert memory.abstract == "pref"
