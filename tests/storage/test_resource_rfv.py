from unittest.mock import AsyncMock

import pytest

from openviking.storage.resource_diff import IndexState
from openviking.storage.resource_rnfv import (
    RequestIntent,
    VectorIndexSnapshot,
    VectorRecordSnapshot,
)


def _record(record_id: str, uri: str, relative_path: str, level: int, md5: str):
    return VectorRecordSnapshot(
        record_id=record_id,
        uri=uri,
        relative_path=relative_path,
        level=level,
        fields={"md5": md5},
    )


def test_rfv_resolver_skips_complete_file_and_marks_changed_or_forced_file_stale():
    from openviking.storage.resource_rfv import (
        RFVEntry,
        RFVFormalSnapshot,
        RFVSnapshot,
        resolve_rfv_state,
    )

    root = "viking://resources/demo"
    record = _record("readme-l2", f"{root}/README.md", "README.md", 2, "same")
    formal = RFVFormalSnapshot(
        entries={
            "": RFVEntry(uri=root, relative_path="", is_dir=True),
            "README.md": RFVEntry(
                uri=f"{root}/README.md",
                relative_path="README.md",
                is_dir=False,
                level_md5s={2: "same"},
            ),
        }
    )
    vectors = VectorIndexSnapshot(
        records_by_id={record.record_id: record},
        projected_fields=frozenset({"id", "uri", "level", "md5"}),
    )

    normal = resolve_rfv_state(RFVSnapshot(RequestIntent(root, "vectors_only"), formal, vectors))
    assert normal.entries["README.md"].content_state.value == "unchanged"
    assert normal.entries["README.md"].index_state == IndexState.COMPLETE
    assert normal.entries["README.md"].level_states == {2: IndexState.COMPLETE}

    changed_formal = RFVFormalSnapshot(
        entries={
            **formal.entries,
            "README.md": RFVEntry(
                uri=f"{root}/README.md",
                relative_path="README.md",
                is_dir=False,
                level_md5s={2: "changed"},
            ),
        }
    )
    changed = resolve_rfv_state(
        RFVSnapshot(RequestIntent(root, "vectors_only"), changed_formal, vectors)
    )
    assert changed.entries["README.md"].index_state == IndexState.STALE

    forced = resolve_rfv_state(
        RFVSnapshot(RequestIntent(root, "vectors_only", force=True), formal, vectors)
    )
    assert forced.entries["README.md"].index_state == IndexState.STALE


def test_rfv_resolver_tracks_directory_levels_independently():
    from openviking.storage.resource_rfv import (
        RFVEntry,
        RFVFormalSnapshot,
        RFVSnapshot,
        resolve_rfv_state,
    )

    root = "viking://resources/demo"
    records = {
        "root-l0": _record("root-l0", root, "", 0, "abstract-same"),
        "root-l1": _record("root-l1", root, "", 1, "overview-old"),
    }
    snapshot = RFVSnapshot(
        RequestIntent(root, "vectors_only"),
        RFVFormalSnapshot(
            entries={
                "": RFVEntry(
                    uri=root,
                    relative_path="",
                    is_dir=True,
                    level_md5s={0: "abstract-same", 1: "overview-new"},
                )
            }
        ),
        VectorIndexSnapshot(
            records_by_id=records,
            projected_fields=frozenset({"id", "uri", "level", "md5"}),
        ),
    )

    resolved = resolve_rfv_state(snapshot)
    assert resolved.entries[""].index_state == IndexState.STALE
    assert resolved.entries[""].level_states == {
        0: IndexState.COMPLETE,
        1: IndexState.STALE,
    }


def test_rfv_resolver_marks_missing_and_orphan_records():
    from openviking.storage.resource_rfv import (
        RFVEntry,
        RFVFormalSnapshot,
        RFVSnapshot,
        resolve_rfv_state,
    )

    root = "viking://resources/demo"
    orphan = _record("old-l2", f"{root}/old.md", "old.md", 2, "old")
    snapshot = RFVSnapshot(
        RequestIntent(root, "vectors_only"),
        RFVFormalSnapshot(
            entries={
                "": RFVEntry(uri=root, relative_path="", is_dir=True),
                "new.md": RFVEntry(
                    uri=f"{root}/new.md",
                    relative_path="new.md",
                    is_dir=False,
                    level_md5s={2: "new"},
                ),
            }
        ),
        VectorIndexSnapshot(
            records_by_id={orphan.record_id: orphan},
            projected_fields=frozenset({"id", "uri", "level", "md5"}),
        ),
    )

    resolved = resolve_rfv_state(snapshot)
    assert resolved.entries["new.md"].index_state == IndexState.MISSING
    assert resolved.entries["old.md"].index_state == IndexState.ORPHAN
    assert resolved.entries["old.md"].content_state.value == "absent"


