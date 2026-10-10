"""TTL experiments: real local adapter, pure response contracts, no per-hit I/O."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from openviking.storage.ttl import indexed_tags, project_results, query_filter, expires_at
from openviking_cli.utils.config.ttl_config import TTLConfig
from tests.storage.test_transfer_merge_binding import binding_fs, indexed_fs, root_ctx

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
ROOT = "viking://user/default/memories/events"


def config(**extra):
    return TTLConfig.model_validate({"user_events": {"mode": "days", "ttl_days": 30}, **extra})


@pytest.mark.parametrize("mode", ["absolute", "inherit"])
def test_no_explicit_inherit_or_absolute(mode):
    with pytest.raises(ValidationError):
        TTLConfig.model_validate({"global": {"mode": mode}})


def test_priority_and_projection():
    cfg = config(directories={ROOT: {"mode": "disabled"}})
    assert expires_at(cfg, ROOT + "/a.md", NOW.isoformat()) is None
    cfg = config()
    assert expires_at(cfg, ROOT + "/a.md", NOW.isoformat()) == NOW + timedelta(days=30)
    assert expires_at(cfg, ROOT, NOW.isoformat()) is None
    records = [{"uri": ROOT + "/a.md", "updated_at": NOW.isoformat(), "level": 2}]
    assert "expires_at" not in project_results(records, TTLConfig())[0]
    assert project_results(records, cfg)[0]["expires_at"] == "2026-11-09T00:00:00Z"
    assert indexed_tags(ROOT + "/a.md", ["__ov_ttl_scope=sessions", "public"]) == [
        "public",
        "__ov_ttl_scope=user_events",
    ]



@pytest.mark.parametrize("root", [
    ROOT,
    "viking://user/default/peers/peer/memories/events",
    "viking://user/default/sessions",
])
def test_policy_root_has_no_object_deadline(root):
    cfg = TTLConfig.model_validate({"global": {"mode": "days", "ttl_days": 30}})
    tags = indexed_tags(root, [], level=0)
    assert tags == ["__ov_ttl_scope=container"]
    row = {"uri": root, "level": 0, "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()}
    assert project_results([row], cfg) == [row]
    assert "expires_at" not in row
    assert "ttl_status" not in row


@pytest.mark.asyncio
async def test_session_parent_summary_is_not_a_session(indexed_fs):
    fs, backend = indexed_fs
    ctx = root_ctx()
    root = "viking://user/default/sessions"
    for uri in (root, root + "/one"):
        await backend.upsert({
            "id": uri, "uri": uri, "account_id": ctx.account_id, "level": 0,
            "vector": [0.1, 0.2, 0.3, 0.4], "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }, ctx=ctx)
    cfg = TTLConfig.model_validate({"sessions": {"mode": "days", "ttl_days": 30}})
    rows = await backend.filter(filter=query_filter(cfg, NOW), limit=10, ctx=ctx)
    assert [row["uri"] for row in rows] == [root + "/one"]


@pytest.mark.asyncio
async def test_real_filter_before_top_k(indexed_fs):
    fs, backend = indexed_fs
    ctx = root_ctx()
    for i, age in enumerate([60, 40, 30, 29, 1, 0]):
        uri = f"{ROOT}/{i}.md"
        await backend.upsert(
            {
                "id": uri,
                "uri": uri,
                "account_id": ctx.account_id,
                "level": 2,
                "vector": [0.1, 0.2, 0.3, 0.4],
                "updated_at": (NOW - timedelta(days=age)).isoformat(),
            },
            ctx=ctx,
        )
    rows = await backend.search(
        query_vector=[0.1, 0.2, 0.3, 0.4], filter=query_filter(config(), NOW), limit=2, ctx=ctx
    )
    assert len(rows) == 2
    assert all(int(row["uri"].split("/")[-1].split(".")[0]) >= 3 for row in rows)
    overridden = await backend.filter(
        filter=query_filter(config(directories={ROOT: {"mode": "disabled"}}), NOW),
        limit=10,
        ctx=ctx,
    )
    assert len(overridden) == 6


@pytest.mark.asyncio
async def test_tenant_disabled_identical_query_and_no_cleanup(indexed_fs, monkeypatch):
    fs, backend = indexed_fs
    ctx = root_ctx()
    backend.ttl_policy_reader = lambda account: TTLConfig()
    cleanup = SimpleNamespace(on_access=lambda ctx: pytest.fail("cleanup on disabled TTL"))
    backend.ttl_cleanup = cleanup
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(backend, "search", search)
    await backend.search_in_tenant(ctx, [1, 0, 0, 0], target_directories=[ROOT], limit=20)
    first = search.call_args
    del backend.ttl_policy_reader
    await backend.search_in_tenant(ctx, [1, 0, 0, 0], target_directories=[ROOT], limit=20)
    assert search.call_args == first


@pytest.mark.asyncio
async def test_delete_rechecks_file_version_and_disabled(indexed_fs, monkeypatch, tmp_path):
    import os
    import openviking.service.ttl_deletion as module

    fs, backend = indexed_fs
    ctx = root_ctx()
    uri = ROOT + "/expired.md"
    cfg = config()
    monkeypatch.setattr(module, "resolve_ttl_config", AsyncMock(side_effect=lambda *a, **k: cfg))
    await fs.write_file(uri, "new content", ctx=ctx)
    assert not await module.delete_expired(fs, uri, ctx)
    path = next(tmp_path.rglob("expired.md"))
    old = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    os.utime(path, (old, old))
    cfg = config(directories={ROOT: {"mode": "disabled"}})
    assert not await module.delete_expired(fs, uri, ctx)
    cfg = config()
    assert await module.delete_expired(fs, uri, ctx)
    assert not path.exists()


@pytest.mark.asyncio
async def test_whole_session_deleted_from_created_time(indexed_fs, monkeypatch):
    import json
    import openviking.service.ttl_deletion as module

    fs, backend = indexed_fs
    ctx = root_ctx()
    root = "viking://user/default/sessions/test"
    cfg = TTLConfig.model_validate({"sessions": {"mode": "days", "ttl_days": 1}})
    monkeypatch.setattr(module, "resolve_ttl_config", AsyncMock(return_value=cfg))
    # All files have a fresh modTime, but the whole Session is already old.
    await fs.write_file(
        root + "/.meta.json", json.dumps({"created_at": "2020-01-01T00:00:00Z"}), ctx=ctx
    )
    await fs.write_file(root + "/archive/a/messages.jsonl", "recent file", ctx=ctx)
    assert await module.delete_expired(fs, root + "/archive/a/messages.jsonl", ctx)
    assert not await fs.exists(root, ctx=ctx)


def test_sdk_roundtrip_omits_off_and_preserves_on():
    from openviking_cli.retrieve.types import MatchedContext, FindResult
    from openviking_cli.retrieve.types import ContextType

    item = MatchedContext(uri=ROOT + "/a.md", context_type=ContextType.MEMORY)
    result = FindResult(memories=[item], resources=[], skills=[])
    assert "expires_at" not in result.to_dict()["memories"][0]
    item.expires_at = "2026-11-09T00:00:00Z"
    assert FindResult.from_dict(result.to_dict()).memories[0].expires_at == item.expires_at


@pytest.mark.asyncio
async def test_queue_delivery_retries_same_message(binding_fs, monkeypatch):
    from openviking.service.ttl_cleanup import TTLCleanup
    from openviking_cli.utils.config.ttl_config import TTLCleanupConfig
    import openviking.service.ttl_cleanup as module

    if not hasattr(TTLCleanup, "_discover"):
        pytest.skip("QueueFS-specific test")
    queue = SimpleNamespace(
        name="test",
        dequeue_raw=AsyncMock(
            return_value={
                "id": "one",
                "data": {
                    "kind": "delete",
                    "account": "default",
                    "user": "default",
                    "uri": ROOT + "/a.md",
                },
            }
        ),
        _ack=AsyncMock(),
    )
    worker = TTLCleanup(binding_fs, TTLCleanupConfig(enabled=True), queue=queue)
    delete = AsyncMock(side_effect=[RuntimeError("transient"), True])
    monkeypatch.setattr(module, "delete_expired", delete)
    with pytest.raises(RuntimeError):
        await worker.run_once()
    queue._ack.assert_not_called()
    assert await worker.run_once()
    assert queue.dequeue_raw.await_count == 1
    assert queue._ack.await_count == 1
