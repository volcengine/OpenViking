# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from openviking.storage import resource_diff
from openviking.storage.index_action import FieldPatch


def test_context_plan_has_explicit_actions_and_compact_semantic_roundtrip():
    from openviking.storage.context_update_plan import (
        ContextUpdatePlan,
        IndexSlot,
        SemanticAction,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )

    entry = SemanticTreeEntry(
        relative_path="a.py",
        kind="file",
        content_state="modified",
        semantic_action=SemanticAction.GENERATE,
        md5="new",
        index_slots=(
            IndexSlot(
                level=2,
                record_id="external-id",
                existing_fields={"abstract": "old"},
                action="upsert",
                upsert_fields={"search_tags": ["new"]},
            ),
        ),
    )
    plan = ContextUpdatePlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        semantic_plan=SemanticPlan(
            root_uri="viking://resources/repo",
            context_type="resource",
            tree=SemanticTreeSnapshot(
                (
                    SemanticTreeEntry("", "directory", "unchanged", "aggregate"),
                    entry,
                )
            ),
        ),
    )
    payload = plan.to_dict()
    restored = ContextUpdatePlan.from_dict(payload)
    assert restored == plan
    node = next(
        item
        for item in payload["semantic_plan"]["tree"]["entries"]
        if item["relative_path"] == "a.py"
    )
    assert "indexed_records" not in node
    assert "previous_abstracts" not in node
    assert "uri" not in node["index_slots"][0]
    assert node["index_slots"][0]["record_id"] == "external-id"


def test_index_actions_reject_unconsumed_field_patch_state():
    from openviking.storage.context_update_plan import DirectIndexAction, IndexSlot

    patch = FieldPatch({"search_tags": ["scope=new"]})
    with pytest.raises(ValueError, match="upsert uses resolved upsert_fields"):
        DirectIndexAction(
            "upsert",
            "viking://resources/repo/a.py",
            2,
            "a-l2",
            field_patch=patch,
        )
    with pytest.raises(ValueError, match="fallback_to_patch requires"):
        IndexSlot(2, "a-l2", action="upsert", fallback_to_patch=True)


def test_context_plan_reads_legacy_flat_patch_fields():
    from openviking.storage.context_update_plan import ContextUpdatePlan

    restored = ContextUpdatePlan.from_dict(
        {
            "root_uri": "viking://resources/repo",
            "context_type": "resource",
            "direct_index_actions": [
                {
                    "action": "update_fields",
                    "uri": "viking://resources/repo/a.py",
                    "level": 2,
                    "record_id": "a-l2",
                    "fields": {"search_tags": ["scope=new"]},
                    "field_modes": {"search_tags": "append"},
                    "initial_fields": {"vector": [0.1, 0.2]},
                    "search_tag_mode": "append",
                }
            ],
        }
    )

    action = restored.direct_index_actions[0]
    assert action.upsert_fields == {}
    assert action.field_patch == FieldPatch(
        {"search_tags": ["scope=new"]},
        {"search_tags": "append"},
        {"vector": [0.1, 0.2]},
    )


def test_legacy_semantic_slot_drops_inert_fallback_without_patch():
    from openviking.storage.context_update_plan import IndexSlot

    slot = IndexSlot.from_dict(
        {
            "level": 2,
            "record_id": "a-l2",
            "action": "upsert",
            "fields": {},
            "update_fields": {},
            "field_modes": {},
            "fallback_update_fields": True,
        }
    )

    assert slot.field_patch is None
    assert slot.fallback_to_patch is False


def test_after_content_commit_keeps_only_derived_actions():
    from openviking.storage.context_update_plan import (
        ContentTreeAction,
        ContextUpdatePlan,
        DirectIndexAction,
    )

    plan = ContextUpdatePlan(
        "viking://resources/repo",
        "resource",
        content_tree_actions=(
            ContentTreeAction(
                "upsert",
                "a.py",
                new_kind="file",
                artifact_path="repository/a.py",
                md5="new",
            ),
        ),
        direct_index_actions=(
            DirectIndexAction("delete", "viking://resources/repo/old.py", 2, "old-l2"),
        ),
    )

    committed = plan.after_content_commit()

    assert committed.content_tree_actions == ()
    assert committed.semantic_plan is plan.semantic_plan
    assert committed.direct_index_actions is plan.direct_index_actions


def test_context_plan_rejects_conflicting_record_operations():
    from openviking.storage.context_update_plan import (
        ContextUpdatePlan,
        DirectIndexAction,
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )

    semantic = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            (
                SemanticTreeEntry("", "directory", "unchanged", "aggregate"),
                SemanticTreeEntry(
                    "a.py",
                    "file",
                    "modified",
                    "generate",
                    md5="new",
                    index_slots=(
                        IndexSlot(
                            2,
                            "same-id",
                            {"abstract": "old"},
                            action="upsert",
                        ),
                    ),
                ),
            )
        ),
    )
    with pytest.raises(ValueError, match="conflict"):
        ContextUpdatePlan(
            root_uri=semantic.root_uri,
            context_type="resource",
            semantic_plan=semantic,
            direct_index_actions=(
                DirectIndexAction("delete", semantic.root_uri + "/a.py", 2, "same-id"),
            ),
        )


def test_semantic_plan_rejects_disconnected_or_mistyped_actions():
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )

    with pytest.raises(ValueError, match="root"):
        SemanticPlan(
            "viking://resources/repo",
            "resource",
            SemanticTreeSnapshot(
                (
                    SemanticTreeEntry(
                        "src/a.py",
                        "file",
                        "modified",
                        "generate",
                        index_slots=(IndexSlot(2, "a-l2", action="upsert"),),
                    ),
                )
            ),
        )
    with pytest.raises(ValueError, match="directory.*aggregate"):
        SemanticTreeEntry("src", "directory", "modified", "generate")
    with pytest.raises(ValueError, match="file.*generate"):
        SemanticTreeEntry("a.py", "file", "modified", "aggregate", md5="m")
    with pytest.raises(ValueError, match="ancestor.*aggregate"):
        SemanticPlan(
            "viking://resources/repo",
            "resource",
            SemanticTreeSnapshot(
                (
                    SemanticTreeEntry(
                        "",
                        "directory",
                        "unchanged",
                        "reuse",
                        index_slots=(IndexSlot(0, "root-l0", {"abstract": "root"}),),
                    ),
                    SemanticTreeEntry(
                        "a.py",
                        "file",
                        "modified",
                        "generate",
                        md5="new",
                        index_slots=(IndexSlot(2, "a-l2", action="upsert"),),
                    ),
                )
            ),
        )


def test_semantic_plan_validates_ancestors_without_rescanning_entries():
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )

    class CountingEntries:
        def __init__(self, entries):
            self._entries = tuple(entries)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return iter(self._entries)

    directories = [
        "/".join(["src", *[f"level-{index}" for index in range(depth)]]) for depth in range(8)
    ]
    entries = CountingEntries(
        [SemanticTreeEntry("", "directory", "unchanged", "aggregate")]
        + [SemanticTreeEntry(path, "directory", "unchanged", "aggregate") for path in directories]
        + [
            SemanticTreeEntry(
                directories[-1] + "/a.py",
                "file",
                "modified",
                "generate",
                md5="new",
                index_slots=(IndexSlot(2, "a-l2", action="upsert"),),
            )
        ]
    )

    SemanticPlan(
        "viking://resources/repo",
        "resource",
        SemanticTreeSnapshot(entries),
    )

    assert entries.iterations <= 4


def test_semantic_plan_rejects_missing_higher_ancestor_independent_of_entry_order():
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )

    with pytest.raises(ValueError, match="lacks ancestor 'a'.*a/b/c.py"):
        SemanticPlan(
            "viking://resources/repo",
            "resource",
            SemanticTreeSnapshot(
                (
                    SemanticTreeEntry("", "directory", "unchanged", "aggregate"),
                    SemanticTreeEntry(
                        "a/b/c.py",
                        "file",
                        "modified",
                        "generate",
                        md5="new",
                        index_slots=(IndexSlot(2, "c-l2", action="upsert"),),
                    ),
                    SemanticTreeEntry("a/b", "directory", "unchanged", "aggregate"),
                )
            ),
        )


def test_index_slot_rejects_non_semantic_operations():
    from openviking.storage.context_update_plan import IndexSlot

    with pytest.raises(ValueError, match="only supports"):
        IndexSlot(2, "a-l2", action="update_fields")


