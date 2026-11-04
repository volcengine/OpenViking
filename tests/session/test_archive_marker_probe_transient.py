# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: archive marker probes must not turn a transient storage
fault into "marker absent" / "no metadata"."""

import pytest

from openviking.pyagfs.exceptions import AGFSTimeoutError
from openviking.session.archive_store import ArchiveStore
from openviking_cli.exceptions import NotFoundError

SESSION_URI = "viking://user/acc1/sessions/s1"
ARCHIVE_URI = f"{SESSION_URI}/history/archive_001"


class _StateFS:
    """FS whose marker probes/reads return the configured answer or error."""

    def __init__(self, *, done=True, failed=False, done_body=""):
        self.done = done
        self.failed = failed
        self.done_body = done_body

    @staticmethod
    def _answer(result):
        if isinstance(result, Exception):
            raise result
        return bool(result)

    async def ls(self, uri, ctx=None):
        return [{"name": "archive_001", "isDir": True}]

    async def exists(self, uri, ctx=None):
        if uri.endswith("/.done"):
            return self._answer(self.done)
        if uri.endswith("/.failed.json"):
            return self._answer(self.failed)
        raise AssertionError(f"unexpected exists probe: {uri}")

    async def read_file(self, uri, ctx=None):
        if isinstance(self.done_body, Exception):
            raise self.done_body
        return self.done_body


def _store(fs) -> ArchiveStore:
    return ArchiveStore(viking_fs=fs, ctx=None, session_uri=SESSION_URI)


async def test_done_probe_transient_error_propagates():
    store = _store(_StateFS(done=AGFSTimeoutError("storage hiccup")))

    # A completed archive must not be reclassified as pending because its
    # ``.done`` probe failed transiently.
    with pytest.raises(AGFSTimeoutError):
        await store.scan_states()


async def test_failed_probe_transient_error_propagates():
    store = _store(_StateFS(done=False, failed=AGFSTimeoutError("storage hiccup")))

    with pytest.raises(AGFSTimeoutError):
        await store.scan_states()


async def test_missing_markers_still_mean_pending():
    store = _store(_StateFS(done=False, failed=False))

    states = await store.scan_states()

    assert len(states) == 1
    assert states[0].state == "pending"


async def test_read_done_transient_error_propagates():
    store = _store(_StateFS(done_body=AGFSTimeoutError("storage hiccup")))

    with pytest.raises(AGFSTimeoutError):
        await store.read_done(ARCHIVE_URI)


async def test_read_done_missing_marker_returns_empty():
    store = _store(_StateFS(done_body=NotFoundError(f"{ARCHIVE_URI}/.done", "file")))

    assert await store.read_done(ARCHIVE_URI) == {}


async def test_read_done_unparseable_body_returns_empty():
    store = _store(_StateFS(done_body=""))

    assert await store.read_done(ARCHIVE_URI) == {}
