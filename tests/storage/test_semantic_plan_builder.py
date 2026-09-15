# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for compiling a committed resource diff into a SemanticPlan."""

from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_plan_builder import (
    build_initial_semantic_plan,
    build_semantic_plan,
)
from openviking.storage.viking_fs._diff_plan import (
    DiffPlan,
    NewEntry,
    TargetFile,
)


class _Ctx:
    account_id = "acct"


@pytest.mark.asyncio
async def test_builder_prunes_unchanged_subtrees_and_hydrates_only_dependencies():
    root = "viking://resources/repo"
    inventory = {
        "root-l0": {"id": "root-l0", "uri": root, "level": 0},
        "root-l1": {"id": "root-l1", "uri": root, "level": 1},
        "src-l0": {"id": "src-l0", "uri": f"{root}/src", "level": 0},
        "src-l1": {"id": "src-l1", "uri": f"{root}/src", "level": 1},
        "a-l2": {
            "id": "a-l2",
            "uri": f"{root}/src/a.py",
            "level": 2,
            "md5": "old-a",
        },
        "b-l2": {
            "id": "b-l2",
            "uri": f"{root}/src/b.py",
            "level": 2,
            "md5": "same-b",
        },
        "utils-l0": {
            "id": "utils-l0",
            "uri": f"{root}/src/utils",
            "level": 0,
        },
        "utils-l1": {
            "id": "utils-l1",
            "uri": f"{root}/src/utils",
            "level": 1,
        },
        "docs-l0": {"id": "docs-l0", "uri": f"{root}/docs", "level": 0},
        "docs-l1": {"id": "docs-l1", "uri": f"{root}/docs", "level": 1},
        "ghost-l2": {
            "id": "ghost-l2",
            "uri": f"{root}/ghost.py#chunk_0001",
            "level": 2,
        },
    }
    hydrated = {
        record_id: {
            **record,
            "abstract": f"abstract:{record_id}",
            "created_at": "2026-09-01T00:00:00Z",
            "vector": [9.9],
            "content": "must be pruned",
        }
        for record_id, record in inventory.items()
    }
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.return_value = hydrated
    new = {
        "src/a.py": NewEntry(md5="new-a"),
        "src/b.py": NewEntry(md5="same-b"),
        "src/utils/c.py": NewEntry(md5="same-c"),
        "docs/readme.md": NewEntry(md5="same-doc"),
    }
    target_files = {path: TargetFile(is_dir=False) for path in new}
    target_files.update(
        {
            "src": TargetFile(is_dir=True),
            "src/utils": TargetFile(is_dir=True),
            "docs": TargetFile(is_dir=True),
        }
    )

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new=new,
        target_files=target_files,
        diff_plan=DiffPlan(
            modified=["src/a.py"],
            unchanged=["src/b.py", "src/utils/c.py", "docs/readme.md"],
            new_files=sorted(new),
            new_md5s={path: entry.md5 for path, entry in new.items()},
            orphan_vectors=["ghost.py#chunk_0001"],
        ),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=True,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert set(entries) == {
        "",
        "docs",
        "src",
        "src/a.py",
        "src/b.py",
        "src/utils",
    }
    assert entries["src/a.py"].state == "modified"
    assert entries["src/b.py"].state == "unchanged"
    assert [record.level for record in entries["src"].indexed_records] == [0, 1]
    assert [record.level for record in entries["src/utils"].indexed_records] == [0]
    assert entries["src/utils"].indexed_records[0].abstract == "abstract:utils-l0"
    assert not hasattr(entries["src/a.py"].indexed_records[0], "vector")
    expected = vikingdb.hydrate_incremental_records.await_args.args[0]
    assert set(expected) == {
        "root-l0",
        "root-l1",
        "docs-l0",
        "src-l0",
        "src-l1",
        "a-l2",
        "b-l2",
        "utils-l0",
    }
    assert plan.orphan_vector_deletes[0].record_id == "ghost-l2"
    assert plan.file_vector_source.value == "summary_when_available"