def test_reuse_entry_rejects_index_mutation():
    from openviking.storage.context_update_plan import IndexSlot, SemanticTreeEntry

    with pytest.raises(ValueError, match="reuse.*index"):
        SemanticTreeEntry(
            "a.py",
            "file",
            "unchanged",
            "reuse",
            index_slots=(
                IndexSlot(
                    2,
                    "a-l2",
                    {"abstract": "old"},
                    action="upsert",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_resolver_compares_missing_fingerprints_with_bounded_reads():
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
    )

    paths = [f"{i}.py" for i in range(8)]
    snapshot = RNFVSnapshot(
        RequestIntent("viking://resources/repo", "semantic_and_vectors"),
        NewArtifactSnapshot({p: NewEntry() for p in paths}),
        FormalTreeSnapshot({p: FormalEntry() for p in paths}),
        VectorIndexSnapshot({}, frozenset({"id", "uri", "level", "md5"})),
    )
    active = peak = 0
    gate = asyncio.Event()

    async def read(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 4:
            gate.set()
        try:
            await asyncio.wait_for(gate.wait(), 1)
            return b"same"
        finally:
            active -= 1

    resolve = getattr(resource_diff, "resolve_resource_diff", None)
    assert resolve is not None
    result = await resolve(
        snapshot,
        store=SimpleNamespace(read_bytes=read),
        artifact_ref=None,
        target=SimpleNamespace(read_file=read),
        concurrency=2,
    )
    assert peak == 4
    assert active == 0
    assert all(
        e.content_state == "unchanged" and e.index_state == "missing"
        for e in result.entries.values()
    )
    assert all(e.md5 for e in result.entries.values())
    assert not hasattr(result, "needs_body_compare")


@pytest.mark.asyncio
async def test_resolver_rejects_incomplete_snapshot_before_io():
    from openviking.storage.resource_rnfv import (
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
    )

    snapshot = RNFVSnapshot(
        RequestIntent("viking://resources/repo", "semantic_and_vectors"),
        NewArtifactSnapshot({}, complete=False),
        FormalTreeSnapshot({}),
        VectorIndexSnapshot({}, frozenset({"id", "uri", "level", "md5"})),
    )
    store, target = AsyncMock(), AsyncMock()
    resolve = getattr(resource_diff, "resolve_resource_diff", None)
    assert resolve is not None
    with pytest.raises(ValueError, match="incomplete"):
        await resolve(snapshot, store=store, artifact_ref=None, target=target)
    store.read_bytes.assert_not_called()
    target.read_file.assert_not_called()


@pytest.mark.asyncio
async def test_resolver_hashes_new_file_when_manifest_md5_is_missing():
    from openviking.storage.context_update_plan import ContentState
    from openviking.storage.resource_rnfv import (
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
    )
    from openviking.utils.content_hash import content_md5

    snapshot = RNFVSnapshot(
        RequestIntent("viking://resources/repo", "semantic_and_vectors"),
        NewArtifactSnapshot({"a.py": NewEntry()}),
        FormalTreeSnapshot({}),
        VectorIndexSnapshot({}, frozenset({"id", "uri", "level", "md5"})),
    )
    store = AsyncMock()
    store.read_bytes.return_value = b"new body"
    result = await resource_diff.resolve_resource_diff(
        snapshot,
        store=store,
        artifact_ref=object(),
        target=AsyncMock(),
        artifact_paths={"a.py": "repository/a.py"},
    )

    assert result.entries["a.py"].content_state is ContentState.ADDED
    assert result.entries["a.py"].md5 == content_md5(b"new body")
    store.read_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolver_marks_removed_content_vectors_as_obsolete():
    from openviking.storage.context_update_plan import ContentState, IndexState
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    records = {
        "removed-l2": VectorRecordSnapshot("removed-l2", f"{root}/removed.md", "removed.md", 2),
        "orphan-l2": VectorRecordSnapshot("orphan-l2", f"{root}/orphan.md", "orphan.md", 2),
    }
    snapshot = RNFVSnapshot(
        RequestIntent(root, "semantic_and_vectors"),
        NewArtifactSnapshot({}),
        FormalTreeSnapshot({"removed.md": FormalEntry()}),
        VectorIndexSnapshot(records, frozenset({"id", "uri", "level", "md5"})),
    )

    result = await resource_diff.resolve_resource_diff(
        snapshot, store=AsyncMock(), artifact_ref=object(), target=AsyncMock()
    )

    assert result.entries["removed.md"].content_state is ContentState.DELETED
    assert result.entries["removed.md"].index_state is IndexState.OBSOLETE
    assert result.entries["orphan.md"].content_state is ContentState.ABSENT
    assert result.entries["orphan.md"].index_state is IndexState.ORPHAN


@pytest.mark.asyncio
async def test_resolver_uses_same_duplicate_record_winner_as_planner():
    from openviking.storage.context_update_plan import ContentState
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    records = {
        "z-new": VectorRecordSnapshot("z-new", f"{root}/a.py", "a.py", 2, {"md5": "new"}),
        "a-old": VectorRecordSnapshot("a-old", f"{root}/a.py", "a.py", 2, {"md5": "old"}),
    }
    snapshot = RNFVSnapshot(
        RequestIntent(root, "semantic_and_vectors"),
        NewArtifactSnapshot({"a.py": NewEntry(md5="new")}),
        FormalTreeSnapshot({"a.py": FormalEntry()}),
        VectorIndexSnapshot(records, frozenset({"id", "uri", "level", "md5"})),
    )

    result = await resource_diff.resolve_resource_diff(
        snapshot, store=AsyncMock(), artifact_ref=object(), target=AsyncMock()
    )

    assert result.entries["a.py"].content_state is ContentState.MODIFIED


@pytest.mark.asyncio
async def test_resolver_index_state_uses_canonical_duplicate_record():
    from openviking.storage.context_update_plan import ContentState, IndexState
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    records = {
        "z-new": VectorRecordSnapshot("z-new", f"{root}/a.py", "a.py", 2, {"md5": "new"}),
        "a-old": VectorRecordSnapshot("a-old", f"{root}/a.py", "a.py", 2, {"md5": "old"}),
    }
    snapshot = RNFVSnapshot(
        RequestIntent(root, "semantic_and_vectors"),
        NewArtifactSnapshot({"a.py": NewEntry(md5="old")}),
        FormalTreeSnapshot({"a.py": FormalEntry()}),
        VectorIndexSnapshot(records, frozenset({"id", "uri", "level", "md5"})),
    )

    result = await resource_diff.resolve_resource_diff(
        snapshot, store=AsyncMock(), artifact_ref=object(), target=AsyncMock()
    )

    entry = result.entries["a.py"]
    assert entry.content_state is ContentState.UNCHANGED
    assert entry.index_state is IndexState.COMPLETE


@pytest.mark.asyncio
async def test_resolver_log_separates_files_directories_and_logical_root(monkeypatch):
    from openviking.storage import resource_diff
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    records = {
        "root-l0": VectorRecordSnapshot("root-l0", root, "", 0),
        "root-l1": VectorRecordSnapshot("root-l1", root, "", 1),
        "file-l2": VectorRecordSnapshot("file-l2", f"{root}/a.py", "a.py", 2, {"md5": "same"}),
        "sub-l0": VectorRecordSnapshot("sub-l0", f"{root}/sub", "sub", 0),
        "sub-l1": VectorRecordSnapshot("sub-l1", f"{root}/sub", "sub", 1),
    }
    snapshot = RNFVSnapshot(
        RequestIntent(root, "semantic_and_vectors"),
        NewArtifactSnapshot(
            {"": NewEntry(is_dir=True), "a.py": NewEntry(md5="same"), "sub": NewEntry(is_dir=True)}
        ),
        FormalTreeSnapshot(
            {"": FormalEntry(is_dir=True), "a.py": FormalEntry(), "sub": FormalEntry(is_dir=True)}
        ),
        VectorIndexSnapshot(records, frozenset({"id", "uri", "level", "md5"})),
    )

    log_info = Mock()
    monkeypatch.setattr(resource_diff.logger, "info", log_info)
    await resource_diff.resolve_resource_diff(
        snapshot,
        store=AsyncMock(),
        artifact_ref=object(),
        target=AsyncMock(),
    )

    message = log_info.call_args.args[0] % log_info.call_args.args[1:]
    fixed_fields = "n_files=1 n_dirs=1 f_files=1 f_dirs=1 logical_root=true v_records=5 root_state=unchanged root_index=complete md5_fast_path=1 body_compared=0 new_files_hashed=0"
    assert fixed_fields in message
    assert "states={file:{'unchanged': 1},dir:{'unchanged': 1}}" in message
    assert "index={file:{'complete': 1},dir:{'complete': 1}}" in message
    assert message.index("md5_fast_path=1") < message.index("states=")
    assert "n_entries=" not in message


def test_tree_entry_log_counts_empty_path_as_a_real_flat_file():
    from openviking.storage.resource_diff import count_tree_entry_kinds
    from openviking.storage.resource_rnfv import FormalEntry, NewEntry

    assert count_tree_entry_kinds({"": NewEntry(md5="digest")}) == (1, 0, False)
    assert count_tree_entry_kinds({"": FormalEntry()}) == (1, 0, False)


def test_planning_details_log_at_debug(monkeypatch):
    from openviking.storage.context_update_plan import ContextUpdatePlan
    from openviking.storage.resource_diff import ResourceDiffResult
    from openviking.utils import resource_processor

    log_info = Mock()
    log_debug = Mock()
    monkeypatch.setattr(resource_processor.logger, "info", log_info)
    monkeypatch.setattr(resource_processor.logger, "debug", log_debug)
    plan = ContextUpdatePlan("viking://resources/repo", "resource")

    resource_processor.ResourceProcessor._log_context_update_plan(plan)
    resource_processor.ResourceProcessor._log_context_commit_summary(
        ResourceDiffResult({}),
        content_actions=(),
        root_uri=plan.root_uri,
        is_initial=False,
    )
    resource_processor.ResourceProcessor._log_context_commit_summary(
        ResourceDiffResult({}),
        content_actions=(),
        root_uri=plan.root_uri,
        is_initial=True,
    )

    assert log_info.call_count == 0
    assert log_debug.call_count == 3


def test_content_commit_log_uses_info_only_for_mutations(monkeypatch):
    from openviking.storage.context_update_plan import (
        ContentTreeAction,
        ContentTreeOperation,
    )
    from openviking.utils import resource_processor

    log_info = Mock()
    log_debug = Mock()
    monkeypatch.setattr(resource_processor.logger, "info", log_info)
    monkeypatch.setattr(resource_processor.logger, "debug", log_debug)

    resource_processor.ResourceProcessor._log_content_tree_commit(
        root_uri="viking://resources/repo",
        artifact_backend="local",
        actions=(),
        duration_ms=1.0,
    )
    resource_processor.ResourceProcessor._log_content_tree_commit(
        root_uri="viking://resources/repo",
        artifact_backend="local",
        actions=(
            ContentTreeAction(
                ContentTreeOperation.UPSERT,
                "a.py",
                new_kind="file",
                artifact_path="a.py",
                md5="digest",
            ),
        ),
        duration_ms=2.0,
    )

    log_debug.assert_called_once()
    log_info.assert_called_once()


def test_add_resource_log_level_contract():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    expected = {
        "openviking/storage/queuefs/add_resource_processor.py": {
            "[AddResourceStarted]": "info",
            "[AddResourceCompleted]": "info",
        },
        "openviking/storage/resource_diff.py": {
            "[ResourceDiffResult]": "info",
        },
        "openviking/utils/resource_processor.py": {
            "[RNFVSnapshot]": "debug",
            "[ContextUpdatePlan]": "debug",
            "[add_resource]": "debug",
            "[DirectIndexActions]": "debug",
        },
        "openviking/storage/queuefs/semantic_processor.py": {
            "[SemanticPlanExecution]": "debug",
        },
        "openviking/parse/parsers/code/code.py": {
            "Uploading code repository artifacts": "debug",
        },
        "openviking/parse/parsers/media/image.py": {
            "Processing large image": "debug",
        },
    }

    for relative_path, markers in expected.items():
        tree = ast.parse(root.joinpath(relative_path).read_text())
        found = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not node.args:
                continue
            message = "".join(
                part.value
                for part in ast.walk(node.args[0])
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            for marker in markers:
                if marker in message:
                    found[marker] = node.func.attr
        assert found == markers


def test_builder_maps_content_semantic_and_direct_index_actions():
    from openviking.storage.context_update_plan import (
        ContentState,
        ContextUpdatePlan,
        IndexAction,
        IndexState,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    diff = ResourceDiffResult(
        entries={
            "changed.py": ResourceDiffEntry(
                "changed.py",
                ContentState.MODIFIED,
                IndexState.STALE,
                old_kind="file",
                new_kind="file",
                md5="new-md5",
            ),
            "gone.py": ResourceDiffEntry(
                "gone.py", ContentState.DELETED, IndexState.OBSOLETE, old_kind="file"
            ),
            "ghost.py": ResourceDiffEntry("ghost.py", ContentState.ABSENT, IndexState.ORPHAN),
        }
    )
    inventory = {
        "changed-id": VectorRecordSnapshot(
            "changed-id",
            "viking://resources/repo/changed.py",
            "changed.py",
            2,
            {"md5": "old-md5", "abstract": "old abstract", "search_tags": ["old"]},
        ),
        "gone-id": VectorRecordSnapshot("gone-id", "viking://resources/repo/gone.py", "gone.py", 2),
        "ghost-id": VectorRecordSnapshot(
            "ghost-id", "viking://resources/repo/ghost.py", "ghost.py", 2
        ),
    }

    from openviking.storage.context_update_plan import build_context_update_plan

    plan = build_context_update_plan(
        root_uri="viking://resources/repo",
        context_type="resource",
        request=RequestIntent("viking://resources/repo", "semantic_and_vectors"),
        diff=diff,
        new_kinds={"": "directory", "changed.py": "file"},
        artifact_paths={"changed.py": "repository/changed.py"},
        records=inventory,
        is_code_repo=True,
        account_id="acc",
    )

    assert isinstance(plan, ContextUpdatePlan)
    assert [(a.operation.value, a.relative_path) for a in plan.content_tree_actions] == [
        ("upsert", "changed.py"),
        ("delete", "gone.py"),
    ]
    assert {(a.action.value, a.record_id) for a in plan.direct_index_actions} == {
        ("delete", "gone-id"),
        ("delete", "ghost-id"),
    }
    changed = next(e for e in plan.semantic_plan.tree.entries if e.relative_path == "changed.py")
    assert changed.semantic_action.value == "generate"
    assert changed.index_slots[0].action == IndexAction.UPSERT
    assert changed.index_slots[0].record_id == "changed-id"


def test_level_conflict_deletes_invalid_record_and_rebuilds_valid_slots():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "semantic_and_vectors"),
        diff=ResourceDiffResult(
            {
                "module": ResourceDiffEntry(
                    "module",
                    ContentState.UNCHANGED,
                    IndexState.LEVEL_CONFLICT,
                    old_kind="directory",
                    new_kind="directory",
                )
            }
        ),
        new_kinds={"": "directory", "module": "directory"},
        artifact_paths={},
        records={"stale-l2": VectorRecordSnapshot("stale-l2", f"{root}/module", "module", 2)},
        is_code_repo=False,
        account_id="acc",
    )

    assert [(a.action.value, a.record_id) for a in plan.direct_index_actions] == [
        ("delete", "stale-l2")
    ]
    entry = next(
        entry for entry in plan.semantic_plan.tree.entries if entry.relative_path == "module"
    )
    assert entry.semantic_action.value == "aggregate"
    assert [(slot.level, slot.action.value) for slot in entry.index_slots] == [
        (0, "upsert"),
        (1, "upsert"),
    ]


def test_builder_deletes_duplicate_same_level_records_without_id_conflict():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    records = {
        record_id: VectorRecordSnapshot(
            record_id,
            root + "/a.py",
            "a.py",
            2,
            {"md5": "old", "abstract": "old"},
        )
        for record_id in ("a-primary", "a-duplicate")
    }
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "semantic_and_vectors"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.MODIFIED,
                    IndexState.STALE,
                    old_kind="file",
                    new_kind="file",
                    md5="new",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records=records,
        is_code_repo=True,
        account_id="acc",
    )

    slot = next(
        entry for entry in plan.semantic_plan.tree.entries if entry.relative_path == "a.py"
    ).slot(2)
    assert slot.record_id in records
    assert [(action.action.value, action.record_id) for action in plan.direct_index_actions] == [
        ("delete", ({"a-primary", "a-duplicate"} - {slot.record_id}).pop())
    ]


def test_healthy_noop_produces_no_actions_or_semantic_plan():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    records = {
        "root-l0": VectorRecordSnapshot("root-l0", root, "", 0, {"abstract": "root abstract"}),
        "root-l1": VectorRecordSnapshot("root-l1", root, "", 1, {"abstract": "root overview"}),
        "a-l2": VectorRecordSnapshot(
            "a-l2",
            root + "/a.py",
            "a.py",
            2,
            {"md5": "same", "abstract": "a summary"},
        ),
    }
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "semantic_and_vectors"),
        diff=ResourceDiffResult(
            {
                "": ResourceDiffEntry(
                    "",
                    ContentState.UNCHANGED,
                    IndexState.COMPLETE,
                    old_kind="directory",
                    new_kind="directory",
                ),
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.UNCHANGED,
                    IndexState.COMPLETE,
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                ),
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records=records,
        is_code_repo=True,
        account_id="acc",
    )

    assert plan.is_noop()
    assert plan.content_tree_actions == ()
    assert plan.semantic_plan is None
    assert plan.direct_index_actions == ()


@pytest.mark.asyncio
async def test_healthy_rnfv_clear_request_produces_scalar_only_update():
    from openviking.storage.context_update_plan import (
        FieldPatch,
        build_context_update_plan_from_snapshot,
    )
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )
    from openviking.utils.ingest_options import IngestOptions

    uri = "viking://resources/repo/a.txt"
    ingest_options = IngestOptions.from_search_tags(None, mode="clear")
    request = RequestIntent.from_ingest_options(
        target_uri=uri,
        processing_mode="vectors_only",
        ingest_options=ingest_options,
    )
    snapshot = RNFVSnapshot(
        request,
        NewArtifactSnapshot({"": NewEntry(md5="same")}),
        FormalTreeSnapshot({"": FormalEntry()}),
        VectorIndexSnapshot(
            {
                "a-l2": VectorRecordSnapshot(
                    "a-l2",
                    uri,
                    "",
                    2,
                    {"md5": "same", "search_tags": ["scope=old"]},
                )
            },
            request.required_vector_fields(),
        ),
    )

    diff, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot,
        store=AsyncMock(),
        artifact_ref=object(),
        target=AsyncMock(),
        vikingdb=AsyncMock(),
        context_type="resource",
        is_code_repo=False,
        account_id="acc",
        ctx=object(),
        root_preexisting=True,
        artifact_paths={"": "repository/a.txt"},
        ingest_options=ingest_options,
        root_is_file=True,
    )

    assert diff.entries[""].content_state.value == "unchanged"
    assert request.scalar_intents[0].value == ()
    assert request.required_vector_fields() >= {"search_tags"}
    assert plan.content_tree_actions == ()
    assert plan.semantic_plan is None
    assert len(plan.direct_index_actions) == 1
    action = plan.direct_index_actions[0]
    assert action.action.value == "update_fields"
    assert action.record_id == "a-l2"
    assert action.field_patch == FieldPatch(
        {"search_tags": ()},
        {"search_tags": "replace"},
        {
            "uri": uri,
            "account_id": "acc",
            "level": 2,
            "md5": "same",
            "search_tags": [],
        },
    )


