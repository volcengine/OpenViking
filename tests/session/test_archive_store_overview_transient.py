# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: a transient overview read failure must not downgrade a
completed archive to ``failed``."""

import json

import pytest

from openviking.pyagfs.exceptions import AGFSTimeoutError
from openviking.session.archive_store import ArchiveStore
from openviking_cli.exceptions import NotFoundError

SESSION_URI = "viking://user/acc1/sessions/s1"
ARCHIVE_URI = f"{SESSION_URI}/history/archive_001"
DONE_URI = f"{ARCHIVE_URI}/.done"
OVERVIEW_URI = f"{ARCHIVE_URI}/.overview.md"


class _OverviewFS:
    """FS where ``.done`` exists and the overview read raises ``error``."""

    def __init__(self, error: Exception):
        self._error = error
        self._files = {DONE_URI: json.dumps({"enable_working_memory": True})}

    async def ls(self, uri, ctx=None):
        return [{"name": "archive_001", "isDir": True}]

    async def exists(self, uri, ctx=None):
        if uri == DONE_URI:
            return True
        if uri.endswith("/.failed.json"):
            return False
        raise AssertionError(f"unexpected exists probe: {uri}")

    async def read_file(self, uri, ctx=None):
        if uri == OVERVIEW_URI:
            raise self._error
        return self._files[uri]


def _store(fs) -> ArchiveStore:
    return ArchiveStore(viking_fs=fs, ctx=None, session_uri=SESSION_URI)


async def test_read_overview_transient_error_propagates():
    store = _store(_OverviewFS(AGFSTimeoutError("storage hiccup")))

    with pytest.raises(AGFSTimeoutError):
        await store.read_overview(ARCHIVE_URI)


async def test_read_overview_missing_file_is_empty():
    store = _store(_OverviewFS(NotFoundError(OVERVIEW_URI, "file")))

    assert await store.read_overview(ARCHIVE_URI) == ""


async def test_scan_states_does_not_reclassify_on_transient_overview_error():
    store = _store(_OverviewFS(AGFSTimeoutError("storage hiccup")))

    # Reporting a completed archive as ``failed`` removes it from
    # ``completed_refs``; Working Memory and cumulative checkpoints would then
    # silently skip it.
    with pytest.raises(AGFSTimeoutError):
        await store.scan_states()


async def test_scan_states_keeps_missing_overview_as_failed():
    store = _store(_OverviewFS(NotFoundError(OVERVIEW_URI, "file")))

    states = await store.scan_states()

    assert len(states) == 1
    assert states[0].state == "failed"
    assert states[0].failed["stage"] == "archive_overview"
