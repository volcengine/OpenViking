# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: archive structural probes must not treat transient
read failures as "absent"."""

from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSTimeoutError
from openviking.session.archive_store import ArchiveStore
from openviking_cli.exceptions import NotFoundError

SESSION_URI = "viking://user/acc1/sessions/s1"
ARCHIVE_URI = f"{SESSION_URI}/history/archive_001"


class _FailingProbeFS:
    """FS surface whose probes raise the injected error."""

    def __init__(self, error: Exception):
        self._error = error
        self.ls = AsyncMock(side_effect=error)
        self.exists = AsyncMock(side_effect=error)
        self.read_file = AsyncMock(side_effect=error)


def _store(fs) -> ArchiveStore:
    return ArchiveStore(viking_fs=fs, ctx=None, session_uri=SESSION_URI)


async def test_file_exists_transient_error_propagates():
    store = _store(_FailingProbeFS(AGFSTimeoutError("storage hiccup")))

    # A transient probe failure must not read as "marker absent": Phase 1
    # recovery would treat the archive as recoverable when it is not.
    with pytest.raises(AGFSTimeoutError):
        await store.file_exists(ARCHIVE_URI, ".failed.json")


async def test_file_exists_not_found_is_false():
    store = _store(_FailingProbeFS(NotFoundError(f"{ARCHIVE_URI}/.failed.json", "file")))

    assert await store.file_exists(ARCHIVE_URI, ".failed.json") is False


async def test_read_meta_transient_error_propagates():
    store = _store(_FailingProbeFS(AGFSTimeoutError("storage hiccup")))

    # A transient read failure must not read as "no metadata", or the Phase 1
    # crash-recovery reconciliation is silently skipped.
    with pytest.raises(AGFSTimeoutError):
        await store.read_meta(ARCHIVE_URI)


async def test_read_meta_missing_returns_empty():
    store = _store(_FailingProbeFS(NotFoundError(f"{ARCHIVE_URI}/.meta.json", "file")))

    assert await store.read_meta(ARCHIVE_URI) == {}


async def test_read_meta_unparseable_body_returns_empty():
    fs = _FailingProbeFS(AGFSTimeoutError("unused"))
    fs.read_file = AsyncMock(return_value="")
    store = _store(fs)

    # An interrupted write can leave an empty body; that stays tolerated.
    assert await store.read_meta(ARCHIVE_URI) == {}
