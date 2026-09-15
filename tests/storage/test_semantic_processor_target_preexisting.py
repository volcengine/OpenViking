from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_plan import (
    PlannedScalarUpdate,
    SemanticPlan,
    SemanticTreeEntry,
    SemanticTreeSnapshot,
    VectorRecordRef,
)
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs import SyncDiff


class _FakeVikingFS:
    async def exists(self, uri, ctx=None):
        return True


class _SyncWrapperVikingFS:
    """Fake FS that verifies the wrapper calls sync_tree and post-processes correctly."""

    def __init__(self, target_exists=True):
        self.target_exists = target_exists
        self.sync_tree_calls = []
        self.deleted_temp = []
        self.mutation_leases = []
        self.sidecar_rewrite_calls = []

    async def exists(self, uri, ctx=None):
        if uri.startswith("viking://resources/root"):
            return self.target_exists
        return True

    async def sync_tree(
        self,
        root_uri,
        target_uri,
        *,
        ctx=None,
        file_change_status=None,
        lease_ref=None,
        delete_temp_after=False,
    ):
        self.sync_tree_calls.append(
            (root_uri, target_uri, file_change_status, lease_ref, delete_temp_after)
        )
        self.mutation_leases.append(("sync_tree", root_uri, target_uri, lease_ref))
        return SyncDiff(updated_files=["viking://resources/root/a.md"])

    async def delete_temp(self, uri, ctx=None):
        self.mutation_leases.append(("delete_temp", uri, None))
        self.deleted_temp.append(uri)

    async def read_file(self, uri, ctx=None):
        return "{}"

    async def stat(self, uri, ctx=None):
        raise FileNotFoundError(uri)

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.mutation_leases.append(("write_file", uri, lease_ref))

    async def glob(self, pattern, uri=None, ctx=None):
        return {"matches": []}


class _FakeDagExecutor:
    calls = []
    runs = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stale = False
        self.scheduled_vector_record_ids = frozenset()
        _FakeDagExecutor.calls.append(kwargs)

    async def run(self, root_uri):
        self.root_uri = root_uri
        _FakeDagExecutor.runs.append(root_uri)

    def get_stats(self):
        from openviking.storage.queuefs.semantic_dag import DagStats

        return DagStats()


@pytest.mark.asyncio
async def test_target_source_syncs_before_semantic_dag(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )

    _FakeDagExecutor.calls = []
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
    processor._enqueue_parent_refresh = AsyncMock()
    processor._cleanup_local_artifact = AsyncMock()
    processor._sync_topdown_recursive = AsyncMock(
        return_value=SyncDiff(
            updated_files=["viking://resources/org/repo/a.md"],
        )
    )
    msg = SemanticMsg(
        uri="viking://temp/import_root/repository",
        target_uri="viking://resources/org/repo",
        context_type="resource",
        target_preexisting=True,
    )

    await processor.on_dequeue(msg.to_dict())

    assert _FakeDagExecutor.calls[0]["incremental_update"] is True
    assert _FakeDagExecutor.calls[0]["target_uri"] == "viking://resources/org/repo"
    assert _FakeDagExecutor.calls[0]["changes"] == {
        "added": [],
        "modified": ["viking://resources/org/repo/a.md"],
        "deleted": [],
    }
    assert _FakeDagExecutor.runs == ["viking://resources/org/repo"]
    processor._cleanup_local_artifact.assert_awaited_once_with(msg)