@pytest.mark.asyncio
async def test_healthy_rnfv_empty_replace_request_is_noop():
    from openviking.storage.context_update_plan import build_context_update_plan_from_snapshot
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )
    from openviking.utils.ingest_options import IngestOptions

    uri = "viking://resources/repo/a.txt"
    ingest_options = IngestOptions.from_search_tags([], mode="replace")
    request = RequestIntent.from_ingest_options(
        target_uri=uri,
        processing_mode="vectors_only",
        ingest_options=ingest_options,
    )
    snapshot = RNFVSnapshot(
        request,
        NewArtifactSnapshot({"": NewEntry(md5="same")}),
        FormalTreeSnapshot({"": FormalEntry()}),
        VectorIndexSnapshot(
            {
                "a-l2": VectorRecordSnapshot(
                    "a-l2",
                    uri,
                    "",
                    2,
                    {"md5": "same", "search_tags": ["scope=old"]},
                )
            },
            request.required_vector_fields(),
        ),
    )

    diff, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot,
        store=AsyncMock(),
        artifact_ref=object(),
        target=AsyncMock(),
        vikingdb=AsyncMock(),
        context_type="resource",
        is_code_repo=False,
        account_id="acc",
        ctx=object(),
        root_preexisting=True,
        artifact_paths={"": "repository/a.txt"},
        ingest_options=ingest_options,
        root_is_file=True,
    )

    assert diff.entries[""].content_state.value == "unchanged"
    assert request.scalar_intents == ()
    assert "search_tags" not in request.required_vector_fields()
    assert plan.is_noop()


def test_healthy_directory_rnfv_clear_updates_all_vector_levels():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import (
        RequestIntent,
        VectorRecordSnapshot,
    )
    from openviking.utils.ingest_options import IngestOptions

    root = "viking://resources/repo"
    request = RequestIntent.from_ingest_options(
        target_uri=root,
        processing_mode="semantic_and_vectors",
        ingest_options=IngestOptions.from_search_tags(None, mode="clear"),
    )
    records = {
        "root-l0": VectorRecordSnapshot(
            "root-l0", root, "", 0, {"search_tags": ["scope=old"]}
        ),
        "root-l1": VectorRecordSnapshot(
            "root-l1", root, "", 1, {"search_tags": ["scope=old"]}
        ),
        "a-l2": VectorRecordSnapshot(
            "a-l2",
            root + "/a.py",
            "a.py",
            2,
            {"md5": "same", "search_tags": ["scope=old"]},
        ),
    }

    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=request,
        diff=ResourceDiffResult(
            {
                "": ResourceDiffEntry(
                    "",
                    ContentState.UNCHANGED,
                    IndexState.COMPLETE,
                    old_kind="directory",
                    new_kind="directory",
                ),
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.UNCHANGED,
                    IndexState.COMPLETE,
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                ),
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records=records,
        is_code_repo=False,
        account_id="acc",
    )

    assert plan.content_tree_actions == ()
    assert plan.semantic_plan is None
    assert {
        (action.record_id, action.level, action.action.value)
        for action in plan.direct_index_actions
    } == {
        ("root-l0", 0, "update_fields"),
        ("root-l1", 1, "update_fields"),
        ("a-l2", 2, "update_fields"),
    }
    assert all(
        action.field_patch.values == {"search_tags": []}
        and action.field_patch.modes == {"search_tags": "replace"}
        for action in plan.direct_index_actions
    )


