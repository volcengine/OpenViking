# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.core.context import ContextLevel
from openviking.storage.abstract_overview import (
    freshness_metadata,
    parse_abstract_overview,
    plan_abstract_overview_refresh,
    render_abstract_overview,
    write_abstract_overview,
)
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction


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

    async def pathlock_acquire_exact_batch(self, paths):
        await self._lock.acquire()
        return {"paths": paths}

    async def pathlock_release(self, lease):
        self._lock.release()
        return None


def _files(dir_uri, *, total=161, pending=3):
    metadata = {"freshness": freshness_metadata(total, min(total, 32), pending)}
    return {
        f"{dir_uri}/.overview.md": render_abstract_overview(
            ContextLevel.OVERVIEW, dir_uri, "overview", metadata
        ),
        f"{dir_uri}/.abstract.md": render_abstract_overview(
            ContextLevel.ABSTRACT, dir_uri, "abstract", metadata
        ),
    }


@pytest.mark.asyncio
async def test_pending_increment_and_threshold_decision_share_one_snapshot():
    dir_uri = "viking://resources/wide"
    fs = _FakeFS(_files(dir_uri, pending=15))

    first = await plan_abstract_overview_refresh(
        viking_fs=fs,
        dir_uri=dir_uri,
        changed_entries=1,
        ctx=None,
        overview_sample_limit=32,
        refresh_ratio=0.10,
    )
    second = await plan_abstract_overview_refresh(
        viking_fs=fs,
        dir_uri=dir_uri,
        changed_entries=1,
        ctx=None,
        overview_sample_limit=32,
        refresh_ratio=0.10,
    )

    assert first.action is FreshnessAction.MARK_PENDING
    assert second.action is FreshnessAction.REFRESH_NOW
    for raw in fs.files.values():
        assert parse_abstract_overview(raw).metadata["freshness"][
            "pending_child_changes"
        ] == 17


@pytest.mark.asyncio
async def test_write_result_ignores_metadata_only_changes_and_preserves_new_pending():
    dir_uri = "viking://resources/wide"
    fs = _FakeFS(_files(dir_uri, pending=5))

    result = await write_abstract_overview(
        viking_fs=fs,
        dir_uri=dir_uri,
        overview="overview",
        abstract="abstract",
        ctx=None,
        is_stale=lambda: False,
        metadata={"freshness": freshness_metadata(160, 32)},
        consume_pending=3,
    )

    assert result.wrote
    assert not result.overview_body_changed
    assert not result.abstract_body_changed
    for raw in fs.files.values():
        freshness = parse_abstract_overview(raw).metadata["freshness"]
        assert freshness == freshness_metadata(160, 32, pending=2)


@pytest.mark.asyncio
async def test_concurrent_pending_marks_do_not_overwrite_each_other():
    dir_uri = "viking://resources/wide"
    fs = _FakeFS(_files(dir_uri, pending=3))

    await asyncio.gather(
        *(
            plan_abstract_overview_refresh(
                viking_fs=fs,
                dir_uri=dir_uri,
                changed_entries=1,
                ctx=None,
                overview_sample_limit=32,
                refresh_ratio=1.0,
            )
            for _ in range(10)
        )
    )

    for raw in fs.files.values():
        assert parse_abstract_overview(raw).metadata["freshness"][
            "pending_child_changes"
        ] == 13


@pytest.mark.asyncio
async def test_partial_sidecar_baseline_refreshes_immediately():
    dir_uri = "viking://resources/wide"
    files = _files(dir_uri, pending=3)
    files.pop(f"{dir_uri}/.abstract.md")
    fs = _FakeFS(files)

    decision = await plan_abstract_overview_refresh(
        viking_fs=fs,
        dir_uri=dir_uri,
        changed_entries=1,
        ctx=None,
        overview_sample_limit=32,
        refresh_ratio=1.0,
    )

    assert decision.action is FreshnessAction.REFRESH_NOW
    assert decision.pending_after == 1
