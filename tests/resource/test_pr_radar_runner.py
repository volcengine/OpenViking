# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource import pr_radar_runner
from openviking.resource.github_radar.collector import GitHubCollector
from openviking.resource.github_radar.store import RadarStore
from tests.utils.radar_fixtures import ANCHOR, REPO, MemoryFS, config, node, page, task


@pytest.fixture
def freeze_now(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return ANCHOR + timedelta(hours=2)

    monkeypatch.setattr(pr_radar_runner, "datetime", Clock)


def processor():
    return SimpleNamespace(vlm_resolver=None)


async def test_run_radar_merged_time_windows_are_disjoint(monkeypatch, freeze_now):
    records = [
        node(1, ANCHOR - timedelta(seconds=1)),
        node(2, ANCHOR),
        node(3, ANCHOR + timedelta(seconds=1)),
        node(4, ANCHOR + timedelta(minutes=30)),
        node(5, ANCHOR + timedelta(minutes=30, seconds=1)),
    ]

    async def search(self, repository, span, cursor):
        return page(
            [
                record
                for record in records
                if span.start <= datetime.fromisoformat(record["mergedAt"]) <= span.end
            ]
        )

    monkeypatch.setattr(GitHubCollector, "search", search)
    fs, watch, source = MemoryFS(), task(), processor()
    store = RadarStore(fs, watch)
    await pr_radar_runner.run_radar(watch, fs, source)
    initial = fs.files[f"{store.root}/latest.md"].decode()
    assert "PR 数量：2" in initial
    assert "/pull/1)" in initial and "/pull/2)" in initial
    assert "/pull/3)" not in initial
    await pr_radar_runner.run_radar(watch, fs, source)
    second = fs.files[f"{store.root}/latest.md"].decode()
    assert "PR 数量：2" in second
    assert "/pull/3)" in second and "/pull/4)" in second
    assert "/pull/2)" not in second and "/pull/5)" not in second
    progress, queue = await store.load(REPO, config())
    assert progress.reported_until == ANCHOR + timedelta(minutes=30)
    assert [item.number for item in queue] == [5]


async def test_run_radar_empty_window_keeps_latest(monkeypatch, freeze_now):
    search = AsyncMock(return_value=page([]))
    monkeypatch.setattr(GitHubCollector, "search", search)
    fs, watch = MemoryFS(), task()
    store = RadarStore(fs, watch)
    fs.files[f"{store.root}/latest.md"] = b"previous report"
    await pr_radar_runner.run_radar(watch, fs, processor())
    progress, queue = await store.load(REPO, config())
    assert progress.backfill_done
    assert queue == []
    assert fs.files[f"{store.root}/latest.md"] == b"previous report"
    assert not any("/reports/" in uri for uri in fs.files)
    search.assert_awaited_once()


async def test_run_radar_restores_pending_without_recollection_or_model(monkeypatch, freeze_now):
    search = AsyncMock(return_value=page([node()]))
    monkeypatch.setattr(GitHubCollector, "search", search)
    fs, watch, source = MemoryFS(), task(), processor()
    store = RadarStore(fs, watch)
    # Inject failure on latest.md irrespective of collection's write count.
    original_write = fs.write_file

    async def fail_latest(uri, content, *, ctx):
        if uri.endswith("/latest.md"):
            raise OSError("publication failure")
        await original_write(uri, content, ctx=ctx)

    monkeypatch.setattr(fs, "write_file", fail_latest)
    with pytest.raises(OSError, match="publication"):
        await pr_radar_runner.run_radar(watch, fs, source)
    progress, _ = await store.load(REPO, config())
    pending = progress.pending_publish
    assert pending is not None
    before = search.await_count
    source.vlm_resolver = SimpleNamespace(
        get_vlm=AsyncMock(side_effect=AssertionError("must not summarize"))
    )
    monkeypatch.setattr(fs, "write_file", original_write)
    await pr_radar_runner.run_radar(watch, fs, source)
    restored, queue = await store.load(REPO, config())
    assert restored.pending_publish is None
    assert queue == []
    assert restored.last_report_id == pending.report_id
    assert fs.files[f"{store.root}/latest.md"].decode() == pending.content
    assert search.await_count == before
    source.vlm_resolver.get_vlm.assert_not_awaited()
