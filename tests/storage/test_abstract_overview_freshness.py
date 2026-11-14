# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.core.context import ContextLevel
from openviking.storage.abstract_overview import (
    freshness_metadata,
    parse_abstract_overview,
    plan_abstract_overview_refresh,
    read_abstract_overview_pending_snapshot,
    render_abstract_overview,
    write_abstract_overview,
)
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction


class _FakeFS:
    def __init__(self, files):
        self.files = dict(files)
        self._async_agfs = self
        self._lock = asyncio.Lock()
        self.lock_timeouts = []

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise KeyError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content

    def _uri_to_path(self, uri, ctx=None):
        return uri

    async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
        self.lock_timeouts.append(timeout_secs)
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
        lock_timeout_secs=1.0,
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
    assert fs.lock_timeouts == [1.0, 0.0]
    for raw in fs.files.values():
        assert parse_abstract_overview(raw).metadata["freshness"]["pending_child_changes"] == 17


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
        assert parse_abstract_overview(raw).metadata["freshness"]["pending_child_changes"] == 13


@pytest.mark.asyncio
async def test_partial_sidecar_baseline_refreshes_immediately():
    dir_uri = "viking://resources/wide"
    files = _files(dir_uri, pending=3)
    files[f"{dir_uri}/.abstract.md"] = "---\n"
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


@pytest.mark.asyncio
async def test_pending_snapshot_of_missing_directory_does_not_lock():
    """Locking sidecars of a deleted directory would recreate it; return 0 instead."""
    fs = _FakeFS({})
    assert (
        await read_abstract_overview_pending_snapshot(
            viking_fs=fs, dir_uri="viking://resources/gone", ctx=None
        )
        == 0
    )
    assert fs.lock_timeouts == []


@pytest.mark.asyncio
async def test_pending_snapshot_reads_existing_sidecars_under_lock():
    dir_uri = "viking://resources/present"
    fs = _FakeFS(_files(dir_uri, pending=3))
    assert (
        await read_abstract_overview_pending_snapshot(viking_fs=fs, dir_uri=dir_uri, ctx=None) == 3
    )
    assert fs.lock_timeouts == [0.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["before_write_lock", "after_write_lock"])
async def test_prepared_refresh_rechecks_revision_at_both_commit_windows(monkeypatch, window):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor

    root = "viking://resources/repo"
    original = _files(root, total=161, pending=17)
    fs = _FakeFS(original)
    fs.ls = AsyncMock(return_value=[])
    generated, finish_generation = asyncio.Event(), asyncio.Event()
    acquiring_write, finish_acquire = asyncio.Event(), asyncio.Event()
    obsolete = False
    lock_count = 0
    original_acquire = fs.pathlock_acquire_exact_batch

    async def acquire(paths, timeout_secs=0.0):
        nonlocal lock_count
        lock_count += 1
        if lock_count == 2 and window == "after_write_lock":
            acquiring_write.set()
            await finish_acquire.wait()
        return await original_acquire(paths, timeout_secs)

    async def generate(*args, **kwargs):
        generated.set()
        await finish_generation.wait()
        return "new overview"

    async def stale():
        return obsolete

    fs.pathlock_acquire_exact_batch = acquire
    processor = SimpleNamespace(
        _generate_overview=generate,
        _normalize_overview_generation=lambda overview: (overview, "new abstract"),
        _vectorize_directory=AsyncMock(),
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    executor = SemanticTreeExecutor(processor, "resource", 1, None)
    executor._is_stale = stale
    task = asyncio.create_task(executor.run(root))
    await asyncio.wait_for(generated.wait(), 2)
    if window == "before_write_lock":
        obsolete = True
        finish_generation.set()
    else:
        finish_generation.set()
        await asyncio.wait_for(acquiring_write.wait(), 2)
        obsolete = True
        finish_acquire.set()
    await asyncio.wait_for(task, 2)
    assert fs.files == original
    assert executor.stale
    processor._vectorize_directory.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "force, expected_pending, aggregate", [(None, 15, True), (False, 16, False), (True, 16, True)]
)
async def test_committed_refresh_keeps_force_refresh_tristate(
    monkeypatch, force, expected_pending, aggregate
):
    from types import SimpleNamespace

    from openviking.storage.context_update_plan import SemanticRefreshIntent
    from openviking.storage.semantic_planning import (
        prepare_committed_refresh,
        resolve_semantic_execution,
    )

    root = "viking://resources/wide"
    fs = _FakeFS(_files(root, total=161, pending=15))
    monkeypatch.setattr(
        "openviking.storage.semantic_planning.get_openviking_config",
        lambda: SimpleNamespace(
            semantic=SimpleNamespace(overview_sample_limit=32, freshness_refresh_ratio=0.10)
        ),
    )
    intent, _ = await prepare_committed_refresh(
        SemanticRefreshIntent(
            root, "resource", recursive=False, changes={"modified": [f"{root}/a.py"]}
        ),
        viking_fs=fs,
        ctx=None,
        force_refresh=force,
    )
    assert intent.aggregate_directory is aggregate
    # Resolution/retry does not admit the original changes a second time.
    resolve_semantic_execution(intent)
    resolve_semantic_execution(intent)
    assert {
        parse_abstract_overview(raw).metadata["freshness"]["pending_child_changes"]
        for raw in fs.files.values()
    } == {expected_pending}
