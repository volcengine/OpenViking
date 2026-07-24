# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for type_quota_recall preference_full_count behavior.

Bug #8: ``preference_full_count`` was incremented before the budget check,
so the first ``PREFERENCE_FULL_LIMIT`` preferences that *failed* the budget
check wasted the cap.  Subsequent preferences that *would* have fit were
denied full rendering.

These tests are standalone unit tests (no server fixtures needed) so they
run without a full OpenViking deployment or config file.
"""

from types import SimpleNamespace

import pytest

from openviking.retrieve.type_quota_recall import (
    PREFERENCE_FULL_LIMIT,
    search_type_quota_recall,
)
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve import ContextType, MatchedContext
from openviking_cli.session.user_id import UserIdentifier


class _FakeFindResult:
    def __init__(self, memories):
        self.memories = memories


def _make_mock_service(find_fn, read_fn):
    """Build a minimal mock service with search.find and fs.read."""
    search = SimpleNamespace(find=find_fn)
    fs = SimpleNamespace(read=read_fn)
    return SimpleNamespace(search=search, fs=fs)


@pytest.mark.asyncio
async def test_preference_full_count_only_incremented_on_full_success():
    """``preference_full_count`` must only increment when a preference actually
    renders as *full*, not before the budget check.

    Scenario: ``PREFERENCE_FULL_LIMIT + 2`` preferences returned.  The first
    ``PREFERENCE_FULL_LIMIT`` entries have content so large that their full
    fragments exceed ``max_chars``.  The remaining two entries have small
    content.  After the fix the last two entries should still render as
    ``"full"`` — the counter was not wasted on the first three (failing)
    entries.
    """
    pref_count = PREFERENCE_FULL_LIMIT + 2
    mem_root = "viking://user/test_user/memories"

    async def fake_find(**kwargs):
        target = kwargs["target_uri"]
        if target.endswith("/preferences"):
            return _FakeFindResult(
                [
                    MatchedContext(
                        uri=f"{mem_root}/preferences/pref_{i}.md",
                        context_type=ContextType.MEMORY,
                        level=2,
                        score=0.9 - i * 0.01,
                        abstract=f"preference {i}",
                        category="preferences",
                    )
                    for i in range(pref_count)
                ]
            )
        return _FakeFindResult([])

    read_contents: dict[str, str] = {}
    for i in range(pref_count):
        if i < PREFERENCE_FULL_LIMIT:
            # Very large content — its full fragment will exceed the budget.
            # Make it unique per memory so the new content-hash dedupe does not
            # collapse the failing entries together.
            read_contents[f"{mem_root}/preferences/pref_{i}.md"] = f"large content block {i} " + "x" * 5000
        else:
            read_contents[f"{mem_root}/preferences/pref_{i}.md"] = f"short pref {i}"

    async def fake_read(uri, **kwargs):
        return read_contents.get(uri, "")

    service = _make_mock_service(fake_find, fake_read)

    ctx = RequestContext(
        user=UserIdentifier.the_default_user("test_user"),
        role=Role.ROOT,
    )

    result = await search_type_quota_recall(
        service=service,
        ctx=ctx,
        query="preferences test",
        quotas={"events": 0, "entities": 0, "preferences": pref_count, "experiences": 0},
        max_chars=2000,
        min_score=0.1,
        render=True,
    )

    by_uri = {e.uri: e for e in result.entries}
    large_uris = {f"{mem_root}/preferences/pref_{i}.md" for i in range(PREFERENCE_FULL_LIMIT)}
    small_uris = {f"{mem_root}/preferences/pref_{i}.md" for i in range(PREFERENCE_FULL_LIMIT, pref_count)}

    # Large-content entries should not have rendered as full; they may still
    # appear as URI-only entries (the upstream budget fallback).
    for uri in large_uris:
        entry = by_uri.get(uri)
        assert entry is not None, f"Missing large-content entry {uri}. Got {list(by_uri)}"
        assert entry.mode != "full", (
            f"Large-content entry {uri} unexpectedly rendered as full"
        )

    # Small-content entries that fit must still render as full: the counter
    # was not consumed by the failing large-content entries.
    for uri in small_uris:
        entry = by_uri.get(uri)
        assert entry is not None, f"Missing small-content entry {uri}. Got {list(by_uri)}"
        assert entry.mode == "full", (
            f"Entry {uri} should be full but was {entry.mode!r}. "
            f"Modes: {[e.mode for e in result.entries]}"
        )


@pytest.mark.asyncio
async def test_preference_full_limit_still_capped_when_full_fits():
    """After ``PREFERENCE_FULL_LIMIT`` preferences successfully render as full,
    subsequent preferences must be capped (no longer try full) even if budget
    remains.
    """
    pref_count = PREFERENCE_FULL_LIMIT + 3
    mem_root = "viking://user/test_user/memories"

    async def fake_find(**kwargs):
        target = kwargs["target_uri"]
        if target.endswith("/preferences"):
            return _FakeFindResult(
                [
                    MatchedContext(
                        uri=f"{mem_root}/preferences/pref_{i}.md",
                        context_type=ContextType.MEMORY,
                        level=2,
                        score=0.9 - i * 0.01,
                        abstract=f"preference {i}",
                        category="preferences",
                    )
                    for i in range(pref_count)
                ]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        # Unique per-memory content so the upstream content-hash deduper
        # does not collapse distinct preferences.
        return f"short content {uri}"

    service = _make_mock_service(fake_find, fake_read)

    ctx = RequestContext(
        user=UserIdentifier.the_default_user("test_user"),
        role=Role.ROOT,
    )

    result = await search_type_quota_recall(
        service=service,
        ctx=ctx,
        query="preferences test",
        quotas={"events": 0, "entities": 0, "preferences": pref_count, "experiences": 0},
        max_chars=10000,
        min_score=0.1,
        render=True,
    )

    full_modes = [e.mode for e in result.entries if e.mode == "full"]
    assert len(full_modes) == PREFERENCE_FULL_LIMIT, (
        f"Expected exactly {PREFERENCE_FULL_LIMIT} full preferences, "
        f"got {len(full_modes)}. Modes: {[e.mode for e in result.entries]}"
    )

    # After the limit, remaining entries should NOT be "full"
    for i in range(PREFERENCE_FULL_LIMIT, len(result.entries)):
        assert result.entries[i].mode != "full", (
            f"Entry {i} should not be full (limit reached). "
            f"Got mode={result.entries[i].mode!r}"
        )
