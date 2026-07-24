# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for Phase-2 archive replay tolerance (follow-up to #3417).

#3417 made ``Session._read_archive_messages`` strict: it now raises on a
missing/corrupt ``messages.jsonl`` instead of returning ``[]``. The read path
(``_get_uncovered_archive_messages``) gained not-found tolerance, but the
Phase-2 commit replay path (``_prepare_phase2_archive_messages``) did not.

A terminally-failed earlier archive whose ``messages.jsonl`` is missing would
therefore make every subsequent commit's Phase-2 replay raise, terminal-fail
the current archive, and permanently poison the session's memory extraction.
"""

from unittest.mock import AsyncMock

import pytest

from openviking.session.session import ArchiveState, Session


class _MemoryVikingFS:
    def __init__(self, files):
        self.files = files

    def _uri_to_path(self, uri, ctx=None):
        return "/local/session-1"

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        value = self.files[uri]
        if isinstance(value, Exception):
            raise value
        return value

    async def write_file(self, uri, content, ctx=None):
        self.files[uri] = content


def _session(files, states, monkeypatch):
    session_uri = "viking://user/sessions/session-1"
    session = Session(
        viking_fs=_MemoryVikingFS(files),
        session_id="session-1",
        session_uri=session_uri,
    )
    monkeypatch.setattr(session, "_scan_archive_states", AsyncMock(return_value=states))
    return session, session_uri


def _failed_then_pending(session_uri):
    """archive_001 = terminally failed (missing messages), archive_002 = current pending."""
    a1 = f"{session_uri}/history/archive_001"
    a2 = f"{session_uri}/history/archive_002"
    states = [
        ArchiveState(
            archive_id="archive_001",
            archive_uri=a1,
            index=1,
            state="failed",
            failed={"stage": "archive_read", "error": "no messages"},
        ),
        ArchiveState(
            archive_id="archive_002",
            archive_uri=a2,
            index=2,
            state="pending",
        ),
    ]
    return a1, a2, states


@pytest.mark.asyncio
async def test_phase2_replay_skips_failed_archive_with_missing_messages(monkeypatch):
    session_uri = "viking://user/sessions/session-1"
    a1, a2, states = _failed_then_pending(session_uri)
    # archive_001/messages.jsonl is absent -> not-found; archive_002 has no
    # replay reads of its own (current messages are passed in directly).
    session, _ = _session({}, states, monkeypatch)

    from openviking.message import Message, TextPart

    current = [Message(id="cur", role="user", parts=[TextPart("hi")])]

    combined, _start, _end, covered_failed, _steps = (
        await session._prepare_phase2_archive_messages(a2, current)
    )

    # The poisoned archive is skipped; the current commit still gets its own
    # messages and can complete Phase 2 instead of being terminal-failed.
    assert [m.id for m in combined] == ["cur"]
    # The skipped archive is still reported as covered_failed so the current
    # archive's .done marks it covered — clearing the poison permanently
    # instead of re-reading (and skipping) it on every future commit.
    assert covered_failed == ["archive_001"]

    # Asymmetry check: the read path already tolerates the exact same scenario.
    uncovered = await session._get_uncovered_archive_messages(states)
    assert uncovered == []


@pytest.mark.asyncio
async def test_phase2_replay_reraises_real_storage_failure(monkeypatch):
    session_uri = "viking://user/sessions/session-1"
    a1, a2, states = _failed_then_pending(session_uri)
    # A real storage failure (not a not-found) reading archive_001 must still
    # propagate so it is not silently swallowed.
    boom = RuntimeError("storage backend unavailable")
    session, _ = _session({f"{a1}/messages.jsonl": boom}, states, monkeypatch)

    from openviking.message import Message, TextPart

    current = [Message(id="cur", role="user", parts=[TextPart("hi")])]

    with pytest.raises(RuntimeError, match="storage backend unavailable"):
        await session._prepare_phase2_archive_messages(a2, current)