def test_rfv_missing_directory_sidecar_is_deleted_for_vectors_only_but_repaired_for_semantic():
    from openviking.storage.resource_rfv import (
        RFVEntry,
        RFVFormalSnapshot,
        RFVSnapshot,
        resolve_rfv_state,
    )

    root = "viking://resources/demo"
    l0 = _record("root-l0", root, "", 0, "old")
    formal = RFVFormalSnapshot({"": RFVEntry(root, "", True, {})})
    vectors = VectorIndexSnapshot({l0.record_id: l0}, frozenset({"id", "uri", "level", "md5"}))

    vectors_only = resolve_rfv_state(
        RFVSnapshot(RequestIntent(root, "vectors_only"), formal, vectors)
    )
    semantic = resolve_rfv_state(
        RFVSnapshot(RequestIntent(root, "semantic_and_vectors"), formal, vectors)
    )

    assert vectors_only.entries[""].level_states == {
        0: IndexState.ABSENT,
        1: IndexState.ABSENT,
    }
    assert semantic.entries[""].level_states == {
        0: IndexState.MISSING,
        1: IndexState.MISSING,
    }


def test_rfv_plan_compiles_per_level_repairs_and_orphan_cleanup_without_content_actions():
    from openviking.storage.context_update_plan import build_rfv_context_update_plan
    from openviking.storage.index_action import IndexAction
    from openviking.storage.resource_rfv import RFVEntry, RFVFormalSnapshot, RFVSnapshot

    root = "viking://resources/demo"
    records = {
        "root-l0": _record("root-l0", root, "", 0, "root-old"),
        "same-l2": _record("same-l2", f"{root}/same.md", "same.md", 2, "same"),
        "stale-l2": _record("stale-l2", f"{root}/stale.md", "stale.md", 2, "old"),
        "orphan-l2": _record("orphan-l2", f"{root}/orphan.md", "orphan.md", 2, "orphan"),
    }
    snapshot = RFVSnapshot(
        RequestIntent(root, "vectors_only"),
        RFVFormalSnapshot(
            entries={
                "": RFVEntry(
                    uri=root,
                    relative_path="",
                    is_dir=True,
                    level_md5s={0: "root-new", 1: "overview-new"},
                ),
                "same.md": RFVEntry(
                    uri=f"{root}/same.md",
                    relative_path="same.md",
                    is_dir=False,
                    level_md5s={2: "same"},
                ),
                "stale.md": RFVEntry(
                    uri=f"{root}/stale.md",
                    relative_path="stale.md",
                    is_dir=False,
                    level_md5s={2: "new"},
                ),
            }
        ),
        VectorIndexSnapshot(
            records_by_id=records,
            projected_fields=frozenset({"id", "uri", "level", "md5"}),
        ),
    )

    diff, plan = build_rfv_context_update_plan(
        snapshot=snapshot,
        context_type="resource",
        account_id="acc",
    )

    assert plan.content_tree_actions == ()
    assert plan.semantic_plan is None
    assert diff.entries["same.md"].level_states == {2: IndexState.COMPLETE}
    assert all(
        action.action is IndexAction.MERGE and action.upsert_fields == {}
        for action in plan.direct_index_actions
        if action.action is not IndexAction.DELETE
    )
    assert {
        (action.action, action.uri, action.level, action.record_id, action.md5)
        for action in plan.direct_index_actions
    } == {
        (IndexAction.MERGE, root, 0, "root-l0", "root-new"),
        (IndexAction.MERGE, root, 1, action_record_id(root, 1), "overview-new"),
        (IndexAction.MERGE, f"{root}/stale.md", 2, "stale-l2", "new"),
        (IndexAction.DELETE, f"{root}/orphan.md", 2, "orphan-l2", None),
    }


