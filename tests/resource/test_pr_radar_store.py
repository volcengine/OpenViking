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


@pytest.mark.parametrize("document", ["state", "queue"])
@pytest.mark.parametrize("failure", ["short_write", "rename"])
async def test_control_write_failure_preserves_previous_file(monkeypatch, document, failure):
    fs, watch = MemoryFS(), task()
    store = RadarStore(fs, watch)
    progress, _ = await store.load(REPO, config())
    await store.write_queue([pr()])
    name = ".radar-state.json" if document == "state" else ".radar-signals.jsonl"
    uri = f"{store.root}/{name}"
    previous = fs.files[uri]
    original_write, original_move = fs.write_file, fs.mv

    async def short_write(uri, content, *, ctx):
        assert isinstance(content, bytes)
        fs.files[uri] = content[:9]
        raise OSError("short write")

    async def failed_move(old_path, new_path, *, fs_ctx):
        assert fs_ctx == {"account_id": "tenant"}
        assert new_path == uri and old_path == f"{uri}.tmp"
        raise OSError("rename failed")

    if failure == "short_write":
        monkeypatch.setattr(fs, "write_file", short_write)
    else:
        monkeypatch.setattr(fs, "mv", failed_move)
    progress.last_report_id = "a-b"
    with pytest.raises(OSError, match="short write|rename failed"):
        if document == "state":
            await store.write_state(progress)
        else:
            await store.write_queue([pr(), pr(2)])
    assert fs.files[uri] == previous
    restored, queue = await store.load(REPO, config())
    assert restored.last_report_id is None
    assert [item.number for item in queue] == [1]
    monkeypatch.setattr(fs, "write_file", original_write)
    monkeypatch.setattr(fs, "mv", original_move)
    if document == "state":
        await store.write_state(progress)
    else:
        await store.write_queue([pr(), pr(2)])
    restored, queue = await store.load(REPO, config())
    assert restored.last_report_id == ("a-b" if document == "state" else None)
    assert [item.number for item in queue] == ([1] if document == "state" else [1, 2])
    assert f"{uri}.tmp" not in fs.files
