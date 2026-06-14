# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for #1487.

``commit_async`` clears the live ``messages.jsonl`` and persists the new
(lower) ``message_count`` to ``.meta.json``. If that clear is lost while
``.meta.json`` and the archive ``.done`` marker land on disk, ``load()`` used
to read the already-archived messages back as live messages and re-insert them
as active context.

``Session.load()`` now reconciles this on load: since ``add_message`` keeps
``.meta.json``'s ``message_count`` in lockstep with the ``messages.jsonl`` line
count, a loaded message count that exceeds the committed ``message_count`` is
the signature of a lost clear, and the stale leading prefix is dropped.

These tests drive a real ``Session`` against a minimal in-memory filesystem, so
they exercise the actual ``load()`` path without the AGFS subprocess or an LLM.
"""

import json

import pytest

from openviking.message import TextPart
from openviking.session import Session


class FakeVikingFS:
    """Minimal in-memory VikingFS covering the methods load()/reconcile use."""

    def __init__(self):
        self.files: dict[str, str] = {}

    async def read_file(self, uri, offset=0, limit=-1, ctx=None):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None):
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.files[uri] = content

    async def ls(self, uri, ctx=None):
        base = uri.rstrip("/")
        names = set()
        for f in self.files:
            if f.startswith(base + "/"):
                names.add(f[len(base) + 1 :].split("/")[0])
        return [{"name": n} for n in sorted(names)]


def _msg_line(idx: int, role: str, text: str) -> str:
    from openviking.message import Message

    return Message(id=f"m{idx}", role=role, parts=[TextPart(text)]).to_jsonl()


def _seed(fs: FakeVikingFS, session_uri: str, jsonl_messages, message_count):
    """Persist a session state directly: messages.jsonl + .meta.json."""
    content = "".join(_msg_line(i, r, t) + "\n" for i, (r, t) in enumerate(jsonl_messages))
    fs.files[f"{session_uri}/messages.jsonl"] = content
    fs.files[f"{session_uri}/.meta.json"] = json.dumps(
        {"session_id": "s1487", "message_count": message_count, "last_commit_at": "t0"}
    )


class TestStaleLiveMessageReconciliation:
    def _session(self, fs):
        return Session(viking_fs=fs, session_id="s1487")

    async def test_lost_clear_drops_all_stale_messages(self):
        """keep=0 commit: message_count=0 but jsonl still holds archived rows."""
        fs = FakeVikingFS()
        uri = Session(viking_fs=fs, session_id="s1487")._session_uri
        _seed(
            fs,
            uri,
            [("user", "old A"), ("assistant", "old B")],
            message_count=0,  # committed clear says zero live messages
        )

        s = self._session(fs)
        await s.load()

        assert len(s.messages) == 0
        # messages.jsonl is rewritten so the heal survives a restart.
        assert fs.files[f"{uri}/messages.jsonl"].strip() == ""

    async def test_lost_clear_keeps_retained_tail(self):
        """keep>0 commit: message_count=1 but jsonl holds archived prefix + tail."""
        fs = FakeVikingFS()
        uri = Session(viking_fs=fs, session_id="s1487")._session_uri
        _seed(
            fs,
            uri,
            [("user", "old A"), ("assistant", "old B"), ("user", "kept tail")],
            message_count=1,  # only the last message should remain live
        )

        s = self._session(fs)
        await s.load()

        assert [m.content for m in s.messages] == ["kept tail"]
        # Disk healed to exactly the retained tail.
        reloaded = self._session(fs)
        await reloaded.load()
        assert [m.content for m in reloaded.messages] == ["kept tail"]

    async def test_consistent_state_is_untouched(self):
        """Normal case: message_count matches jsonl line count -> no change."""
        fs = FakeVikingFS()
        uri = Session(viking_fs=fs, session_id="s1487")._session_uri
        _seed(
            fs,
            uri,
            [("user", "live one"), ("assistant", "live two")],
            message_count=2,
        )
        before = fs.files[f"{uri}/messages.jsonl"]

        s = self._session(fs)
        await s.load()

        assert [m.content for m in s.messages] == ["live one", "live two"]
        # No spurious rewrite of messages.jsonl.
        assert fs.files[f"{uri}/messages.jsonl"] == before

    async def test_legacy_session_without_meta_is_untouched(self):
        """No .meta.json (legacy): reconciliation must not run."""
        fs = FakeVikingFS()
        uri = Session(viking_fs=fs, session_id="s1487")._session_uri
        fs.files[f"{uri}/messages.jsonl"] = (
            _msg_line(0, "user", "legacy one")
            + "\n"
            + _msg_line(1, "assistant", "legacy two")
            + "\n"
        )

        s = self._session(fs)
        await s.load()

        assert [m.content for m in s.messages] == ["legacy one", "legacy two"]


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
