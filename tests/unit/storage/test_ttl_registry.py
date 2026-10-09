# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cross-worker visibility of the coarse account TTL marker."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import (
    AGFSNetworkError,
    AGFSTimeoutError,
)
from openviking.storage.ttl_registry import TTLRegistry
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs


@pytest.mark.asyncio
async def test_account_marker_becomes_visible_across_workers(binding_fs, monkeypatch):
    agfs = binding_fs._async_agfs
    original_stat = agfs.stat

    async def stat(path, *, bypass_cache=False):
        if not bypass_cache:
            raise FileNotFoundError("cached marker miss")
        return await original_stat(path, bypass_cache=True)

    observed = AsyncMock(side_effect=stat)
    monkeypatch.setattr(agfs, "stat", observed)
    reader = TTLRegistry(agfs)
    writer = TTLRegistry(agfs)
    assert await reader.account_may_have_records("acct") is False
    await writer.mark_account("acct")
    assert await reader.account_may_have_records("acct") is True
    assert await reader.account_may_have_records("acct") is True
    assert observed.await_count == 2  # Recheck absence; cache confirmed presence.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("backend unavailable"),
        AGFSNetworkError("endpoint not found"),
        AGFSTimeoutError("backend not found before timeout"),
    ],
)
async def test_marker_inspection_fails_open_on_storage_error(error):
    agfs = SimpleNamespace(stat=AsyncMock(side_effect=error))
    assert await TTLRegistry(agfs).account_may_have_records("acct") is True