@pytest.mark.parametrize("existing_tags", [None, []])
def test_healthy_rnfv_clear_is_noop_when_vector_has_no_tags(existing_tags):
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot
    from openviking.utils.ingest_options import IngestOptions

    root = "viking://resources/repo"
    fields = {"md5": "same"}
    if existing_tags is not None:
        fields["search_tags"] = existing_tags
    request = RequestIntent.from_ingest_options(
        target_uri=root,
        processing_mode="vectors_only",
        ingest_options=IngestOptions.from_search_tags(None, mode="clear"),
    )
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=request,
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.UNCHANGED,
                    IndexState.COMPLETE,
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={
            "a-l2": VectorRecordSnapshot(
                "a-l2", root + "/a.py", "a.py", 2, fields
            )
        },
        is_code_repo=False,
        account_id="acc",
    )

    assert plan.is_noop()


@pytest.mark.parametrize(
    ("existing_tags", "expected_update"),
    [
        (["scope=old"], True),
        (["scope=new"], False),
        (None, True),
        ([], True),
    ],
)
def test_healthy_rnfv_non_empty_replace_compares_against_vector_tags(
    existing_tags, expected_update
):
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot
    from openviking.utils.ingest_options import IngestOptions

    root = "viking://resources/repo"
    fields = {"md5": "same"}
    if existing_tags is not None:
        fields["search_tags"] = existing_tags
    request = RequestIntent.from_ingest_options(
        target_uri=root,
        processing_mode="vectors_only",
        ingest_options=IngestOptions.from_search_tags(["scope=new"], mode="replace"),
    )
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=request,
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.UNCHANGED,
                    IndexState.COMPLETE,
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={
            "a-l2": VectorRecordSnapshot(
                "a-l2", root + "/a.py", "a.py", 2, fields
            )
        },
        is_code_repo=False,
        account_id="acc",
    )

    assert bool(plan.direct_index_actions) is expected_update


@pytest.mark.asyncio
async def test_vectorize_disabled_plan_ignores_stale_vectors_and_compares_formal_content():
    from openviking.storage.context_update_plan import (
        ContentState,
        build_context_update_plan_from_snapshot,
    )
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    store = AsyncMock()
    store.read_bytes.return_value = b"v1"
    target = AsyncMock()
    target.read_file.return_value = b"v2"
    vikingdb = AsyncMock()
    snapshot = RNFVSnapshot(
        RequestIntent(root, "vectors_only", vectorize=False),
        NewArtifactSnapshot({"a.txt": NewEntry(md5="hash-v1")}),
        FormalTreeSnapshot({"a.txt": FormalEntry()}),
        VectorIndexSnapshot(
            {
                "stale-l2": VectorRecordSnapshot(
                    "stale-l2",
                    "viking://resources/other/a.txt",
                    "a.txt",
                    2,
                    {"md5": "hash-v1", "abstract": "stale summary"},
                )
            },
            frozenset({"id", "uri", "level", "md5", "abstract"}),
            complete=False,
        ),
    )

    diff, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot,
        store=store,
        artifact_ref=object(),
        target=target,
        vikingdb=vikingdb,
        context_type="resource",
        is_code_repo=False,
        account_id="acc",
        ctx=object(),
        root_preexisting=True,
        artifact_paths={"a.txt": "repository/a.txt"},
    )

    assert diff.entries["a.txt"].content_state is ContentState.MODIFIED
    assert [
        (action.operation.value, action.relative_path) for action in plan.content_tree_actions
    ] == [("upsert", "a.txt")]
    assert plan.semantic_plan is None
    assert plan.direct_index_actions == ()
    store.read_bytes.assert_awaited_once()
    target.read_file.assert_awaited_once_with("a.txt")
    vikingdb.hydrate_incremental_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_vectorize_enabled_keeps_md5_noop_without_formal_content_reads():
    from openviking.storage.context_update_plan import ContentState
    from openviking.storage.resource_diff import resolve_resource_diff
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    snapshot = RNFVSnapshot(
        RequestIntent(root, "semantic_and_vectors", vectorize=True),
        NewArtifactSnapshot({"a.txt": NewEntry(md5="same")}),
        FormalTreeSnapshot({"a.txt": FormalEntry()}),
        VectorIndexSnapshot(
            {"a-l2": VectorRecordSnapshot("a-l2", root + "/a.txt", "a.txt", 2, {"md5": "same"})},
            frozenset({"id", "uri", "level", "md5"}),
        ),
    )
    store = AsyncMock()
    target = AsyncMock()

    diff = await resolve_resource_diff(
        snapshot,
        store=store,
        artifact_ref=object(),
        target=target,
        artifact_paths={"a.txt": "repository/a.txt"},
    )

    assert diff.entries["a.txt"].content_state is ContentState.UNCHANGED
    store.read_bytes.assert_not_awaited()
    target.read_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_vectorize_disabled_semantic_plan_regenerates_without_vector_hydration():
    from openviking.storage.context_update_plan import build_context_update_plan_from_snapshot
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        ScalarIntent,
        VectorIndexSnapshot,
    )

    root = "viking://resources/repo"
    store = AsyncMock()
    store.read_bytes.side_effect = lambda _ref, path: {
        "repository/a.txt": b"new a",
        "repository/b.txt": b"same b",
    }[path]
    target = AsyncMock()
    target.read_file.side_effect = lambda path: {
        "a.txt": b"old a",
        "b.txt": b"same b",
    }[path]
    vikingdb = AsyncMock()
    snapshot = RNFVSnapshot(
        RequestIntent(
            root,
            "semantic_and_vectors",
            vectorize=False,
            scalar_intents=(ScalarIntent("search_tags", "replace", ("team=search",)),),
        ),
        NewArtifactSnapshot(
            {
                "a.txt": NewEntry(md5="new-a"),
                "b.txt": NewEntry(md5="same-b"),
            }
        ),
        FormalTreeSnapshot({"a.txt": FormalEntry(), "b.txt": FormalEntry()}),
        VectorIndexSnapshot({}, frozenset()),
    )

    _, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot,
        store=store,
        artifact_ref=object(),
        target=target,
        vikingdb=vikingdb,
        context_type="resource",
        is_code_repo=False,
        account_id="acc",
        ctx=object(),
        root_preexisting=True,
        artifact_paths={
            "a.txt": "repository/a.txt",
            "b.txt": "repository/b.txt",
        },
    )

    assert plan.semantic_plan is not None
    assert {
        entry.relative_path: entry.semantic_action.value
        for entry in plan.semantic_plan.tree.entries
    } == {"": "aggregate", "a.txt": "generate", "b.txt": "generate"}
    assert all(
        slot.action.value == "none"
        for entry in plan.semantic_plan.tree.entries
        for slot in entry.index_slots
    )
    assert all(
        not slot.upsert_fields and slot.field_patch is None
        for entry in plan.semantic_plan.tree.entries
        for slot in entry.index_slots
    )
    assert all(not entry.repair for entry in plan.semantic_plan.tree.entries)
    assert plan.semantic_plan.ingest_options.search_tags is None
    assert plan.direct_index_actions == ()
    vikingdb.hydrate_incremental_records.assert_not_awaited()


def test_restore_with_matching_index_reuses_file_summary_and_aggregates_parent():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "semantic_and_vectors"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.RESTORE,
                    IndexState.COMPLETE,
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={
            "a-l2": VectorRecordSnapshot(
                "a-l2",
                root + "/a.py",
                "a.py",
                2,
                {"md5": "same", "abstract": "old summary"},
            ),
            "root-l0": VectorRecordSnapshot("root-l0", root, "", 0, {"abstract": "old root"}),
            "root-l1": VectorRecordSnapshot("root-l1", root, "", 1, {"abstract": "old overview"}),
        },
        is_code_repo=True,
        account_id="acc",
    )

    entries = {entry.relative_path: entry for entry in plan.semantic_plan.tree.entries}
    assert entries["a.py"].semantic_action.value == "reuse"
    assert entries[""].semantic_action.value == "aggregate"
    assert entries[""].membership_changed is True


def test_builder_keeps_append_intent_as_fallback_for_semantic_upsert():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, ScalarIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    record = VectorRecordSnapshot(
        "id-a",
        root + "/a.py",
        "a.py",
        2,
        {"md5": "old", "abstract": "old abstract", "search_tags": ["scope=old"]},
    )
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(
            root,
            "semantic_and_vectors",
            scalar_intents=(ScalarIntent("search_tags", "append", ("owner=new",)),),
        ),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.MODIFIED,
                    IndexState.STALE,
                    old_kind="file",
                    new_kind="file",
                    md5="new",
                ),
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={"id-a": record},
        is_code_repo=True,
        account_id="acc",
    )
    slot = next(
        e for e in plan.semantic_plan.tree.entries if e.relative_path == "a.py"
    ).index_slots[0]
    assert slot.upsert_fields == {"search_tags": ["scope=old", "owner=new"]}
    assert slot.field_patch == FieldPatch(
        {"search_tags": ["owner=new"]},
        {"search_tags": "append"},
    )
    assert slot.fallback_to_patch is True
    assert plan.semantic_plan.ingest_options.search_tags is None


def test_builder_skips_scalar_update_when_vectorization_is_disabled():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import (
        RequestIntent,
        ScalarIntent,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    record = VectorRecordSnapshot(
        "a-l2",
        root + "/a.py",
        "a.py",
        2,
        {"abstract": "old", "search_tags": ["scope=old"]},
    )
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(
            root,
            "semantic_and_vectors",
            vectorize=False,
            scalar_intents=(ScalarIntent("search_tags", "replace", ("scope=new",)),),
        ),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.MODIFIED,
                    IndexState.STALE,
                    old_kind="file",
                    new_kind="file",
                    md5="new",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={record.record_id: record},
        is_code_repo=True,
        account_id="acc",
    )

    assert plan.semantic_plan is not None
    assert plan.direct_index_actions == ()