@pytest.mark.asyncio
async def test_builder_connects_deep_change_through_minimal_ancestor_tree():
    root = "viking://resources/repo"
    new = {
        "root.py": NewEntry(md5="new-root"),
        "docs/deep/tests/test_a.py": NewEntry(md5="new-deep"),
    }
    target_files = {
        "root.py": TargetFile(is_dir=False),
        "docs": TargetFile(is_dir=True),
        "docs/deep": TargetFile(is_dir=True),
        "docs/deep/tests": TargetFile(is_dir=True),
        "docs/deep/tests/test_a.py": TargetFile(is_dir=False),
    }
    inventory = {}
    for rel_path, levels in {
        "": (0, 1),
        "docs": (0, 1),
        "docs/deep": (0, 1),
        "docs/deep/tests": (0, 1),
        "root.py": (2,),
        "docs/deep/tests/test_a.py": (2,),
    }.items():
        uri = root if not rel_path else f"{root}/{rel_path}"
        for level in levels:
            record_id = f"{rel_path or 'root'}-l{level}"
            inventory[record_id] = {
                "id": record_id,
                "uri": uri,
                "level": level,
                "md5": "old",
            }
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.side_effect = lambda expected, **_: {
        record_id: {**identity, "id": record_id, "abstract": f"abstract:{record_id}"}
        for record_id, identity in expected.items()
    }

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new=new,
        target_files=target_files,
        diff_plan=DiffPlan(
            modified=["root.py", "docs/deep/tests/test_a.py"],
            new_files=sorted(new),
            new_md5s={path: entry.md5 for path, entry in new.items()},
        ),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=True,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert {"", "docs", "docs/deep", "docs/deep/tests"} <= set(entries)
    assert {path for path, entry in entries.items() if entry.state == "modified"} == {
        "root.py",
        "docs/deep/tests/test_a.py",
    }
    assert plan.execution_root_uris() == (root,)


@pytest.mark.asyncio
async def test_builder_first_import_keeps_complete_current_tree_without_hydration():
    root = "viking://resources/repo"
    vikingdb = AsyncMock()
    new = {
        "a.py": NewEntry(md5="a"),
        "src/b.py": NewEntry(md5="b"),
    }

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new=new,
        target_files={},
        diff_plan=DiffPlan(
            added=["a.py", "src/b.py"],
            new_files=sorted(new),
            new_md5s={path: entry.md5 for path, entry in new.items()},
        ),
        inventory={},
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=False,
        is_code_repo=False,
        root_preexisting=False,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert set(entries) == {"", "a.py", "src", "src/b.py"}
    assert all(entry.state == "added" for entry in entries.values())
    assert plan.outputs.vectorize is False
    vikingdb.hydrate_incremental_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_builder_reads_manifest_and_marks_every_node_added():
    root = "viking://resources/repo"
    new = {
        "a.py": NewEntry(md5="a"),
        "src/b.py": NewEntry(md5="b"),
    }
    store = object()
    ref = object()
    read_manifest = AsyncMock(return_value=new)

    plan = await build_initial_semantic_plan(
        root_uri=root,
        context_type="resource",
        store=store,
        artifact_ref=ref,
        doc_rel="repository",
        md5_by_rel={"a.py": "final-a", "src/b.py": "final-b"},
        vectorize=True,
        is_code_repo=True,
        read_manifest=read_manifest,
    )

    read_manifest.assert_awaited_once_with(store, ref, doc_rel="repository")
    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert entries["a.py"].md5 == "final-a"
    assert entries["src/b.py"].md5 == "final-b"
    assert all(entry.state == "added" for entry in entries.values())