def test_rfv_semantic_plan_reuses_existing_semantic_actions_for_stale_nodes():
    from openviking.storage.context_update_plan import (
        SemanticAction,
        build_rfv_context_update_plan,
    )
    from openviking.storage.index_action import IndexAction
    from openviking.storage.resource_rfv import RFVEntry, RFVFormalSnapshot, RFVSnapshot

    root = "viking://resources/demo"
    records = {
        "root-l0": VectorRecordSnapshot(
            "root-l0", root, "", 0, {"md5": "old-l0", "abstract": "old root"}
        ),
        "root-l1": VectorRecordSnapshot(
            "root-l1", root, "", 1, {"md5": "old-l1", "abstract": "old overview"}
        ),
        "a-l2": VectorRecordSnapshot(
            "a-l2",
            f"{root}/a.md",
            "a.md",
            2,
            {"md5": "old-file", "abstract": "old file"},
        ),
    }
    snapshot = RFVSnapshot(
        RequestIntent(root, "semantic_and_vectors"),
        RFVFormalSnapshot(
            {
                "": RFVEntry(root, "", True, {0: "new-l0", 1: "new-l1"}),
                "a.md": RFVEntry(f"{root}/a.md", "a.md", False, {2: "new-file"}),
            }
        ),
        VectorIndexSnapshot(
            records,
            frozenset({"id", "uri", "level", "md5", "abstract"}),
        ),
    )

    _diff, plan = build_rfv_context_update_plan(
        snapshot=snapshot, context_type="resource", account_id="acc"
    )

    assert plan.content_tree_actions == ()
    assert plan.direct_index_actions == ()
    entries = {entry.relative_path: entry for entry in plan.semantic_plan.tree.entries}
    assert entries[""].semantic_action is SemanticAction.AGGREGATE
    assert entries["a.md"].semantic_action is SemanticAction.GENERATE
    assert entries[""].repair is True
    assert entries["a.md"].repair is True
    assert [(slot.level, slot.action) for slot in entries[""].index_slots] == [
        (0, IndexAction.MERGE),
        (1, IndexAction.MERGE),
    ]
    assert [(slot.level, slot.action) for slot in entries["a.md"].index_slots] == [
        (2, IndexAction.MERGE)
    ]


def test_rfv_semantic_file_uses_existing_file_refresh_without_duplicate_direct_upsert():
    from openviking.storage.context_update_plan import build_rfv_context_update_plan
    from openviking.storage.resource_rfv import RFVEntry, RFVFormalSnapshot, RFVSnapshot

    uri = "viking://resources/demo.md"
    snapshot = RFVSnapshot(
        RequestIntent(uri, "semantic_and_vectors", force=True),
        RFVFormalSnapshot({"": RFVEntry(uri, "", False, {2: "current"})}),
        VectorIndexSnapshot(
            {"file-l2": _record("file-l2", uri, "", 2, "current")},
            frozenset({"id", "uri", "level", "md5", "abstract"}),
        ),
    )

    _diff, plan = build_rfv_context_update_plan(
        snapshot=snapshot, context_type="resource", account_id="acc"
    )

    assert plan.semantic_plan is None
    assert plan.direct_index_actions == ()
    assert plan.file_refresh.file_uri == uri
    assert plan.file_refresh.md5 == "current"


def test_rfv_semantic_file_is_noop_when_fingerprint_and_scalars_are_complete():
    from openviking.storage.context_update_plan import build_rfv_context_update_plan
    from openviking.storage.resource_rfv import RFVEntry, RFVFormalSnapshot, RFVSnapshot

    uri = "viking://resources/demo.md"
    snapshot = RFVSnapshot(
        RequestIntent(uri, "semantic_and_vectors"),
        RFVFormalSnapshot({"": RFVEntry(uri, "", False, {2: "same"})}),
        VectorIndexSnapshot(
            {"file-l2": _record("file-l2", uri, "", 2, "same")},
            frozenset({"id", "uri", "level", "md5", "abstract"}),
        ),
    )

    _diff, plan = build_rfv_context_update_plan(
        snapshot=snapshot, context_type="resource", account_id="acc"
    )

    assert plan.is_noop()