def test_vectors_only_upsert_preserves_existing_custom_scalars():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    record = VectorRecordSnapshot(
        "external-id",
        root + "/a.py",
        "a.py",
        2,
        {
            "abstract": "old",
            "md5": "old",
            "business_priority": 7,
            "vector": [1.0],
        },
    )
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "vectors_only"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.MODIFIED,
                    IndexState.STALE,
                    old_kind="file",
                    new_kind="file",
                    md5="new",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={record.record_id: record},
        is_code_repo=False,
        account_id="acc",
    )

    action = plan.direct_index_actions[0]
    assert action.action.value == "upsert"
    assert action.record_id == "external-id"
    assert action.upsert_fields["business_priority"] == 7
    assert "vector" not in action.upsert_fields


def test_vectors_only_restore_with_complete_index_does_not_reembed():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    record = VectorRecordSnapshot(
        "existing-id",
        root + "/a.py",
        "a.py",
        2,
        {"md5": "same", "abstract": "old", "search_tags": ["scope=old"]},
    )
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "vectors_only"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.RESTORE,
                    IndexState.COMPLETE,
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={record.record_id: record},
        is_code_repo=False,
        account_id="acc",
    )

    assert [
        (action.operation.value, action.relative_path) for action in plan.content_tree_actions
    ] == [("upsert", "a.py")]
    assert plan.semantic_plan is None
    assert plan.direct_index_actions == ()


def test_vectors_only_unchanged_file_with_extra_level_only_deletes_conflict():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "vectors_only"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.UNCHANGED,
                    IndexState.LEVEL_CONFLICT,
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={
            "valid-l2": VectorRecordSnapshot(
                "valid-l2", root + "/a.py", "a.py", 2, {"md5": "same"}
            ),
            "invalid-l0": VectorRecordSnapshot("invalid-l0", root + "/a.py", "a.py", 0, {}),
        },
        is_code_repo=False,
        account_id="acc",
    )

    assert [(action.action.value, action.record_id) for action in plan.direct_index_actions] == [
        ("delete", "invalid-l0")
    ]


def test_builder_carries_request_ingest_options_into_semantic_plan():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent
    from openviking.utils.ingest_options import IngestOptions

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "semantic_and_vectors"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.ADDED,
                    IndexState.MISSING,
                    new_kind="file",
                    md5="new",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={},
        is_code_repo=False,
        account_id="acc",
        ingest_options=IngestOptions.from_search_tags(["team=search"], mode="append"),
    )

    assert plan.semantic_plan.ingest_options.search_tags == ["team=search"]
    assert plan.semantic_plan.ingest_options.search_tag_mode == "append"


def test_missing_index_for_existing_content_uses_partial_update_repair():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "semantic_and_vectors"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.UNCHANGED,
                    IndexState.MISSING,
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={},
        is_code_repo=False,
        account_id="acc",
    )

    file_entry = next(
        entry for entry in plan.semantic_plan.tree.entries if entry.relative_path == "a.py"
    )
    assert file_entry.slot(2).action.value == "merge"


@pytest.mark.parametrize("content_state", ["added", "restore"])
def test_new_or_restored_content_does_not_use_partial_update(content_state):
    from openviking.storage.context_update_plan import build_context_update_plan
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "semantic_and_vectors"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py", content_state, "missing", new_kind="file", md5="new"
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={},
        is_code_repo=False,
        account_id="acc",
    )

    file_entry = next(
        entry for entry in plan.semantic_plan.tree.entries if entry.relative_path == "a.py"
    )
    assert file_entry.slot(2).action.value == "upsert"


def test_vectors_only_missing_index_for_existing_content_uses_partial_update_repair():
    from openviking.storage.context_update_plan import build_context_update_plan
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "vectors_only"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    "unchanged",
                    "missing",
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={},
        is_code_repo=False,
        account_id="acc",
    )

    assert len(plan.direct_index_actions) == 1
    assert plan.direct_index_actions[0].action.value == "merge"


def test_vectors_only_partial_repair_ignores_explicit_replace_empty_tags():
    from openviking.storage.context_update_plan import build_context_update_plan
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent
    from openviking.utils.ingest_options import IngestOptions

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent.from_ingest_options(
            target_uri=root,
            processing_mode="vectors_only",
            ingest_options=IngestOptions.from_search_tags([], mode="replace"),
        ),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    "unchanged",
                    "missing",
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={},
        is_code_repo=False,
        account_id="acc",
    )

    assert len(plan.direct_index_actions) == 1
    assert plan.direct_index_actions[0].field_patch is None


def test_vectors_only_partial_repair_preserves_clear_tags_intent():
    from openviking.storage.context_update_plan import build_context_update_plan
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent
    from openviking.utils.ingest_options import IngestOptions

    root = "viking://resources/repo"
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent.from_ingest_options(
            target_uri=root,
            processing_mode="vectors_only",
            ingest_options=IngestOptions.from_search_tags(None, mode="clear"),
        ),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    "unchanged",
                    "missing",
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                )
            }
        ),
        new_kinds={"a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={},
        is_code_repo=False,
        account_id="acc",
    )

    action = plan.direct_index_actions[0]
    assert action.action.value == "merge"
    assert action.field_patch == FieldPatch(
        {"search_tags": []},
        {"search_tags": "replace"},
    )


def test_builder_backfills_missing_md5_and_merges_scalar_update():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import (
        RequestIntent,
        ScalarIntent,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    record = VectorRecordSnapshot(
        "a-l2",
        root + "/a.py",
        "a.py",
        2,
        {
            "md5": "",
            "abstract": "old",
            "search_tags": ["scope=old"],
        },
    )
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(
            root,
            "semantic_and_vectors",
            scalar_intents=(ScalarIntent("search_tags", "replace", ("scope=new",)),),
        ),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.UNCHANGED,
                    IndexState.STALE,
                    old_kind="file",
                    new_kind="file",
                    md5="resolved-md5",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={"a-l2": record},
        is_code_repo=True,
        account_id="acc",
    )

    assert plan.semantic_plan is None
    assert len(plan.direct_index_actions) == 1
    action = plan.direct_index_actions[0]
    assert action.record_id == "a-l2"
    assert action.action.value == "update_fields"
    assert action.field_patch.values == {
        "md5": "resolved-md5",
        "search_tags": ["scope=new"],
    }


def test_builder_preserves_existing_record_identity_and_custom_scalars_on_upsert():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        build_context_update_plan,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    record = VectorRecordSnapshot(
        "external-record-id",
        root + "/a.py",
        "a.py",
        2,
        {
            "abstract": "old",
            "business_priority": 7,
            "vector": [1.0],
            "content": "large old body",
        },
    )
    plan = build_context_update_plan(
        root_uri=root,
        context_type="resource",
        request=RequestIntent(root, "semantic_and_vectors"),
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.MODIFIED,
                    IndexState.STALE,
                    old_kind="file",
                    new_kind="file",
                    md5="new",
                )
            }
        ),
        new_kinds={"": "directory", "a.py": "file"},
        artifact_paths={"a.py": "repository/a.py"},
        records={record.record_id: record},
        is_code_repo=True,
        account_id="acc",
    )

    slot = next(
        entry for entry in plan.semantic_plan.tree.entries if entry.relative_path == "a.py"
    ).slot(2)
    assert slot.record_id == "external-record-id"
    assert slot.scalar_override()["business_priority"] == 7
    assert "vector" not in slot.scalar_override()
    assert "content" not in slot.scalar_override()


@pytest.mark.asyncio
async def test_snapshot_builder_returns_one_canonical_context_plan():
    from openviking.storage.context_update_plan import (
        ContentState,
        build_context_update_plan_from_snapshot,
    )
    from openviking.storage.resource_rnfv import (
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
    )

    root = "viking://resources/repo"
    snapshot = RNFVSnapshot(
        request=RequestIntent(root, "semantic_and_vectors"),
        new=NewArtifactSnapshot({"a.py": NewEntry(md5="new")}),
        formal=FormalTreeSnapshot({}),
        vectors=VectorIndexSnapshot({}, frozenset({"id", "uri", "level", "md5"})),
    )
    diff, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot,
        store=AsyncMock(),
        artifact_ref=object(),
        target=AsyncMock(),
        vikingdb=AsyncMock(),
        context_type="resource",
        is_code_repo=True,
        account_id="acc",
        ctx=object(),
        root_preexisting=False,
        artifact_paths={"a.py": "repository/a.py"},
    )

    assert diff.entries["a.py"].content_state is ContentState.ADDED
    assert [a.relative_path for a in plan.content_tree_actions] == ["", "a.py"]
    assert plan.content_tree_actions[1].artifact_path == "repository/a.py"
    assert plan.semantic_plan is not None
    entries = {entry.relative_path: entry for entry in plan.semantic_plan.tree.entries}
    assert entries[""].semantic_action.value == "aggregate"
    assert entries["a.py"].semantic_action.value == "generate"


@pytest.mark.asyncio
async def test_snapshot_builder_hydrates_scalars_for_vectors_only_upsert():
    from openviking.storage.context_update_plan import build_context_update_plan_from_snapshot
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )

    root = "viking://resources/repo"
    record = VectorRecordSnapshot(
        "existing-id",
        root + "/a.py",
        "a.py",
        2,
        {"md5": "old", "abstract": "old summary"},
    )
    snapshot = RNFVSnapshot(
        request=RequestIntent(root, "vectors_only"),
        new=NewArtifactSnapshot({"a.py": NewEntry(md5="new")}),
        formal=FormalTreeSnapshot({"a.py": FormalEntry()}),
        vectors=VectorIndexSnapshot(
            {record.record_id: record},
            frozenset({"id", "uri", "level", "md5", "abstract"}),
        ),
    )
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.return_value = {
        record.record_id: {
            "id": record.record_id,
            "uri": record.uri,
            "level": 2,
            "md5": "old",
            "abstract": "old summary",
            "business_priority": 7,
        }
    }

    _, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot,
        store=AsyncMock(),
        artifact_ref=object(),
        target=AsyncMock(),
        vikingdb=vikingdb,
        context_type="resource",
        is_code_repo=False,
        account_id="acc",
        ctx=object(),
        root_preexisting=True,
        artifact_paths={"a.py": "repository/a.py"},
    )

    vikingdb.hydrate_incremental_records.assert_awaited_once()
    assert plan.direct_index_actions[0].record_id == record.record_id
    assert plan.direct_index_actions[0].upsert_fields["business_priority"] == 7