@pytest.mark.asyncio
async def test_semantic_plan_skips_sync_and_runs_only_minimal_roots(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("src", "directory", "unchanged"),
                SemanticTreeEntry("src/a.py", "file", "modified", md5="a"),
                SemanticTreeEntry("docs", "directory", "unchanged"),
                SemanticTreeEntry("docs/b.md", "file", "modified", md5="b"),
            )
        ),
        orphan_vector_deletes=(
            VectorRecordRef(
                record_id="ghost-l2",
                uri="viking://resources/repo/ghost.py",
                level=2,
            ),
        ),
    )
    _FakeDagExecutor.calls = []
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
    processor._sync_topdown_recursive = AsyncMock(
        side_effect=AssertionError("plan path must not sync")
    )
    processor._enqueue_plan_vector_deletes = AsyncMock()
    processor._enqueue_parent_refresh = AsyncMock()
    msg = SemanticMsg(
        uri=plan.root_uri,
        context_type="resource",
        plan_version=1,
        plan=plan,
    )

    await processor.on_dequeue(msg.to_dict())

    processor._sync_topdown_recursive.assert_not_awaited()
    processor._enqueue_plan_vector_deletes.assert_awaited_once_with(msg, plan)
    assert _FakeDagExecutor.runs == [
        "viking://resources/repo/docs",
        "viking://resources/repo/src",
    ]
    assert all(call["semantic_plan"] == plan for call in _FakeDagExecutor.calls)


@pytest.mark.asyncio
async def test_semantic_plan_runs_disconnected_nested_root(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "unchanged"),
                SemanticTreeEntry("root.py", "file", "modified", md5="root"),
                SemanticTreeEntry("docs/deep/tests", "directory", "unchanged"),
                SemanticTreeEntry(
                    "docs/deep/tests/test_a.py", "file", "modified", md5="deep"
                ),
            )
        ),
    )
    _FakeDagExecutor.calls = []
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
    processor._enqueue_plan_vector_deletes = AsyncMock()
    processor._enqueue_parent_refresh = AsyncMock()
    msg = SemanticMsg(
        uri=plan.root_uri,
        context_type="resource",
        plan_version=1,
        plan=plan,
    )

    await processor.on_dequeue(msg.to_dict())

    assert _FakeDagExecutor.runs == [
        "viking://resources/repo",
        "viking://resources/repo/docs/deep/tests",
    ]


@pytest.mark.asyncio
async def test_plan_added_entry_does_not_delete_same_level_stale_record(monkeypatch):
    # A re-added file (F missing, V had a stale same-level L2) must NOT enqueue a
    # delete for that record: the re-vectorize upsert reuses the same
    # deterministic (uri, level) id and overwrites it. Deleting here would race
    # that upsert on one id.
    from openviking.storage.queuefs.semantic_plan import IndexedRecordSnapshot

    queue = SimpleNamespace(enqueue=AsyncMock(return_value="queued"))
    manager = SimpleNamespace(
        EMBEDDING="embedding",
        get_queue=lambda *_args, **_kwargs: queue,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: manager,
    )
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry(
                    "a.py",
                    "file",
                    "added",
                    md5="new",
                    indexed_records=(IndexedRecordSnapshot("stale-l2", 2),),
                ),
            )
        ),
    )
    msg = SemanticMsg(
        uri=plan.root_uri,
        context_type="resource",
        plan_version=1,
        plan=plan,
    )

    await SemanticProcessor()._enqueue_plan_vector_deletes(msg, plan)

    # No orphan_vector_deletes and no deleted entries => nothing to delete.
    queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_scalar_updates_enqueue_update_fields_without_running_dag(monkeypatch):
    queue = SimpleNamespace(enqueue=AsyncMock(return_value="queued"))
    manager = SimpleNamespace(
        EMBEDDING="embedding",
        get_queue=lambda *_args, **_kwargs: queue,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: manager,
    )
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(SemanticTreeEntry("", "directory", "unchanged"),)
        ),
        scalar_updates=(
            PlannedScalarUpdate(
                record_id="a-l2",
                uri="viking://resources/repo/a.py",
                level=2,
                fields={"search_tags": ["team=search"]},
            ),
        ),
    )
    msg = SemanticMsg(
        uri=plan.root_uri,
        context_type="resource",
        plan_version=2,
        plan=plan,
    )

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
    processor._enqueue_plan_vector_deletes = AsyncMock()
    processor._cleanup_local_artifact = AsyncMock()

    await processor.on_dequeue(msg.to_dict())

    queued = queue.enqueue.await_args.args[0]
    assert queued.operation.value == "update_fields"
    assert queued.record_ids == ["a-l2"]
    assert queued.update_fields["search_tags"] == ["team=search"]
    assert queued.update_fields["updated_at"]
    assert _FakeDagExecutor.runs == []