def test_rfv_semantic_file_applies_scalar_only_update_without_refresh():
    from openviking.storage.context_update_plan import build_rfv_context_update_plan
    from openviking.storage.resource_rfv import RFVEntry, RFVFormalSnapshot, RFVSnapshot
    from openviking.storage.resource_rnfv import ScalarIntent

    uri = "viking://resources/demo.md"
    snapshot = RFVSnapshot(
        RequestIntent(
            uri,
            "semantic_and_vectors",
            scalar_intents=(ScalarIntent("search_tags", "replace", ("team=search",)),),
        ),
        RFVFormalSnapshot({"": RFVEntry(uri, "", False, {2: "same"})}),
        VectorIndexSnapshot(
            {"file-l2": _record("file-l2", uri, "", 2, "same")},
            frozenset({"id", "uri", "level", "md5", "abstract", "search_tags"}),
        ),
    )

    _diff, plan = build_rfv_context_update_plan(
        snapshot=snapshot, context_type="resource", account_id="acc"
    )

    assert plan.file_refresh is None
    assert [action.action.value for action in plan.direct_index_actions] == ["update_fields"]


def test_rfv_semantic_file_missing_l2_refreshes_with_request_scalars():
    from openviking.storage.context_update_plan import build_rfv_context_update_plan
    from openviking.storage.resource_rfv import RFVEntry, RFVFormalSnapshot, RFVSnapshot
    from openviking.storage.resource_rnfv import ScalarIntent

    uri = "viking://resources/demo.md"
    snapshot = RFVSnapshot(
        RequestIntent(
            uri,
            "semantic_and_vectors",
            scalar_intents=(ScalarIntent("search_tags", "replace", ("team=search",)),),
        ),
        RFVFormalSnapshot({"": RFVEntry(uri, "", False, {2: "same"})}),
        VectorIndexSnapshot(
            {},
            frozenset({"id", "uri", "level", "md5", "abstract", "search_tags"}),
        ),
    )

    _diff, plan = build_rfv_context_update_plan(
        snapshot=snapshot, context_type="resource", account_id="acc"
    )

    assert plan.file_refresh is not None
    assert plan.file_refresh.file_uri == uri
    assert plan.direct_index_actions == ()


def action_record_id(uri: str, level: int) -> str:
    from openviking.storage.vector_ids import vector_record_id

    return vector_record_id("acc", uri, level)


class _SnapshotFS:
    def __init__(self, root: str):
        self.root = root
        self.tree = AsyncMock(
            return_value=[
                {"uri": f"{root}/docs", "rel_path": "docs", "isDir": True},
                {
                    "uri": f"{root}/docs/.abstract.md",
                    "rel_path": "docs/.abstract.md",
                    "isDir": False,
                },
                {
                    "uri": f"{root}/docs/.overview.md",
                    "rel_path": "docs/.overview.md",
                    "isDir": False,
                },
                {"uri": f"{root}/docs/a.md", "rel_path": "docs/a.md", "isDir": False},
            ]
        )
        self.stat = AsyncMock(return_value={"isDir": True})
        self.read_file_bytes = AsyncMock(
            side_effect=lambda uri, ctx=None: {
                f"{root}/docs/.abstract.md": b"docs abstract",
                f"{root}/docs/.overview.md": b"docs overview",
                f"{root}/docs/a.md": b"file body",
            }[uri]
        )


class _SnapshotDB:
    def __init__(self):
        self.get_incremental_inventory_under_uri = AsyncMock(return_value={})


@pytest.mark.asyncio
async def test_build_rfv_snapshot_reads_one_f_inventory_one_v_inventory_and_each_source_once():
    from openviking.storage.resource_rfv import build_rfv_snapshot
    from openviking.utils.content_hash import content_md5

    root = "viking://resources/demo"
    fs = _SnapshotFS(root)
    db = _SnapshotDB()

    snapshot = await build_rfv_snapshot(
        viking_fs=fs,
        vikingdb=db,
        target_uri=root,
        ctx=object(),
        request_intent=RequestIntent(root, "vectors_only"),
        recursive=True,
        root_is_dir=True,
    )

    fs.tree.assert_awaited_once()
    fs.stat.assert_not_awaited()
    db.get_incremental_inventory_under_uri.assert_awaited_once()
    assert fs.read_file_bytes.await_count == 3
    assert snapshot.formal.entries["docs"].level_md5s == {
        0: content_md5(b"docs abstract"),
        1: content_md5(b"docs overview"),
    }
    assert snapshot.formal.entries["docs/a.md"].level_md5s == {2: content_md5(b"file body")}
    assert snapshot.source_contents == {
        (f"{root}/docs", 0): "docs abstract",
        (f"{root}/docs", 1): "docs overview",
        (f"{root}/docs/a.md", 2): b"file body",
    }