@pytest.mark.asyncio
async def test_file_root_plan_uses_file_refresh_without_directory_semantic_tree():
    from openviking.storage.context_update_plan import build_context_update_plan_from_snapshot
    from openviking.storage.resource_rnfv import (
        FormalEntry,
        FormalTreeSnapshot,
        NewArtifactSnapshot,
        NewEntry,
        RequestIntent,
        RNFVSnapshot,
        VectorIndexSnapshot,
        VectorRecordSnapshot,
    )

    root = "viking://resources/report.md"
    record = VectorRecordSnapshot("report-l2", root, "", 2, {"md5": "old", "abstract": "old"})
    snapshot = RNFVSnapshot(
        RequestIntent(root, "semantic_and_vectors"),
        NewArtifactSnapshot({"": NewEntry(md5="new")}),
        FormalTreeSnapshot({"": FormalEntry()}),
        VectorIndexSnapshot({"report-l2": record}, frozenset({"id", "uri", "level", "md5"})),
    )
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.return_value = {"report-l2": {"abstract": "old"}}

    _, plan = await build_context_update_plan_from_snapshot(
        snapshot=snapshot,
        store=AsyncMock(read_bytes=AsyncMock(return_value=b"new")),
        artifact_ref=object(),
        target=AsyncMock(read_file=AsyncMock(return_value=b"old")),
        vikingdb=vikingdb,
        context_type="resource",
        is_code_repo=False,
        account_id="acc",
        ctx=object(),
        root_preexisting=True,
        artifact_paths={"": "report.md"},
        root_is_file=True,
    )

    assert [
        (action.operation.value, action.relative_path) for action in plan.content_tree_actions
    ] == [("upsert", "")]
    assert plan.semantic_plan is None
    assert plan.file_refresh is not None
    assert plan.file_refresh.md5 == "new"


@pytest.mark.asyncio
async def test_hydration_promotes_a_dependency_when_its_record_disappears():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        hydrate_context_plan_records,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import VectorRecordSnapshot

    root = "viking://resources/repo"
    inventory = {
        "changed": VectorRecordSnapshot("changed", f"{root}/a.py", "a.py", 2, {"md5": "old"}),
        "sibling": VectorRecordSnapshot("sibling", f"{root}/b.py", "b.py", 2, {"md5": "same"}),
    }
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.return_value = {
        "changed": {
            "id": "changed",
            "uri": f"{root}/a.py",
            "level": 2,
            "abstract": "old a",
        }
    }
    records, (active, _, retained) = await hydrate_context_plan_records(
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.MODIFIED,
                    IndexState.STALE,
                    old_kind="file",
                    new_kind="file",
                    md5="new",
                ),
                "b.py": ResourceDiffEntry(
                    "b.py",
                    ContentState.UNCHANGED,
                    IndexState.COMPLETE,
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                ),
            }
        ),
        new_kinds={"": "directory", "a.py": "file", "b.py": "file"},
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=object(),
    )

    assert records["sibling"].record_id == "sibling"
    assert "b.py" in active
    assert "b.py" in retained


@pytest.mark.asyncio
async def test_hydration_reads_summaries_before_full_scalars_for_active_records():
    from openviking.storage.context_update_plan import (
        ContentState,
        IndexState,
        hydrate_context_plan_records,
    )
    from openviking.storage.resource_diff import ResourceDiffEntry, ResourceDiffResult
    from openviking.storage.resource_rnfv import RequestIntent, VectorRecordSnapshot

    root = "viking://resources/repo"
    inventory = {
        "changed": VectorRecordSnapshot("changed", f"{root}/a.py", "a.py", 2, {"md5": "old"}),
        "sibling": VectorRecordSnapshot("sibling", f"{root}/b.py", "b.py", 2, {"md5": "same"}),
    }
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.side_effect = [
        {
            "changed": {"abstract": "old a"},
            "sibling": {"abstract": "old b"},
        },
        {"changed": {"business_priority": 7}},
    ]
    records, (active, _, retained) = await hydrate_context_plan_records(
        diff=ResourceDiffResult(
            {
                "a.py": ResourceDiffEntry(
                    "a.py",
                    ContentState.MODIFIED,
                    IndexState.STALE,
                    old_kind="file",
                    new_kind="file",
                    md5="new",
                ),
                "b.py": ResourceDiffEntry(
                    "b.py",
                    ContentState.UNCHANGED,
                    IndexState.COMPLETE,
                    old_kind="file",
                    new_kind="file",
                    md5="same",
                ),
            }
        ),
        new_kinds={"": "directory", "a.py": "file", "b.py": "file"},
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=object(),
        request=RequestIntent(root, "semantic_and_vectors"),
    )

    assert active == {"", "a.py"}
    assert retained == {"", "a.py", "b.py"}
    assert records["sibling"].fields == {"md5": "same", "abstract": "old b"}
    assert records["changed"].fields == {
        "md5": "old",
        "abstract": "old a",
        "business_priority": 7,
    }
    assert vikingdb.hydrate_incremental_records.await_args_list[0].kwargs["output_fields"] == {
        "abstract"
    }
    assert "output_fields" not in vikingdb.hydrate_incremental_records.await_args_list[1].kwargs
    assert set(vikingdb.hydrate_incremental_records.await_args_list[1].args[0]) == {"changed"}


@pytest.mark.asyncio
async def test_content_executor_uploads_files_with_bounded_concurrency():
    from unittest.mock import patch

    import openviking.concurrency as concurrency_utils
    from openviking.storage.context_update_plan import (
        ContentTreeAction,
        execute_content_tree_actions,
    )

    active = peak = 0
    gate = asyncio.Event()

    class Store:
        async def read_bytes(self, ref, path):
            return path.encode()

    class Target:
        async def write_file(self, path, data):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 3:
                gate.set()
            try:
                await asyncio.wait_for(gate.wait(), 1)
            finally:
                active -= 1

        async def delete_path(self, path, *, is_dir):
            return None

        async def mkdir(self, path):
            return None

    actions = tuple(
        ContentTreeAction(
            "upsert",
            f"{index}.py",
            new_kind="file",
            artifact_path=f"{index}.py",
            md5=str(index),
        )
        for index in range(8)
    )
    create_task = asyncio.create_task
    with patch.object(concurrency_utils.asyncio, "create_task", wraps=create_task) as created:
        await execute_content_tree_actions(
            actions,
            store=Store(),
            artifact_ref=object(),
            target=Target(),
            concurrency=3,
        )

    assert peak == 3
    assert created.call_count == 3


@pytest.mark.asyncio
async def test_bounded_map_preserves_order_and_processes_all_items():
    from unittest.mock import patch

    import openviking.concurrency as concurrency_utils

    started = 0
    release = asyncio.Event()

    async def work(value: int) -> int:
        nonlocal started
        started += 1
        if started == 3:
            release.set()
        await release.wait()
        return value * 2

    create_task = asyncio.create_task
    with patch.object(concurrency_utils.asyncio, "create_task", wraps=create_task) as created:
        result = await concurrency_utils.bounded_map(range(100), work, concurrency=3)

    assert started == 100
    assert created.call_count == 3
    assert result == [index * 2 for index in range(100)]


@pytest.mark.asyncio
async def test_bounded_map_stops_claiming_after_worker_failure():
    from openviking.concurrency import bounded_map

    started: list[int] = []
    release = asyncio.Event()

    async def work(value: int) -> int:
        started.append(value)
        if value == 0:
            raise RuntimeError("boom")
        await release.wait()
        return value

    task = asyncio.create_task(bounded_map(range(100), work, concurrency=3))
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(RuntimeError, match="boom"):
        await task

    assert set(started) <= {0, 1, 2}


@pytest.mark.asyncio
async def test_content_executor_logs_failed_action_with_correlation(monkeypatch):
    import openviking.storage.context_update_plan as plan_module
    from openviking.service.task_work_index import bind_task_context
    from openviking.storage.context_update_plan import (
        ContentTreeAction,
        execute_content_tree_actions,
    )
    from openviking.telemetry import OperationTelemetry, bind_telemetry

    class Target:
        async def delete_path(self, path, *, is_dir):
            raise OSError("delete failed")

    action = ContentTreeAction("delete", "old.md", old_kind="file")
    telemetry = OperationTelemetry("add_resource_job", enabled=True)
    log_exception = MagicMock()
    monkeypatch.setattr(plan_module.logger, "exception", log_exception)

    with (
        bind_task_context("task-1", "acct", "user"),
        bind_telemetry(telemetry),
        pytest.raises(OSError, match="delete failed"),
    ):
        await execute_content_tree_actions(
            (action,),
            store=object(),
            artifact_ref=object(),
            target=Target(),
        )

    message, correlation, operation, relative_path, old_kind, new_kind = (
        log_exception.call_args.args
    )
    assert "[ContentTreeActionFailed]" in message
    assert correlation == f"task_id=task-1 telemetry_id={telemetry.telemetry_id}"
    assert (operation, relative_path, old_kind, new_kind) == (
        "delete",
        "old.md",
        "file",
        "-",
    )
    assert log_exception.call_args.kwargs == {}


