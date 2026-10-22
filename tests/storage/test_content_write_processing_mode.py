# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage import content_write as content_write_module
from openviking.storage.abstract_overview import (
    render_abstract_overview,
)
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.context_update_plan import ContextUpdatePlan
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


class _FakePathLock:
    """Mock for _async_agfs pathlock operations."""

    def __init__(self):
        self._lease = SimpleNamespace(id="lock-1")
        self.exact_acquire_calls = []
        self.tree_acquire_calls = []
        self.release_calls = []
        self.to_handoff_calls = []
        self.handoff_calls = []

    async def pathlock_acquire_exact(self, lock_path):
        self.exact_acquire_calls.append(lock_path)
        return self._lease

    async def pathlock_acquire_tree(self, lock_path):
        self.tree_acquire_calls.append(lock_path)
        raise AssertionError("Skill content writes must not acquire a package tree lock")

    async def pathlock_release(self, lease):
        self.release_calls.append(lease.id)

    async def pathlock_to_handoff(self, lease):
        self.to_handoff_calls.append(lease.id)
        return {
            "owner_id": lease.id,
            "covered_paths": [{"path": "/fake/file", "kind": "exact"}],
        }

    async def pathlock_handoff(self, lease):
        self.handoff_calls.append(lease.id)


class _FakeVikingFS:
    def __init__(self):
        self.write_file = AsyncMock()
        self.write_file_bytes = AsyncMock()
        self.read_file = AsyncMock(return_value="previous")
        self._async_agfs = _FakePathLock()

    def _uri_to_path(self, uri, ctx=None):
        return f"/fake/{uri}"

    async def _ensure_access(self, uri, ctx, action):
        del uri, ctx, action


@pytest.mark.asyncio
async def test_content_write_stat_skips_directory_vector_count(ctx):
    fake_fs = _FakeVikingFS()
    fake_fs.stat = AsyncMock(return_value={"isDir": True})
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    assert await coordinator._safe_stat("viking://resources/demo", ctx=ctx) == {"isDir": True}
    fake_fs.stat.assert_awaited_once_with(
        "viking://resources/demo",
        ctx=ctx,
        skip_count=True,
    )


def _sidecar(level=ContextLevel.ABSTRACT, body="Original body."):
    return render_abstract_overview(
        level,
        "viking://resources/demo",
        body,
        {
            "generated_by": {"component": "test", "trigger": "test"},
            "freshness": {
                "total_entries": 1,
                "sampled_entries": 1,
                "unsampled_entries": 0,
                "pending_child_changes": 0,
            },
        },
    )


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)


@pytest.mark.asyncio
async def test_semantic_message_carries_file_md5_and_old_abstract(monkeypatch, ctx):
    file_uri = "viking://resources/demo.py"
    queue = SimpleNamespace(enqueue=AsyncMock(return_value="enqueued"))
    manager = SimpleNamespace(SEMANTIC="Semantic", get_queue=lambda *args, **kwargs: queue)
    monkeypatch.setattr(content_write_module, "get_queue_manager", lambda: manager)
    monkeypatch.setattr(
        content_write_module,
        "plan_abstract_overview_refresh",
        AsyncMock(return_value=SimpleNamespace(action=FreshnessAction.REFRESH_NOW)),
    )
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())

    await coordinator._enqueue_semantic_refresh_changes(
        root_uri="viking://resources",
        context_type="resource",
        changes={"modified": [file_uri]},
        ctx=ctx,
        file_md5s={file_uri: "new-md5"},
        file_abstracts={file_uri: "old abstract"},
    )

    msg = queue.enqueue.await_args.args[0]
    assert msg.file_md5s == {file_uri: "new-md5"}
    assert msg.file_abstracts == {file_uri: "old abstract"}


@pytest.mark.asyncio
async def test_old_abstract_lookup_uses_viking_fs_vector_store_when_not_injected(ctx):
    file_uri = "viking://agent/skills/demo/SKILL.md"

    class _VectorStore:
        async def get_l2_diff_records_by_uris(self, uris, *, ctx):
            return {file_uri: {"abstract": "skill summary"}}

    fake_fs = _FakeVikingFS()
    fake_fs._get_vector_store = lambda: _VectorStore()
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    assert await coordinator._load_file_abstracts([file_uri], ctx=ctx) == {
        file_uri: "skill summary"
    }


