# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for bounded pending-child-URI accounting (#4577).

A pending *counter* alone cannot tell the DAG executor which sampled inputs are
dirty, so every triggered aggregation rebuilds the whole sample set. The URI
collection added here records *which* children changed (bounded, with an
overflow fallback), letting aggregation rebuild only dirty inputs.
"""

import asyncio

import pytest

from openviking.core.context import ContextLevel
from openviking.storage.abstract_overview import (
    PENDING_CHILD_URIS_LIMIT,
    _merge_pending_child_uris,
    freshness_metadata,
    parse_abstract_overview,
    plan_abstract_overview_refresh,
    read_abstract_overview_pending_child_uris,
    render_abstract_overview,
)


class _FakeFS:
    def __init__(self, files):
        self.files = dict(files)
        self._async_agfs = self
        self._lock = asyncio.Lock()

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise KeyError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content

    def _uri_to_path(self, uri, ctx=None):
        return uri

    async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
        await self._lock.acquire()
        return {"paths": paths}

    async def pathlock_release(self, lease):
        self._lock.release()
        return None


def _files(dir_uri, *, total=161, pending=3, extra_freshness=None):
    freshness = freshness_metadata(total, min(total, 32), pending)
    if extra_freshness:
        freshness = {**freshness, **extra_freshness}
    metadata = {"freshness": freshness}
    return {
        f"{dir_uri}/.overview.md": render_abstract_overview(
            ContextLevel.OVERVIEW, dir_uri, "overview", metadata
        ),
        f"{dir_uri}/.abstract.md": render_abstract_overview(
            ContextLevel.ABSTRACT, dir_uri, "abstract", metadata
        ),
    }


def _freshness_of(fs, uri):
    document = parse_abstract_overview(fs.files[f"{uri}/.overview.md"])
    return document.metadata["freshness"]


# ---------- _merge_pending_child_uris (pure) ----------


def test_merge_records_and_dedupes_uris():
    first = _merge_pending_child_uris({}, ["viking://r/a", "viking://r/b"])
    assert first == {"pending_child_uris": ["viking://r/a", "viking://r/b"]}
    second = _merge_pending_child_uris(first, ["viking://r/b", "viking://r/c"])
    assert second["pending_child_uris"] == ["viking://r/a", "viking://r/b", "viking://r/c"]
    assert "pending_child_uris_overflow" not in second


def test_merge_without_uris_touches_nothing():
    assert _merge_pending_child_uris({}, None) == {}


def test_merge_overflow_drops_set_and_flags():
    many = [f"viking://r/f{i}" for i in range(PENDING_CHILD_URIS_LIMIT + 1)]
    result = _merge_pending_child_uris({}, many)
    assert result["pending_child_uris"] == []
    assert result["pending_child_uris_overflow"] is True


def test_merge_overflow_sticks_until_cleared():
    overflowed = {"pending_child_uris": [], "pending_child_uris_overflow": True}
    # further merges stay in overflow (conservative full rebuild)
    result = _merge_pending_child_uris(overflowed, ["viking://r/x"])
    assert result["pending_child_uris_overflow"] is True


# ---------- plan side-effect on sidecar metadata ----------


@pytest.mark.asyncio
async def test_plan_persists_uris_into_sidecars():
    fs = _FakeFS(_files("viking://r"))
    await plan_abstract_overview_refresh(
        viking_fs=fs,
        dir_uri="viking://r",
        changed_entries=1,
        ctx=None,
        changed_child_uris=["viking://r/a.md"],
    )
    freshness = _freshness_of(fs, "viking://r")
    assert freshness["pending_child_uris"] == ["viking://r/a.md"]


@pytest.mark.asyncio
async def test_plan_without_uris_keeps_legacy_counter_only():
    fs = _FakeFS(_files("viking://r"))
    await plan_abstract_overview_refresh(
        viking_fs=fs, dir_uri="viking://r", changed_entries=1, ctx=None
    )
    freshness = _freshness_of(fs, "viking://r")
    assert "pending_child_uris" not in freshness


# ---------- read side: three states ----------


@pytest.mark.asyncio
async def test_read_returns_collection_when_recorded():
    fs = _FakeFS(
        _files("viking://r", extra_freshness={"pending_child_uris": ["viking://r/a.md"]})
    )
    uris, overflow = await read_abstract_overview_pending_child_uris(
        viking_fs=fs, dir_uri="viking://r", ctx=None
    )
    assert uris == {"viking://r/a.md"}
    assert overflow is False


@pytest.mark.asyncio
async def test_read_legacy_sidecar_reports_empty_not_overflow():
    fs = _FakeFS(_files("viking://r"))
    uris, overflow = await read_abstract_overview_pending_child_uris(
        viking_fs=fs, dir_uri="viking://r", ctx=None
    )
    assert uris == set()
    assert overflow is False  # empty+False = legacy, executor falls back


@pytest.mark.asyncio
async def test_read_overflow_flags_full_rebuild():
    fs = _FakeFS(
        _files(
            "viking://r",
            extra_freshness={"pending_child_uris": [], "pending_child_uris_overflow": True},
        )
    )
    uris, overflow = await read_abstract_overview_pending_child_uris(
        viking_fs=fs, dir_uri="viking://r", ctx=None
    )
    assert uris == set()
    assert overflow is True


@pytest.mark.asyncio
async def test_write_after_aggregation_returns_to_legacy_state():
    # A fresh write builds metadata via freshness_metadata(), which has no URI
    # collection — after aggregation the sidecar is intentionally "legacy"
    # (empty + no overflow), so the next aggregation without new URIs falls
    # back to the conservative behavior.
    freshness = freshness_metadata(total_entries=10, sampled_entries=10, pending=0)
    assert "pending_child_uris" not in freshness
    assert "pending_child_uris_overflow" not in freshness
