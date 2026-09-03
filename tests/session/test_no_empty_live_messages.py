# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A fully archived session must not leave an empty messages.jsonl behind.

Committing with keep_recent_count=0 moves every message into
history/archive_NNN and used to rewrite the live file as an empty string, so
each session directory kept a 0-byte messages.jsonl forever. #3820 made that
file the materialization boundary for session-aware recall, so removing it has
to keep a committed session distinguishable from a half-created root.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.session.session import Session


class _FakeFS:
    """Records writes and removals, and answers stat from what exists."""

    def __init__(self, present=()):
        self.present = set(present)
        self.removed: list[str] = []
        self.written: dict[str, str] = {}

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.written[uri] = content
        self.present.add(uri)

    async def rm(self, uri, ctx=None, lease_ref=None, **kwargs):
        self.removed.append(uri)
        self.present.discard(uri)

    async def stat(self, uri, ctx=None):
        if uri not in self.present:
            raise FileNotFoundError(uri)
        return {"uri": uri}


def _session(fs) -> Session:
    session = Session.__new__(Session)
    session._viking_fs = fs
    session._session_uri = "viking://user/alice/sessions/s1"
    session.ctx = None
    session._messages = []
    session._stats = SimpleNamespace(total_turns=0)
    session._compression = SimpleNamespace(compression_index=1)
    return session


@pytest.mark.asyncio
async def test_writing_no_messages_removes_the_live_file():
    """The empty-file case is a removal, not a zero-byte write."""
    uri = "viking://user/alice/sessions/s1/messages.jsonl"
    fs = _FakeFS(present=[uri])

    await Session._write_to_agfs_async(_session(fs), [])

    assert fs.removed == [uri]
    assert uri not in fs.written


@pytest.mark.asyncio
async def test_a_committed_session_is_still_materialized():
    """#3820's boundary must survive: history/ is the second half of it."""
    root = "viking://user/alice/sessions/s1"
    fs = _FakeFS(present=[f"{root}/history"])

    assert await Session.is_materialized(_session(fs)) is True


@pytest.mark.asyncio
async def test_a_live_session_is_still_materialized():
    root = "viking://user/alice/sessions/s1"
    fs = _FakeFS(present=[f"{root}/messages.jsonl"])

    assert await Session.is_materialized(_session(fs)) is True


@pytest.mark.asyncio
async def test_a_half_created_root_is_not_materialized():
    """The case #3820 was written for: a root with neither file."""
    fs = _FakeFS(present=[])

    assert await Session.is_materialized(_session(fs)) is False


@pytest.mark.asyncio
async def test_a_storage_failure_is_not_swallowed_as_not_materialized():
    """Only not-found means 'absent'; anything else must surface."""

    class _BrokenFS(_FakeFS):
        async def stat(self, uri, ctx=None):
            raise RuntimeError("vector store unreachable")

    with pytest.raises(RuntimeError):
        await Session.is_materialized(_session(_BrokenFS()))