@pytest.mark.asyncio
async def test_plan_scalar_updates_skip_records_already_handled_by_dag(monkeypatch):
    queue = SimpleNamespace(enqueue=AsyncMock(return_value="queued"))
    manager = SimpleNamespace(
        EMBEDDING="embedding",
        get_queue=lambda *_args, **_kwargs: queue,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: manager,
    )
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(entries=()),
        scalar_updates=(
            PlannedScalarUpdate(
                record_id="a-l2",
                uri="viking://resources/repo/a.py",
                level=2,
                fields={"search_tags": ["team=search"]},
            ),
            PlannedScalarUpdate(
                record_id="b-l2",
                uri="viking://resources/repo/b.py",
                level=2,
                fields={"search_tags": ["team=search"]},
            ),
        ),
    )
    msg = SemanticMsg(
        uri=plan.root_uri, context_type="resource", plan_version=2, plan=plan
    )

    await SemanticProcessor()._enqueue_plan_scalar_updates(
        msg, plan, exclude_record_ids={"a-l2"}
    )

    queued = queue.enqueue.await_args_list
    assert len(queued) == 1
    assert queued[0].args[0].record_ids == ["b-l2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("new_kind", "expected_deleted_ids"),
    [
        ("file", ["old-l0", "old-l1"]),
        ("directory", ["old-l2"]),
    ],
)
async def test_plan_structural_flip_deletes_only_cross_level_stale_records(
    monkeypatch, new_kind, expected_deleted_ids
):
    # Structural entries carry the old inventory snapshots directly on the added
    # entry. Delete only levels invalid for the new kind: dir -> file removes old
    # L0/L1, file -> dir removes old L2. A same-level record is intentionally kept
    # because the rebuild upsert reuses and overwrites its deterministic id.
    from openviking.storage.queuefs.semantic_plan import IndexedRecordSnapshot

    queue = SimpleNamespace(enqueue=AsyncMock(return_value="queued"))
    manager = SimpleNamespace(
        EMBEDDING="embedding",
        get_queue=lambda *_args, **_kwargs: queue,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: manager,
    )
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry(
                    "mod",
                    new_kind,
                    "added",
                    md5="new" if new_kind == "file" else None,
                    indexed_records=(
                        IndexedRecordSnapshot("old-l0", 0),
                        IndexedRecordSnapshot("old-l1", 1),
                        IndexedRecordSnapshot("old-l2", 2),
                    ),
                ),
            )
        ),
    )
    msg = SemanticMsg(
        uri=plan.root_uri,
        context_type="resource",
        plan_version=1,
        plan=plan,
    )

    await SemanticProcessor()._enqueue_plan_vector_deletes(msg, plan)

    queued = queue.enqueue.await_args.args[0]
    assert queued.record_ids == expected_deleted_ids


@pytest.mark.asyncio
async def test_local_artifact_is_cleaned_after_semantic_success(monkeypatch, tmp_path):
    from openviking.parse.output import LocalParseOutputStore

    store = LocalParseOutputStore(local_root=str(tmp_path))
    raw_ref = await store.create_artifact()
    from openviking.parse.output import ParseArtifactRef

    ref = ParseArtifactRef(
        backend=raw_ref.backend,
        root=raw_ref.root,
        resource_rel="repository",
        root_type=raw_ref.root_type,
    )
    await store.write_bytes(ref, "repository/a.py", b"a")

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_openviking_config",
        lambda: SimpleNamespace(
            storage=SimpleNamespace(
                parse_output=SimpleNamespace(resolved_local_root=lambda: str(tmp_path))
            )
        ),
    )
    # store_for_artifact_ref resolves the local root from the config source.
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(
            storage=SimpleNamespace(
                parse_output=SimpleNamespace(resolved_local_root=lambda: str(tmp_path))
            )
        ),
    )

    processor = SemanticProcessor()
    processor._enqueue_parent_refresh = AsyncMock()
    msg = SemanticMsg(
        uri="viking://resources/root",
        context_type="resource",
        artifact_ref=ref.to_dict(),
        artifact_files=["a.py"],
    )

    await processor.on_dequeue(msg.to_dict())

    assert not tmp_path.joinpath(ref.root).exists()