@pytest.mark.asyncio
async def test_prepare_inline_resource_plan_reuses_locked_facts_and_preloaded_inventory(
    monkeypatch, ctx
):
    """Single and batch adapters share one file-scoped RNFV plan builder."""
    uri = "viking://resources/demo.md"
    fake_fs = _FakeVikingFS()
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    vector_store = object()
    captured = {}
    existing_record = SimpleNamespace(
        uri=uri,
        level=2,
        fields={"abstract": "existing L2 abstract"},
    )

    async def _snapshot(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(vectors=SimpleNamespace(records_by_id={"a-l2": existing_record}))

    async def _plan(**kwargs):
        captured["plan_kwargs"] = kwargs
        return None, ContextUpdatePlan(uri, "resource")

    monkeypatch.setattr(content_write_module, "build_rnfv_snapshot", _snapshot)
    monkeypatch.setattr(content_write_module, "build_context_update_plan_from_snapshot", _plan)

    prepared = await coordinator._prepare_inline_resource_plan(
        uri=uri,
        final_bytes=b"new bytes",
        target_preexisting=True,
        formal_snapshot=({"": content_write_module.FormalEntry(is_dir=False)}, True),
        vector_inventory={"a-l2": {"id": "a-l2"}},
        vector_store=vector_store,
        request_intent=content_write_module.RequestIntent(uri, "semantic_and_vectors"),
        ctx=ctx,
        lease=SimpleNamespace(id="lock-1"),
        ingest_options=IngestOptions(),
        include_previous_abstract=True,
    )

    assert prepared.final_bytes == b"new bytes"
    assert prepared.previous_abstract == "existing L2 abstract"
    assert captured["root_is_file"] is True
    assert captured["vector_scope"] == "self"
    assert captured["formal_snapshot"] == ({"": content_write_module.FormalEntry(False)}, True)
    assert captured["vector_inventory"] == {"a-l2": {"id": "a-l2"}}


def test_write_result_keeps_content_updated_compatibility_field():
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())

    result = coordinator._build_write_result(
        uri="viking://resources/demo.md",
        root_uri="viking://resources",
        context_type="resource",
        mode="replace",
        written_bytes=7,
        queue_status=None,
        semantic_status="skipped",
        vector_status="skipped",
    )

    assert result["content_updated"] is True


@pytest.mark.asyncio
async def test_missing_generated_sidecar_is_rejected_after_exact_lock(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    events = []
    original_acquire = fake_fs._async_agfs.pathlock_acquire_exact

    async def _acquire(path):
        events.append("lock")
        return await original_acquire(path)

    fake_fs._async_agfs.pathlock_acquire_exact = _acquire
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._classify_locked_write_target = AsyncMock(
        side_effect=InvalidArgumentError("cannot create generated abstract overview directly")
    )

    with pytest.raises(InvalidArgumentError, match="cannot create generated"):
        await coordinator.write(
            uri="viking://resources/demo/.abstract.md",
            content="manual body",
            mode="replace",
            ctx=ctx,
        )

    assert events == ["lock"]
    coordinator._classify_locked_write_target.assert_awaited_once()


def test_new_memory_file_renders_from_replace_mode_and_explicit_new_file_state():
    uri = "viking://user/account-1/memories/new.md"
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())

    rendered = coordinator._render_final_bytes(
        uri,
        "new memory content",
        mode="replace",
        existing_raw=None,
        is_new_file=True,
    )

    assert MemoryFileUtils.read(rendered.decode("utf-8"), uri=uri).content == "new memory content"


def test_plan_statuses_report_deferred_parent_and_queued_vector_work():
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())
    work = SimpleNamespace(
        semantic_requested=False,
        vector_requested=True,
        semantic_action=FreshnessAction.MARK_PENDING.value,
    )

    assert coordinator._plan_statuses(work, wait=False, queue_status=None) == (
        "deferred",
        "queued",
    )


@pytest.mark.parametrize(
    ("semantic_action", "wait", "queue_status", "expected"),
    [
        (
            FreshnessAction.MARK_PENDING.value,
            False,
            None,
            ("deferred", "queued"),
        ),
        (
            FreshnessAction.NOOP.value,
            True,
            {},
            ("skipped", "complete"),
        ),
    ],
)
def test_plan_statuses_prioritize_parent_freshness_over_file_semantic_work(
    semantic_action, wait, queue_status, expected
):
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())
    work = SimpleNamespace(
        semantic_requested=True,
        vector_requested=True,
        semantic_action=semantic_action,
    )

    assert coordinator._plan_statuses(work, wait=wait, queue_status=queue_status) == expected