@pytest.mark.asyncio
async def test_incremental_builder_marks_new_directory_and_its_files_added():
    root = "viking://resources/repo"
    vikingdb = AsyncMock()
    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={
            "src": NewEntry(is_dir=True),
            "src/new": NewEntry(is_dir=True),
            "src/new/a.py": NewEntry(md5="a"),
        },
        target_files={"src": TargetFile(is_dir=True)},
        diff_plan=DiffPlan(added=["src/new/a.py"]),
        inventory={},
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=True,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert entries["src/new"].state == "added"
    assert entries["src/new/a.py"].state == "added"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("new_entry", "target_file", "inventory_levels"),
    [
        (NewEntry(md5="new-file"), TargetFile(is_dir=True), (0, 1)),
        (NewEntry(is_dir=True), TargetFile(is_dir=False), (2,)),
    ],
)
async def test_builder_keeps_structural_old_levels_on_added_entry(
    new_entry, target_file, inventory_levels
):
    # Structural paths are excluded from orphan_vector_deletes because their old
    # inventory is carried on the added entry. The processor then deletes only
    # levels invalid for the new kind before the DAG rebuilds the new levels.
    root = "viking://resources/repo"
    inventory = {
        f"old-l{level}": {
            "id": f"old-l{level}",
            "uri": f"{root}/mod",
            "level": level,
        }
        for level in inventory_levels
    }
    vikingdb = AsyncMock()

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={"mod": new_entry},
        target_files={"mod": target_file},
        diff_plan=DiffPlan(
            structural=["mod"],
            added=[] if new_entry.is_dir else ["mod"],
            added_dirs=["mod"] if new_entry.is_dir else [],
            new_files=[] if new_entry.is_dir else ["mod"],
            new_md5s={"mod": new_entry.md5} if new_entry.md5 else {},
        ),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert entries["mod"].state == "added"
    assert [record.level for record in entries["mod"].indexed_records] == list(
        inventory_levels
    )
    assert plan.orphan_vector_deletes == ()
    vikingdb.hydrate_incremental_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_builder_keeps_deleted_records_on_tombstone_and_pure_orphans_separate():
    root = "viking://resources/repo"
    inventory = {
        "old-l2": {"id": "old-l2", "uri": f"{root}/old.py", "level": 2},
        "ghost-l2": {"id": "ghost-l2", "uri": f"{root}/ghost.py", "level": 2},
    }
    vikingdb = AsyncMock()

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={},
        target_files={"old.py": TargetFile(is_dir=False)},
        diff_plan=DiffPlan(deleted=["old.py"], orphan_vectors=["ghost.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert entries["old.py"].state == "deleted"
    assert entries["old.py"].indexed_records[0].record_id == "old-l2"
    assert [record.record_id for record in plan.orphan_vector_deletes] == ["ghost-l2"]
    vikingdb.hydrate_incremental_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_builder_deletes_directory_level_orphans_without_business_tree_node():
    root = "viking://resources/repo"
    inventory = {
        "ghost-l0": {
            "id": "ghost-l0",
            "uri": f"{root}/missing-dir",
            "level": 0,
        },
        "ghost-l1": {
            "id": "ghost-l1",
            "uri": f"{root}/missing-dir",
            "level": 1,
        },
    }
    vikingdb = AsyncMock()

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={"a.py": NewEntry(md5="same")},
        target_files={"a.py": TargetFile(is_dir=False)},
        diff_plan=DiffPlan(unchanged=["a.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    assert [record.record_id for record in plan.orphan_vector_deletes] == [
        "ghost-l0",
        "ghost-l1",
    ]


@pytest.mark.asyncio
async def test_builder_deletes_invalid_l2_on_root_directory_and_control_path():
    root = "viking://resources/repo"
    inventory = {
        "root-l0": {"id": "root-l0", "uri": root, "level": 0},
        "root-l1": {"id": "root-l1", "uri": root, "level": 1},
        "root-l2": {"id": "root-l2", "uri": root, "level": 2},
        "sidecar-l2": {
            "id": "sidecar-l2",
            "uri": f"{root}/.abstract.md",
            "level": 2,
        },
    }
    vikingdb = AsyncMock()

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={},
        target_files={},
        diff_plan=DiffPlan(orphan_vectors=["", ".abstract.md"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    assert [record.record_id for record in plan.orphan_vector_deletes] == [
        "root-l2",
        "sidecar-l2",
    ]


@pytest.mark.asyncio
async def test_builder_repairs_existing_directory_missing_a_semantic_level():
    root = "viking://resources/repo"
    inventory = {
        "root-l0": {"id": "root-l0", "uri": root, "level": 0},
        "a-l2": {"id": "a-l2", "uri": f"{root}/a.py", "level": 2, "md5": "same"},
    }
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.side_effect = lambda expected, **_: {
        record_id: {
            **identity,
            "id": record_id,
            "abstract": f"abstract:{record_id}",
        }
        for record_id, identity in expected.items()
    }

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={"a.py": NewEntry(md5="same")},
        target_files={"a.py": TargetFile(is_dir=False)},
        diff_plan=DiffPlan(unchanged=["a.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert entries[""].state == "modified"
    assert entries["a.py"].state == "unchanged"
    assert plan.execution_root_uris() == (root,)


@pytest.mark.asyncio
async def test_builder_returns_empty_work_for_healthy_noop():
    root = "viking://resources/repo"
    inventory = {
        "root-l0": {"id": "root-l0", "uri": root, "level": 0},
        "root-l1": {"id": "root-l1", "uri": root, "level": 1},
        "a-l2": {
            "id": "a-l2",
            "uri": f"{root}/a.py",
            "level": 2,
            "md5": "same",
        },
    }
    vikingdb = AsyncMock()

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={"a.py": NewEntry(md5="same")},
        target_files={"a.py": TargetFile(is_dir=False)},
        diff_plan=DiffPlan(unchanged=["a.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    assert plan.tree.entries == ()
    assert plan.orphan_vector_deletes == ()
    assert plan.execution_root_uris() == ()
    vikingdb.hydrate_incremental_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_summarize_only_refreshes_complete_tree_without_vector_abstracts():
    root = "viking://resources/repo"
    new = {
        "a.py": NewEntry(md5="same-a"),
        "src": NewEntry(is_dir=True),
        "src/b.py": NewEntry(md5="same-b"),
    }
    target_files = {
        "a.py": TargetFile(is_dir=False),
        "src": TargetFile(is_dir=True),
        "src/b.py": TargetFile(is_dir=False),
    }
    vikingdb = AsyncMock()

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new=new,
        target_files=target_files,
        diff_plan=DiffPlan(unchanged=["a.py", "src/b.py"]),
        inventory={},
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=False,
        is_code_repo=False,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert set(entries) == {"", "a.py", "src", "src/b.py"}
    assert all(entry.state == "modified" for entry in entries.values())
    assert plan.execution_root_uris() == (root,)
    assert plan.outputs.vectorize is False
    vikingdb.hydrate_incremental_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_summarize_only_regenerates_missing_unchanged_file_summary():
    root = "viking://resources/repo"
    inventory = {
        "root-l0": {"id": "root-l0", "uri": root, "level": 0},
        "root-l1": {"id": "root-l1", "uri": root, "level": 1},
        "changed-l2": {
            "id": "changed-l2",
            "uri": f"{root}/changed.py",
            "level": 2,
        },
    }
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.side_effect = lambda expected, **_: {
        record_id: {**identity, "id": record_id, "abstract": "old changed"}
        for record_id, identity in expected.items()
    }

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={
            "changed.py": NewEntry(md5="new"),
            "sibling.py": NewEntry(md5="same"),
        },
        target_files={
            "changed.py": TargetFile(is_dir=False),
            "sibling.py": TargetFile(is_dir=False),
        },
        diff_plan=DiffPlan(modified=["changed.py"], unchanged=["sibling.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=False,
        is_code_repo=False,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert entries["changed.py"].state == "modified"
    assert entries["sibling.py"].state == "modified"
    assert plan.outputs.vectorize is False


@pytest.mark.asyncio
async def test_builder_regenerates_unchanged_dependency_with_empty_abstract():
    root = "viking://resources/repo"
    inventory = {
        "root-l0": {"id": "root-l0", "uri": root, "level": 0},
        "root-l1": {"id": "root-l1", "uri": root, "level": 1},
        "changed-l2": {
            "id": "changed-l2",
            "uri": f"{root}/changed.py",
            "level": 2,
        },
        "sibling-l2": {
            "id": "sibling-l2",
            "uri": f"{root}/sibling.py",
            "level": 2,
        },
    }
    hydrated = {
        "root-l0": {**inventory["root-l0"], "abstract": "old root"},
        "root-l1": {**inventory["root-l1"], "abstract": "old overview"},
        "changed-l2": {**inventory["changed-l2"], "abstract": "old changed"},
        "sibling-l2": {**inventory["sibling-l2"], "abstract": ""},
    }
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.side_effect = lambda expected, **_: {
        record_id: hydrated[record_id] for record_id in expected
    }

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={
            "changed.py": NewEntry(md5="new"),
            "sibling.py": NewEntry(md5="same"),
        },
        target_files={
            "changed.py": TargetFile(is_dir=False),
            "sibling.py": TargetFile(is_dir=False),
        },
        diff_plan=DiffPlan(modified=["changed.py"], unchanged=["sibling.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert entries["sibling.py"].state == "modified"


@pytest.mark.asyncio
async def test_builder_expands_directory_with_empty_abstract_for_targeted_repair():
    root = "viking://resources/repo"
    inventory = {
        "root-l0": {"id": "root-l0", "uri": root, "level": 0},
        "root-l1": {"id": "root-l1", "uri": root, "level": 1},
        "changed-l2": {"id": "changed-l2", "uri": f"{root}/changed.py", "level": 2},
        "child-l0": {"id": "child-l0", "uri": f"{root}/child", "level": 0},
        "child-l1": {"id": "child-l1", "uri": f"{root}/child", "level": 1},
        "nested-l2": {"id": "nested-l2", "uri": f"{root}/child/nested.py", "level": 2},
    }
    hydrated = {
        record_id: {
            **record,
            "abstract": "" if record_id == "child-l0" else f"abstract:{record_id}",
        }
        for record_id, record in inventory.items()
    }
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.side_effect = lambda expected, **_: {
        record_id: hydrated[record_id] for record_id in expected
    }

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={
            "changed.py": NewEntry(md5="new"),
            "child": NewEntry(is_dir=True),
            "child/nested.py": NewEntry(md5="same"),
        },
        target_files={
            "changed.py": TargetFile(is_dir=False),
            "child": TargetFile(is_dir=True),
            "child/nested.py": TargetFile(is_dir=False),
        },
        diff_plan=DiffPlan(modified=["changed.py"], unchanged=["child/nested.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    entries = {entry.relative_path: entry for entry in plan.tree.entries}
    assert entries["child"].state == "modified"
    assert [record.level for record in entries["child"].indexed_records] == [0, 1]
    assert entries["child/nested.py"].indexed_records[0].abstract == "abstract:nested-l2"


@pytest.mark.asyncio
async def test_builder_downgrades_disappeared_modified_l2_to_repair():
    root = "viking://resources/repo"
    inventory = {"a-l2": {"id": "a-l2", "uri": f"{root}/a.py", "level": 2}}
    vikingdb = AsyncMock()
    vikingdb.hydrate_incremental_records.return_value = {}

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={"a.py": NewEntry(md5="new")},
        target_files={"a.py": TargetFile(is_dir=False)},
        diff_plan=DiffPlan(modified=["a.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    entry = next(item for item in plan.tree.entries if item.relative_path == "a.py")
    assert entry.state == "modified"
    assert entry.indexed_records == ()


@pytest.mark.asyncio
async def test_builder_puts_stale_same_path_vector_on_added_entry_for_delete():
    root = "viking://resources/repo"
    inventory = {
        "stale-l2": {
            "id": "stale-l2",
            "uri": f"{root}/a.py",
            "level": 2,
            "md5": "stale",
        }
    }
    vikingdb = AsyncMock()

    plan = await build_semantic_plan(
        root_uri=root,
        context_type="resource",
        new={"a.py": NewEntry(md5="new")},
        target_files={},
        diff_plan=DiffPlan(added=["a.py"], orphan_vectors=["a.py"]),
        inventory=inventory,
        vikingdb=vikingdb,
        ctx=_Ctx(),
        vectorize=True,
        is_code_repo=False,
        root_preexisting=True,
    )

    entry = next(item for item in plan.tree.entries if item.relative_path == "a.py")
    assert entry.state == "added"
    assert [record.record_id for record in entry.indexed_records] == ["stale-l2"]
    assert plan.orphan_vector_deletes == ()
    vikingdb.hydrate_incremental_records.assert_not_awaited()
