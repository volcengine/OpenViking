# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.resource.github_radar.models import PendingPublish
from openviking.resource.github_radar.store import RadarStore
from tests.utils.radar_fixtures import REPO, MemoryFS, config, pr, task


async def test_load_recovers_facts_before_watermark_and_uses_user_context():
    fs, watch = MemoryFS(), task()
    store = RadarStore(fs, watch)
    progress, _ = await store.load(REPO, config())
    await store.write_queue([pr()])
    loaded, queue = await RadarStore(fs, watch).load(REPO, config())
    assert loaded.seen_numbers == [1]
    assert loaded.collected_until is None
    assert len(queue) == 1
    assert progress.repository == REPO
    assert store.root == f"viking://user/alice/resources/radar/{watch.task_id}"
    assert all(
        ctx.role == "user" and not ctx.bypass_acl and ctx.account_id == "tenant"
        for ctx in fs.contexts
    )


@pytest.mark.parametrize("failure_step", range(1, 6))
@pytest.mark.parametrize("after_write", [False, True])
async def test_publish_resumes_each_interrupted_write(failure_step, after_write):
    fs, watch = MemoryFS(), task()
    store = RadarStore(fs, watch)
    progress, _ = await store.load(REPO, config())
    await store.write_queue([pr()])
    final = progress.model_copy(deep=True)
    final.backfill_done = True
    final.reported_until = progress.anchor
    final.seen_numbers = [1]
    final.last_report_id = "a-b"
    progress.pending_publish = PendingPublish(
        report_id="a-b",
        content="fixed report",
        queue_after=[],
        state_after=final.model_dump(mode="json"),
    )
    await store.write_state(progress)
    fs.fail_at = fs.writes + failure_step
    fs.fail_after_write = after_write
    with pytest.raises(OSError, match="injected"):
        await store.publish(progress)
    reloaded, _ = await RadarStore(fs, watch).load(REPO, config())
    if reloaded.pending_publish:
        reloaded = await store.publish(reloaded)
    assert reloaded.pending_publish is None
    assert reloaded.reported_until == progress.anchor
    assert fs.files[f"{store.root}/latest.md"] == b"fixed report"
    assert fs.files[f"{store.root}/reports/a-b.md"] == b"fixed report"
    assert fs.files[f"{store.root}/.radar-signals.jsonl"] == b""


async def test_load_refuses_corrupt_state():
    fs, watch = MemoryFS(), task()
    store = RadarStore(fs, watch)
    fs.files[f"{store.root}/.radar-state.json"] = b"not json"
    with pytest.raises(ValueError):
        await store.load(REPO, config())
    assert fs.writes == 0