@pytest.mark.asyncio
async def test_resource_processor_dispatches_direct_index_actions_without_semantic(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.context_update_plan import DirectIndexAction
    from openviking.utils.resource_processor import ResourceProcessor
    from openviking_cli.session.user_id import UserIdentifier

    enqueued = []

    async def enqueue(queue, message, **kwargs):
        del queue, kwargs
        enqueued.append(message)
        return True

    queue_manager = SimpleNamespace(
        EMBEDDING="embedding",
        get_queue=lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue_manager)
    monkeypatch.setattr("openviking.utils.embedding_utils._enqueue_embedding_message", enqueue)
    processor = ResourceProcessor(SimpleNamespace(get_embedder=lambda: None))
    processor._vectorize_resource_file = AsyncMock()
    ctx = RequestContext(UserIdentifier("acc", "user"), Role.USER)
    await processor._enqueue_index_actions(
        (
            DirectIndexAction("delete", "viking://resources/repo/a.py", 2, "id-a"),
            DirectIndexAction(
                "update_fields",
                "viking://resources/repo/b.py",
                2,
                "id-b",
                field_patch=FieldPatch(
                    {"search_tags": ["scope=new"]},
                    {"search_tags": "replace"},
                ),
            ),
            DirectIndexAction(
                "update_fields",
                "viking://resources/repo/clear.py",
                2,
                "id-clear",
                field_patch=FieldPatch(
                    {"search_tags": []},
                    {"search_tags": "replace"},
                ),
            ),
            DirectIndexAction(
                "merge",
                "viking://resources/repo/c.py",
                2,
                "id-c",
                field_patch=FieldPatch(
                    {"search_tags": ["scope=new"]},
                    {"search_tags": "append"},
                ),
                md5="new-md5",
            ),
        ),
        ctx=ctx,
    )
    assert [msg.action.value for msg in enqueued] == [
        "delete",
        "update_fields",
        "update_fields",
    ]
    assert enqueued[0].record_ids == ["id-a"]
    assert enqueued[1].update_fields["search_tags"] == ["scope=new"]
    assert enqueued[2].record_ids == ["id-clear"]
    assert enqueued[2].update_fields["search_tags"] == []
    assert enqueued[2].field_modes == {"search_tags": "replace"}
    processor._vectorize_resource_file.assert_awaited_once_with(
        "viking://resources/repo/c.py",
        ctx=ctx,
        file_md5="new-md5",
        scalar_override={"_record_id": "id-c"},
        action="merge",
        field_patch=FieldPatch(
            {"search_tags": ["scope=new"]},
            {"search_tags": "append"},
        ),
    )


def test_semantic_message_roundtrip_uses_explicit_plan():
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_msg import SemanticMsg

    plan = SemanticPlan(
        "viking://resources/repo",
        "resource",
        tree=SemanticTreeSnapshot(
            (
                SemanticTreeEntry("", "directory", "unchanged", "aggregate"),
                SemanticTreeEntry(
                    "a.py",
                    "file",
                    "modified",
                    "generate",
                    md5="new",
                    index_slots=(
                        IndexSlot(
                            2,
                            "actual-id",
                            {"abstract": "old"},
                            action="upsert",
                        ),
                    ),
                ),
            )
        ),
    )
    msg = SemanticMsg(uri=plan.root_uri, context_type="resource", plan=plan)
    restored = SemanticMsg.from_json(msg.to_json())
    assert restored.plan == plan
    assert (
        next(entry for entry in restored.plan.tree.entries if entry.relative_path == "a.py")
        .index_slots[0]
        .record_id
        == "actual-id"
    )


@pytest.mark.asyncio
async def test_v3_tree_reuses_explicit_node_without_listing_its_subtree(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    calls = []

    class FS:
        _async_agfs = None

        async def ls(self, uri, **kwargs):
            calls.append(uri)
            raise AssertionError("v3 plan must not list the live tree")

        def _uri_to_path(self, uri, ctx=None):
            return uri

    fs = FS()
    fs._async_agfs = fs
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    processor = AsyncMock()
    plan = SemanticPlan(
        root,
        "resource",
        tree=SemanticTreeSnapshot(
            (
                SemanticTreeEntry(
                    "",
                    "directory",
                    "unchanged",
                    "reuse",
                    index_slots=(IndexSlot(0, "root-l0", {"abstract": "old root"}),),
                ),
            )
        ),
    )
    executor = SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        semantic_plan=plan,
    )
    await executor.run(root)
    assert calls == []
    assert executor.root_write_result.wrote is False


@pytest.mark.asyncio
async def test_directory_index_slots_choose_exact_levels(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.abstract_overview import AbstractOverviewWriteResult
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    fs = SimpleNamespace(
        _async_agfs=None,
        _uri_to_path=lambda uri, ctx=None: uri,
    )
    fs._async_agfs = fs
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

    class Processor:
        _generate_overview = AsyncMock(return_value="overview")
        _vectorize_directory = AsyncMock(return_value={1})

        @staticmethod
        def _normalize_overview_generation(overview):
            return overview, "abstract"

    processor = Processor()
    plan = SemanticPlan(
        root,
        "resource",
        SemanticTreeSnapshot(
            (
                SemanticTreeEntry(
                    "",
                    "directory",
                    "unchanged",
                    "aggregate",
                    index_slots=(
                        IndexSlot(0, "l0", {"abstract": "old"}),
                        IndexSlot(1, "l1", None, action="upsert"),
                    ),
                ),
            )
        ),
    )
    executor = SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        semantic_plan=plan,
    )
    executor._write_directory_semantics = AsyncMock(
        return_value=AbstractOverviewWriteResult(wrote=True, overview_body_changed=True)
    )

    await executor.run(root)

    kwargs = processor._vectorize_directory.await_args.kwargs
    assert kwargs["include_abstract"] is False
    assert kwargs["include_overview"] is True


@pytest.mark.asyncio
async def test_directory_sidecar_write_failure_fails_semantic_plan(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.context_update_plan import (
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    fs = SimpleNamespace(_async_agfs=None, _uri_to_path=lambda uri, ctx=None: uri)
    fs._async_agfs = fs
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

    class Processor:
        _generate_overview = AsyncMock(return_value="overview")
        _vectorize_directory = AsyncMock(return_value={0, 1})

        @staticmethod
        def _normalize_overview_generation(overview):
            return overview, "abstract"

    processor = Processor()
    plan = SemanticPlan(
        root,
        "resource",
        SemanticTreeSnapshot((SemanticTreeEntry("", "directory", "unchanged", "aggregate"),)),
    )
    executor = SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        semantic_plan=plan,
    )
    executor._write_directory_semantics = AsyncMock(side_effect=OSError("sidecar unavailable"))

    with pytest.raises(OSError, match="sidecar unavailable"):
        await executor.run(root)

    processor._vectorize_directory.assert_not_awaited()


@pytest.mark.asyncio
async def test_directory_repair_passes_partial_update_per_missing_level(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.abstract_overview import AbstractOverviewWriteResult
    from openviking.storage.context_update_plan import (
        IndexAction,
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    fs = SimpleNamespace(_async_agfs=None, _uri_to_path=lambda uri, ctx=None: uri)
    fs._async_agfs = fs
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

    class Processor:
        _generate_overview = AsyncMock(return_value="overview")
        _vectorize_directory = AsyncMock(return_value={0, 1})

        @staticmethod
        def _normalize_overview_generation(overview):
            return overview, "abstract"

    processor = Processor()
    plan = SemanticPlan(
        root,
        "resource",
        SemanticTreeSnapshot(
            (
                SemanticTreeEntry(
                    "",
                    "directory",
                    "unchanged",
                    "aggregate",
                    index_slots=(
                        IndexSlot(
                            0,
                            "root-l0",
                            None,
                            action="merge",
                        ),
                        IndexSlot(
                            1,
                            "root-l1",
                            None,
                            action="upsert",
                        ),
                    ),
                ),
            )
        ),
    )
    executor = SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        semantic_plan=plan,
    )
    executor._write_directory_semantics = AsyncMock(
        return_value=AbstractOverviewWriteResult(
            wrote=True, overview_body_changed=True, abstract_body_changed=True
        )
    )

    await executor.run(root)

    assert processor._vectorize_directory.await_args.kwargs["actions"] == {
        0: IndexAction.MERGE,
        1: IndexAction.UPSERT,
    }


@pytest.mark.asyncio
async def test_directory_output_unchanged_still_applies_planned_scalar_fields(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.abstract_overview import AbstractOverviewWriteResult
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    fs = SimpleNamespace(_async_agfs=None, _uri_to_path=lambda uri, ctx=None: uri)
    fs._async_agfs = fs
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

    class Processor:
        _generate_overview = AsyncMock(return_value="overview")
        _vectorize_directory = AsyncMock(return_value=set())
        _update_vector_fields = AsyncMock(return_value=True)

        @staticmethod
        def _normalize_overview_generation(overview):
            return overview, "abstract"

    processor = Processor()
    plan = SemanticPlan(
        root,
        "resource",
        SemanticTreeSnapshot(
            (
                SemanticTreeEntry(
                    "",
                    "directory",
                    "unchanged",
                    "aggregate",
                    index_slots=(
                        IndexSlot(
                            0,
                            "root-l0",
                            {"abstract": "abstract"},
                            action="upsert",
                            upsert_fields={"search_tags": ["scope=new"]},
                            field_patch=FieldPatch(
                                {"search_tags": ["scope=new"]},
                                {"search_tags": "append"},
                            ),
                            fallback_to_patch=True,
                        ),
                        IndexSlot(
                            1,
                            "root-l1",
                            None,
                            action="upsert",
                        ),
                    ),
                ),
            )
        ),
    )
    executor = SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        semantic_plan=plan,
    )
    executor._write_directory_semantics = AsyncMock(
        return_value=AbstractOverviewWriteResult(wrote=True)
    )
    executor._check_dir_children_changed = AsyncMock(return_value=False)
    executor._read_existing_overview_abstract = AsyncMock(return_value=("overview", "abstract"))

    await executor.run(root)

    processor._vectorize_directory.assert_not_awaited()
    processor._update_vector_fields.assert_awaited_once_with(
        record_id="root-l0",
        uri=root,
        level=0,
        field_patch=FieldPatch(
            {"search_tags": ["scope=new"]},
            {"search_tags": "append"},
            {
                "abstract": "abstract",
                "uri": root,
                "level": 0,
                "account_id": "acc",
            },
        ),
        ctx=executor._ctx,
    )


@pytest.mark.asyncio
async def test_code_summary_unchanged_still_reembeds_after_content_change(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.abstract_overview import AbstractOverviewWriteResult
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    fs = SimpleNamespace(
        _async_agfs=None,
        _uri_to_path=lambda uri, ctx=None: uri,
        read_file_bytes=AsyncMock(return_value=b"changed body"),
    )
    fs._async_agfs = fs
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

    class Processor:
        _generate_single_file_summary = AsyncMock(return_value={"name": "a.py", "summary": "same"})
        _update_file_vector_fields = AsyncMock(return_value=True)
        _vectorize_single_file = AsyncMock(return_value=True)
        _generate_overview = AsyncMock(return_value="overview")
        _vectorize_directory = AsyncMock(return_value=set())

        @staticmethod
        def _normalize_overview_generation(overview):
            return overview, "abstract"

    processor = Processor()
    plan = SemanticPlan(
        root,
        "resource",
        SemanticTreeSnapshot(
            (
                SemanticTreeEntry(
                    "",
                    "directory",
                    "unchanged",
                    "aggregate",
                    index_slots=(IndexSlot(0, "root-l0", {"abstract": "old"}),),
                ),
                SemanticTreeEntry(
                    "a.py",
                    "file",
                    "modified",
                    "generate",
                    md5="new-md5",
                    index_slots=(
                        IndexSlot(
                            2,
                            "a-l2",
                            {"abstract": "same"},
                            action="upsert",
                        ),
                    ),
                ),
            )
        ),
        file_vector_source="summary_when_available",
    )
    executor = SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        semantic_plan=plan,
    )
    executor._write_directory_semantics = AsyncMock(
        return_value=AbstractOverviewWriteResult(wrote=True)
    )

    await executor.run(root)

    processor._vectorize_single_file.assert_awaited_once()
    processor._update_file_vector_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_repair_passes_partial_update_to_embedding(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.abstract_overview import AbstractOverviewWriteResult
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    fs = SimpleNamespace(
        _async_agfs=None,
        _uri_to_path=lambda uri, ctx=None: uri,
        read_file_bytes=AsyncMock(return_value=b"body"),
    )
    fs._async_agfs = fs
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

    class Processor:
        _generate_single_file_summary = AsyncMock(
            return_value={"name": "a.py", "summary": "summary"}
        )
        _vectorize_single_file = AsyncMock(return_value=True)
        _generate_overview = AsyncMock(return_value="overview")
        _vectorize_directory = AsyncMock(return_value=set())

        @staticmethod
        def _normalize_overview_generation(overview):
            return overview, "abstract"

    processor = Processor()
    plan = SemanticPlan(
        root,
        "resource",
        SemanticTreeSnapshot(
            (
                SemanticTreeEntry("", "directory", "unchanged", "aggregate"),
                SemanticTreeEntry(
                    "a.py",
                    "file",
                    "unchanged",
                    "generate",
                    md5="same",
                    index_slots=(
                        IndexSlot(
                            2,
                            "a-l2",
                            None,
                            action="merge",
                        ),
                    ),
                ),
            )
        ),
    )
    executor = SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        semantic_plan=plan,
    )
    executor._write_directory_semantics = AsyncMock(
        return_value=AbstractOverviewWriteResult(wrote=True)
    )

    await executor.run(root)

    assert processor._vectorize_single_file.await_args.kwargs["action"] == "merge"


@pytest.mark.asyncio
async def test_direct_only_plan_skips_semantic_queue(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.context_update_plan import DirectIndexAction
    from openviking.utils.resource_processor import ResourceProcessor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    lock = {"lease_ref": "plan"}
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(SimpleNamespace(get_embedder=lambda: None))
    processor._enqueue_index_actions = AsyncMock()
    summarizer = SimpleNamespace(summarize=AsyncMock())
    processor._get_summarizer = lambda: summarizer
    action = DirectIndexAction(
        "update_fields",
        root + "/a.py",
        2,
        "a-l2",
        field_patch=FieldPatch({"search_tags": ["scope=new"]}),
    )

    await processor.finish_prepared_resource(
        {
            "root_uri": root,
            "temp_uri": root,
            "source_committed": True,
            "target_preexisting": True,
            "root_is_file": False,
            "context_update_plan": {
                "root_uri": root,
                "context_type": "resource",
                "content_tree_actions": [],
                "semantic_plan": None,
                "direct_index_actions": [
                    {
                        "action": action.action.value,
                        "uri": action.uri,
                        "level": action.level,
                        "record_id": action.record_id,
                        "field_patch": action.field_patch.to_dict(),
                        "md5": action.md5,
                    }
                ],
            },
        },
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        resource_lock=lock,
        build_index=True,
        processing_mode="semantic_and_vectors",
    )

    processor._enqueue_index_actions.assert_awaited_once()
    summarizer.summarize.assert_not_awaited()
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_vectors_only_context_plan_does_not_run_legacy_vectorization(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.context_update_plan import ContextUpdatePlan, DirectIndexAction
    from openviking.utils.resource_processor import ResourceProcessor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    lock = {"lease_ref": "plan"}
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor = ResourceProcessor(SimpleNamespace(get_embedder=lambda: None))
    processor._enqueue_index_actions = AsyncMock()
    processor._vectorize_prepared_files = AsyncMock(
        side_effect=AssertionError("legacy vectors_only path must not run")
    )
    action = DirectIndexAction("upsert", root + "/a.py", 2, "a-l2", md5="m")
    plan = ContextUpdatePlan(root, "resource", direct_index_actions=(action,))

    await processor.finish_prepared_resource(
        {
            "root_uri": root,
            "temp_uri": root,
            "source_committed": True,
            "target_preexisting": True,
            "root_is_file": False,
            "context_update_plan": plan.to_dict(),
        },
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        resource_lock=lock,
        build_index=True,
        processing_mode="vectors_only",
    )

    processor._enqueue_index_actions.assert_awaited_once_with(
        plan.direct_index_actions, ctx=processor._enqueue_index_actions.await_args.kwargs["ctx"]
    )
    processor._vectorize_prepared_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_index_enqueue_failure_releases_lock():
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.context_update_plan import ContextUpdatePlan, DirectIndexAction
    from openviking.utils.resource_processor import ResourceProcessor
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://resources/repo"
    lock = {"lease_ref": "plan"}
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    processor = ResourceProcessor(SimpleNamespace(get_embedder=lambda: None))
    processor._enqueue_index_actions = AsyncMock(side_effect=RuntimeError("queue unavailable"))
    plan = ContextUpdatePlan(
        root,
        "resource",
        direct_index_actions=(DirectIndexAction("delete", root + "/a.py", 2, "a-l2"),),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                "openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs
            )
            await processor.finish_prepared_resource(
                {
                    "root_uri": root,
                    "temp_uri": root,
                    "source_committed": True,
                    "target_preexisting": True,
                    "root_is_file": False,
                    "context_update_plan": plan.to_dict(),
                },
                ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
                resource_lock=lock,
                build_index=True,
            )

    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_semantic_processor_runs_only_plan_execution_roots(monkeypatch):
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )
    from openviking.storage.queuefs.semantic_msg import SemanticMsg
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    calls = []

    class Executor:
        stale = True
        root_write_result = None

        def __init__(self, **kwargs):
            calls.append(("init", kwargs["semantic_plan"]))

        async def run(self, uri):
            calls.append(("run", uri))

        def get_stats(self):
            return SimpleNamespace()

    root = "viking://resources/repo"
    plan = SemanticPlan(
        root,
        "resource",
        SemanticTreeSnapshot(
            (
                SemanticTreeEntry(
                    "",
                    "directory",
                    "unchanged",
                    "aggregate",
                    index_slots=(IndexSlot(0, "root-l0", {"abstract": "root"}),),
                ),
                SemanticTreeEntry(
                    "src",
                    "directory",
                    "unchanged",
                    "aggregate",
                    index_slots=(IndexSlot(0, "src-l0", {"abstract": "old"}),),
                ),
                SemanticTreeEntry(
                    "src/a.py",
                    "file",
                    "modified",
                    "generate",
                    md5="new",
                    index_slots=(
                        IndexSlot(
                            2,
                            "a-l2",
                            {"abstract": "old a"},
                            action="upsert",
                        ),
                    ),
                ),
                SemanticTreeEntry(
                    "docs",
                    "directory",
                    "unchanged",
                    "aggregate",
                    index_slots=(IndexSlot(0, "docs-l0", {"abstract": "old"}),),
                ),
                SemanticTreeEntry(
                    "docs/b.md",
                    "file",
                    "modified",
                    "generate",
                    md5="new",
                    index_slots=(
                        IndexSlot(
                            2,
                            "b-l2",
                            {"abstract": "old b"},
                            action="upsert",
                        ),
                    ),
                ),
            )
        ),
    )
    fs = SimpleNamespace(exists=AsyncMock(return_value=True))
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticTreeExecutor", Executor
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    processor = SemanticProcessor()
    processor._cleanup_local_artifact = AsyncMock()
    msg = SemanticMsg(
        uri=root,
        context_type="resource",
        account_id="acc",
        user_id="user",
        role="user",
        plan=plan,
    )

    await processor.on_dequeue(msg.to_dict())

    assert [value for kind, value in calls if kind == "run"] == [root]


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_process_resource_does_not_handoff_local_artifact(
    monkeypatch, tmp_path, cleanup_fails
):
    from openviking.parse.output import LocalParseOutputStore
    from openviking.server.identity import RequestContext, Role
    from openviking.storage.context_update_plan import ContextUpdatePlan
    from openviking.utils.resource_processor import ResourceProcessor
    from openviking_cli.session.user_id import UserIdentifier

    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    ref = await store.create_artifact()
    await store.write_bytes(ref, "repository/a.py", b"a")
    if cleanup_fails:
        store.cleanup = AsyncMock(side_effect=OSError("cleanup unavailable"))
    processor = ResourceProcessor(SimpleNamespace(get_embedder=lambda: None))
    processor._build_parse_output_store = lambda: store
    processor._get_media_processor = lambda: SimpleNamespace(
        process=AsyncMock(
            return_value=SimpleNamespace(
                temp_dir_path=ref.root,
                source_path="x",
                source_format="repository",
                meta={},
                warnings=[],
                artifact_ref=ref,
                ensure_artifact_ref=lambda: ref,
            )
        )
    )
    processor.tree_builder.finalize_from_temp = AsyncMock(
        return_value=SimpleNamespace(
            root=SimpleNamespace(
                uri="viking://resources/repo",
                temp_uri=ref.root + "/repository",
            ),
            _root_is_file=False,
        )
    )
    processor._commit_directory_artifact_with_plan = AsyncMock(
        return_value=ContextUpdatePlan("viking://resources/repo", "resource")
    )
    viking_fs = SimpleNamespace(
        exists=AsyncMock(return_value=False),
        _uri_to_path=lambda uri, ctx=None: uri,
        bind_request_context=lambda ctx: nullcontext(),
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: viking_fs)
    processor.acquire_resource_lock = AsyncMock(return_value={"lease_ref": "x"})

    result = await processor.process_resource(
        path="x",
        ctx=RequestContext(UserIdentifier("acc", "user"), Role.USER),
        to="viking://resources/repo",
        defer_post_processing=True,
        build_index=True,
    )

    assert result["_post_process"]["artifact_ref"] is None
    assert "file_md5s" not in result["_post_process"]
    assert "file_abstracts" not in result["_post_process"]
    assert "artifact_files" not in result["_post_process"]
    artifact_path = tmp_path.joinpath("artifacts", ref.root.split("/")[-1])
    assert artifact_path.exists() is cleanup_fails
