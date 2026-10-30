# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from openviking.message import Message
from openviking.message.part import ContextPart, TextPart
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.usage_feedback import (
    USAGE_FEEDBACK_FIELD,
    Verdict,
    apply_verdict,
    collect_candidate_uris,
    parse_verdicts,
    peer_allowed,
    read_candidates,
    render_trajectory,
    score_usage,
    write_back,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

MEM_A = "viking://user/u1/memories/preferences/a.md"
MEM_B = "viking://user/u1/memories/entities/b.md"
NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def _msg(role: str, *parts) -> Message:
    return Message(id=f"m{id(parts)}", role=role, parts=list(parts))


def _ctx(uri: str, context_type: str = "memory") -> ContextPart:
    return ContextPart(uri=uri, context_type=context_type)


class FakeFS:
    def __init__(self, files):
        self.files = dict(files)
        self.writes = []

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None):
        self.writes.append(uri)
        self.files[uri] = content


class FakeVLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    async def get_completion_async(self, prompt):
        self.prompts.append(prompt)
        return self.response


def _stored(uri: str, body: str = "Prefers tabs.") -> str:
    return MemoryFileUtils.write(
        MemoryFile(
            uri=uri,
            memory_type="preferences",
            content=body,
            links=[{"to_uri": MEM_B, "link_type": "related"}],
            extra_fields={"version": 3, "topic": "editor"},
        )
    )


def test_collect_candidates_filters_ranks_and_limits():
    messages = [
        _msg("user", TextPart("hi"), _ctx(MEM_B), _ctx(MEM_A)),
        _msg("user", _ctx(MEM_A), _ctx("viking://resources/doc.md", "resource")),
        _msg("user", _ctx("viking://user/u1/skills/s/SKILL.md", "skill")),
    ]
    assert collect_candidate_uris(messages) == [MEM_A, MEM_B]
    assert collect_candidate_uris(messages, limit=1) == [MEM_A]


def test_parse_verdicts_drops_invalid_entries():
    raw = "noise " + json.dumps(
        [
            {"uri": MEM_A, "score": 1, "reason": "x" * 500},
            {"uri": MEM_A, "score": -1, "reason": "duplicate"},
            {"uri": MEM_B, "score": 2},
            {"uri": "viking://user/u1/memories/unknown.md", "score": 0},
            {"uri": MEM_B, "score": True},
            "junk",
        ]
    )
    verdicts = parse_verdicts(raw, {MEM_A: "", MEM_B: ""})
    assert verdicts == [Verdict(MEM_A, 1, "x" * 200)]
    assert parse_verdicts("not json", {MEM_A: ""}) == []
    assert parse_verdicts('{"uri": "x"}', {MEM_A: ""}) == []


def test_apply_verdict_accumulates_and_is_idempotent_per_archive():
    mf = MemoryFile(uri=MEM_A, content="body")
    assert apply_verdict(mf, Verdict(MEM_A, -1, "misled"), "s/a1", NOW)
    assert not apply_verdict(mf, Verdict(MEM_A, -1, "misled"), "s/a1", NOW)
    assert apply_verdict(mf, Verdict(MEM_A, 0, "unused"), "s/a2", NOW)
    assert apply_verdict(mf, Verdict(MEM_A, -1, "again"), "s/a3", NOW)
    stats = mf.extra_fields[USAGE_FEEDBACK_FIELD]
    assert stats["value"] == -2
    assert (stats["helpful"], stats["harmful"], stats["unused"]) == (0, 2, 1)
    assert stats["last_archive"] == "s/a3"
    assert stats["last_reason"] == "again"


def test_render_trajectory_keeps_head_and_tail():
    messages = [_msg("user", TextPart(f"line-{i:03d} " + "x" * 50)) for i in range(100)]
    rendered = render_trajectory(messages, max_chars=1000)
    assert "line-000" in rendered and "line-099" in rendered
    assert "line-050" not in rendered
    assert "messages omitted" in rendered
    assert len(rendered) < 1200
    assert render_trajectory(messages[:2], max_chars=1000).count("line-") == 2


def test_peer_allowed_mirrors_extraction_scope():
    peer_uri = "viking://user/u1/peers/p1/memories/x.md"
    assert peer_allowed(MEM_A, allow_self=True, allowed_peer_ids=set())
    assert not peer_allowed(MEM_A, allow_self=False, allowed_peer_ids={"p1"})
    assert peer_allowed(peer_uri, allow_self=False, allowed_peer_ids={"p1"})
    assert not peer_allowed(peer_uri, allow_self=True, allowed_peer_ids={"p2"})


@pytest.mark.asyncio
async def test_score_and_write_back_round_trip_preserves_body_and_version():
    fs = FakeFS({MEM_A: _stored(MEM_A), MEM_B: _stored(MEM_B, "Uses vim.")})
    memories = await read_candidates(fs, None, [MEM_A, MEM_B, "viking://user/u1/memories/gone.md"])
    assert list(memories) == [MEM_A, MEM_B]

    vlm = FakeVLM(
        json.dumps(
            [
                {"uri": MEM_A, "score": 1, "reason": "used"},
                {"uri": MEM_B, "score": -1, "reason": "wrong"},
            ]
        )
    )
    messages = [_msg("user", TextPart("format my file"), _ctx(MEM_A))]
    verdicts = await score_usage(vlm, messages, memories)
    assert "Prefers tabs." in vlm.prompts[0] and "format my file" in vlm.prompts[0]

    before = MemoryFileUtils.read(fs.files[MEM_A], uri=MEM_A)
    written = await write_back(fs, None, verdicts, "s/a1", NOW)
    assert written == {MEM_A: 1, MEM_B: -1}
    after = MemoryFileUtils.read(fs.files[MEM_A], uri=MEM_A)
    assert after.content == before.content
    assert after.links == before.links
    assert after.extra_fields["version"] == 3
    assert after.extra_fields["topic"] == "editor"
    assert after.extra_fields[USAGE_FEEDBACK_FIELD]["value"] == 1

    # Same archive again: nothing rewritten.
    fs.writes.clear()
    assert await write_back(fs, None, verdicts, "s/a1", NOW) == {}
    assert fs.writes == []


@pytest.mark.asyncio
async def test_write_back_skips_failed_file():
    fs = FakeFS({MEM_B: _stored(MEM_B)})
    written = await write_back(
        fs, None, [Verdict(MEM_A, 1, ""), Verdict(MEM_B, 0, "")], "s/a1", NOW
    )
    assert written == {MEM_B: 0}