@pytest.mark.asyncio
async def test_local_incremental_reads_final_target_bytes_after_apply(monkeypatch, tmp_path):
    from openviking.parse.output import LocalParseOutputStore, ParseArtifactRef

    store = LocalParseOutputStore(local_root=str(tmp_path))
    raw_ref = await store.create_artifact()
    ref = ParseArtifactRef(
        backend=raw_ref.backend,
        root=raw_ref.root,
        resource_rel="repository",
        root_type=raw_ref.root_type,
    )
    await store.write_bytes(ref, "repository/a.md", b"unrewritten")
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_openviking_config",
        lambda: SimpleNamespace(
            storage=SimpleNamespace(
                parse_output=SimpleNamespace(resolved_local_root=lambda: str(tmp_path))
            )
        ),
    )
    _FakeDagExecutor.calls = []
    processor = SemanticProcessor()
    processor._enqueue_parent_refresh = AsyncMock()
    msg = SemanticMsg(
        uri="viking://resources/root",
        context_type="resource",
        target_preexisting=True,
        changes={"modified": ["viking://resources/root/a.md"]},
        artifact_ref=ref.to_dict(),
        artifact_files=["a.md"],
    )

    await processor.on_dequeue(msg.to_dict())

    assert _FakeDagExecutor.calls[0]["artifact_files"] == ["a.md"]
    assert _FakeDagExecutor.calls[0]["target_uri"] == "viking://resources/root"


@pytest.mark.asyncio
async def test_local_initial_reads_final_target_bytes_after_apply(monkeypatch, tmp_path):
    from openviking.parse.output import LocalParseOutputStore, ParseArtifactRef

    store = LocalParseOutputStore(local_root=str(tmp_path))
    raw_ref = await store.create_artifact()
    ref = ParseArtifactRef(
        backend=raw_ref.backend,
        root=raw_ref.root,
        resource_rel="repository",
        root_type=raw_ref.root_type,
    )
    await store.write_bytes(ref, "repository/a.md", b"unrewritten")
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_openviking_config",
        lambda: SimpleNamespace(
            storage=SimpleNamespace(
                parse_output=SimpleNamespace(resolved_local_root=lambda: str(tmp_path))
            )
        ),
    )
    _FakeDagExecutor.calls = []
    processor = SemanticProcessor()
    processor._enqueue_parent_refresh = AsyncMock()
    msg = SemanticMsg(
        uri="viking://resources/root",
        context_type="resource",
        target_preexisting=False,
        artifact_ref=ref.to_dict(),
        artifact_files=["a.md"],
    )

    await processor.on_dequeue(msg.to_dict())

    assert _FakeDagExecutor.calls[0]["artifact_files"] == ["a.md"]


@pytest.mark.asyncio
async def test_stale_content_write_keeps_file_work_without_directory_aggregation(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.is_semantic_msg_stale",
        lambda msg: bool(msg.coalesce_key),
    )

    _FakeDagExecutor.calls = []
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
    processor._enqueue_parent_refresh = AsyncMock()
    changed = "viking://resources/wiki/changed.md"
    msg = SemanticMsg(
        uri="viking://resources/wiki",
        context_type="resource",
        recursive=False,
        coalesce_key="resource|wiki",
        coalesce_version=1,
        changes={"modified": [changed], "deleted": ["viking://resources/wiki/old.md"]},
        generation_trigger="content_write",
    )

    await processor.on_dequeue(msg.to_dict())

    assert _FakeDagExecutor.calls[0]["aggregate_directory"] is False
    assert _FakeDagExecutor.calls[0]["changes"] == {"modified": [changed]}
    assert _FakeDagExecutor.calls[0]["coalesce_key"] == ""
    assert _FakeDagExecutor.runs == ["viking://resources/wiki"]
    processor._enqueue_parent_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("recursive", [False, True])