@pytest.mark.asyncio
async def test_build_rfv_snapshot_non_recursive_directory_does_not_walk_descendants():
    from openviking.storage.resource_rfv import build_rfv_snapshot

    root = "viking://resources/demo"
    fs = _SnapshotFS(root)
    db = _SnapshotDB()

    snapshot = await build_rfv_snapshot(
        viking_fs=fs,
        vikingdb=db,
        target_uri=root,
        ctx=object(),
        request_intent=RequestIntent(root, "vectors_only"),
        recursive=False,
    )

    fs.tree.assert_not_awaited()
    assert set(snapshot.formal.entries) == {""}
    assert db.get_incremental_inventory_under_uri.await_args.kwargs["recursive"] is False


@pytest.mark.asyncio
async def test_build_rfv_snapshot_keeps_dotfiles_and_excludes_control_files():
    from openviking.storage.resource_rfv import build_rfv_snapshot

    root = "viking://resources/demo"
    fs = _SnapshotFS(root)
    fs.tree.return_value = [
        {"uri": f"{root}/.env", "rel_path": ".env", "isDir": False},
        {"uri": f"{root}/.source.json", "rel_path": ".source.json", "isDir": False},
        {"uri": f"{root}/.path.ovlock", "rel_path": ".path.ovlock", "isDir": False},
    ]
    fs.read_file_bytes.side_effect = lambda uri, ctx=None: b"KEY=value"

    snapshot = await build_rfv_snapshot(
        viking_fs=fs,
        vikingdb=_SnapshotDB(),
        target_uri=root,
        ctx=object(),
        request_intent=RequestIntent(root, "vectors_only"),
    )

    assert set(snapshot.formal.entries) == {"", ".env"}


@pytest.mark.asyncio
async def test_build_rfv_snapshot_reads_skill_source_metadata_from_same_f_inventory():
    from openviking.storage.resource_rfv import build_rfv_snapshot

    root = "viking://user/alice/skills/demo"
    fs = _SnapshotFS(root)
    fs.tree.return_value = [
        {"uri": f"{root}/.source.json", "rel_path": ".source.json", "isDir": False}
    ]
    fs.read_file_bytes.side_effect = lambda uri, ctx=None: (
        b'{"type":"git","source":"https://example/repo"}'
    )

    snapshot = await build_rfv_snapshot(
        viking_fs=fs,
        vikingdb=_SnapshotDB(),
        target_uri=root,
        ctx=object(),
        request_intent=RequestIntent(root, "semantic_and_vectors"),
    )

    assert snapshot.source_metadata == {
        "kind": "git",
        "uri": "https://example/repo",
        "path": "https://example/repo",
    }
    assert ".source.json" not in snapshot.formal.entries


def test_rfv_resolver_rejects_formal_entry_outside_request_scope():
    from openviking.storage.resource_rfv import (
        RFVEntry,
        RFVFormalSnapshot,
        RFVSnapshot,
        resolve_rfv_state,
    )

    root = "viking://resources/demo"
    snapshot = RFVSnapshot(
        RequestIntent(root, "vectors_only"),
        RFVFormalSnapshot(
            {
                "outside.md": RFVEntry(
                    "viking://resources/other.md",
                    "outside.md",
                    False,
                    {2: "fingerprint"},
                )
            }
        ),
        VectorIndexSnapshot({}, frozenset({"id", "uri", "level", "md5"})),
    )

    with pytest.raises(ValueError, match="outside target"):
        resolve_rfv_state(snapshot)


@pytest.mark.asyncio
async def test_build_rfv_snapshot_rejects_denied_reused_formal_inventory():
    from openviking.storage.resource_rfv import build_rfv_snapshot

    root = "viking://resources/demo"
    fs = _SnapshotFS(root)

    with pytest.raises(ValueError, match="incomplete formal tree"):
        await build_rfv_snapshot(
            viking_fs=fs,
            vikingdb=_SnapshotDB(),
            target_uri=root,
            ctx=object(),
            request_intent=RequestIntent(root, "vectors_only"),
            root_is_dir=True,
            formal_inventory=(
                True,
                [
                    {
                        "uri": f"{root}/private.md",
                        "isDir": False,
                        "access": "denied",
                    }
                ],
                True,
            ),
        )