async def test_memory_reindex_uses_semantic_dag(monkeypatch, recursive):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )

    _FakeDagExecutor.calls = []
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
    processor._process_memory_directory = AsyncMock()
    msg = SemanticMsg(
        uri="viking://user/alice/memories/preferences",
        context_type="memory",
        recursive=recursive,
        generation_trigger="reindex",
        use_hierarchical_aggregation=True,
    )

    await processor.on_dequeue(msg.to_dict())

    processor._process_memory_directory.assert_not_awaited()
    assert _FakeDagExecutor.calls[0]["context_type"] == "memory"
    assert _FakeDagExecutor.calls[0]["recursive"] is recursive
    assert _FakeDagExecutor.runs == [msg.uri]


@pytest.mark.asyncio
async def test_memory_trigger_does_not_select_hierarchical_aggregation(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )

    processor = SemanticProcessor()
    processor._process_memory_directory = AsyncMock()
    msg = SemanticMsg(
        uri="viking://user/alice/memories/preferences",
        context_type="memory",
        generation_trigger="reindex",
    )

    await processor.on_dequeue(msg.to_dict())

    processor._process_memory_directory.assert_awaited_once()


@pytest.mark.asyncio
async def test_content_copy_does_not_enqueue_ancestor_refresh(monkeypatch):
    processor = SemanticProcessor()
    plan_refresh = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.plan_abstract_overview_refresh",
        plan_refresh,
    )
    msg = SemanticMsg(
        uri="viking://resources/archive",
        context_type="resource",
        generation_trigger="content_copy",
    )

    await processor._enqueue_parent_refresh(
        msg,
        "viking://resources/archive/copied.jpg",
        l0_body_changed=True,
    )

    plan_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_wrapper_delegates_to_sync_tree_and_cleans_temp(monkeypatch):
    """The wrapper calls viking_fs.sync_tree and then deletes the temp tree."""
    fake_fs = _SyncWrapperVikingFS(target_exists=True)
    lease = {
        "lease_ref": "outer-tree-ref",
        "owner_id": "outer-tree-owner",
        "owned": False,
    }
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.rewrite_image_uris",
        AsyncMock(),
    )

    diff = await SemanticProcessor()._sync_topdown_recursive(
        "viking://temp/import",
        "viking://resources/root",
        lock=lease,
    )

    assert len(fake_fs.sync_tree_calls) == 1
    root_uri, target_uri, fcs, lr, dta = fake_fs.sync_tree_calls[0]
    assert root_uri == "viking://temp/import"
    assert target_uri == "viking://resources/root"
    assert lr is lease
    assert dta is False
    assert diff.to_changes() == {
        "added": [],
        "modified": ["viking://resources/root/a.md"],
        "deleted": [],
    }
    assert fake_fs.deleted_temp == ["viking://temp/import"]


@pytest.mark.asyncio
async def test_sync_wrapper_whole_tree_mv_for_new_target(monkeypatch):
    """When the target does not exist, sync_tree does a full mv and the wrapper does not delete (source is gone)."""
    fake_fs = _SyncWrapperVikingFS(target_exists=False)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.rewrite_image_uris",
        AsyncMock(),
    )

    await SemanticProcessor()._sync_topdown_recursive(
        "viking://temp/import",
        "viking://resources/root",
        lock=None,
    )

    assert len(fake_fs.sync_tree_calls) == 1
    assert fake_fs.deleted_temp == []  # whole-tree mv already consumed the source


@pytest.mark.asyncio
async def test_sync_missing_source_never_touches_target(monkeypatch):
    fake_fs = AsyncMock()
    fake_fs.exists.return_value = False
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )

    with pytest.raises(FileNotFoundError, match="refusing to sync"):
        await SemanticProcessor()._sync_topdown_recursive(
            "viking://temp/missing",
            "viking://resources/root",
            lock=None,
        )

    fake_fs.sync_tree.assert_not_awaited()
    fake_fs.rm.assert_not_awaited()
