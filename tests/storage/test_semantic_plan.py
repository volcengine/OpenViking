# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_plan import (
    FileVectorSource,
    IndexedRecordSnapshot,
    ParentPropagation,
    SemanticOutputs,
    SemanticPlan,
    SemanticTreeEntry,
    SemanticTreeSnapshot,
    VectorRecordRef,
)
from openviking.utils.ingest_options import IngestOptions


def _plan() -> SemanticPlan:
    return SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry(
                    relative_path="",
                    kind="directory",
                    state="unchanged",
                    indexed_records=(
                        IndexedRecordSnapshot(
                            record_id="root-l0",
                            level=0,
                            abstract="repository",
                        ),
                    ),
                ),
                SemanticTreeEntry(
                    relative_path="src/a.py",
                    kind="file",
                    state="modified",
                    md5="new-md5",
                    indexed_records=(
                        IndexedRecordSnapshot(
                            record_id="a-l2",
                            level=2,
                            abstract="old abstract",
                            md5="old-md5",
                            created_at="2026-09-01T00:00:00Z",
                            search_tags=("language:python",),
                        ),
                    ),
                ),
                SemanticTreeEntry(
                    relative_path="src/old.py",
                    kind="file",
                    state="deleted",
                    indexed_records=(
                        IndexedRecordSnapshot(record_id="old-l2", level=2),
                    ),
                ),
            )
        ),
        orphan_vector_deletes=(
            VectorRecordRef(
                record_id="ghost-l2",
                uri="viking://resources/repo/ghost.py",
                level=2,
            ),
        ),
        outputs=SemanticOutputs(vectorize=True),
        propagation=ParentPropagation(enabled=True, use_freshness=True),
        file_vector_source=FileVectorSource.SUMMARY_WHEN_AVAILABLE,
        ingest_options=IngestOptions(
            search_tags=["language:python"], search_tag_mode="append"
        ),
        source_metadata={"kind": "git", "uri": "https://example.com/repo.git"},
    )


def test_semantic_plan_round_trips_through_semantic_msg() -> None:
    msg = SemanticMsg(
        uri="viking://resources/repo",
        context_type="resource",
        plan_version=1,
        plan=_plan(),
    )

    restored = SemanticMsg.from_json(msg.to_json())

    assert restored.plan_version == 1
    assert restored.plan == _plan()
    assert restored.plan.tree.entries[1].indexed_records[0].search_tags == (
        "language:python",
    )


def test_legacy_semantic_msg_has_no_plan() -> None:
    restored = SemanticMsg.from_dict(
        {"uri": "viking://resources/repo", "context_type": "resource"}
    )

    assert restored.plan_version is None
    assert restored.plan is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"relative_path": "/absolute.py", "kind": "file", "state": "added"},
        {"relative_path": "../escape.py", "kind": "file", "state": "added"},
        {"relative_path": "a.py", "kind": "invalid", "state": "added"},
        {"relative_path": "a.py", "kind": "file", "state": "invalid"},
    ],
)
def test_semantic_plan_rejects_invalid_entries(kwargs) -> None:
    with pytest.raises(ValueError):
        SemanticTreeEntry(**kwargs)


def test_semantic_plan_rejects_duplicate_paths() -> None:
    duplicate = SemanticTreeEntry(
        relative_path="src/a.py", kind="file", state="modified"
    )

    with pytest.raises(ValueError, match="duplicate"):
        SemanticTreeSnapshot(entries=(duplicate, duplicate))


def test_semantic_plan_rejects_deleted_entry_with_md5() -> None:
    with pytest.raises(ValueError, match="deleted"):
        SemanticTreeEntry(
            relative_path="src/old.py",
            kind="file",
            state="deleted",
            md5="must-not-exist",
        )


def test_semantic_plan_rejects_orphan_outside_root() -> None:
    plan = _plan().to_dict()
    plan["orphan_vector_deletes"][0]["uri"] = "viking://resources/other/ghost.py"

    with pytest.raises(ValueError, match="outside root"):
        SemanticPlan.from_dict(plan)


def test_semantic_plan_rejects_delete_and_live_record_conflict() -> None:
    with pytest.raises(ValueError, match="conflicting vector operation"):
        SemanticPlan(
            root_uri="viking://resources/repo",
            context_type="resource",
            tree=SemanticTreeSnapshot(
                entries=(
                    SemanticTreeEntry(
                        "a.py",
                        "file",
                        "modified",
                        md5="new",
                        indexed_records=(IndexedRecordSnapshot("a-l2", 2),),
                    ),
                )
            ),
            orphan_vector_deletes=(
                VectorRecordRef("a-l2", "viking://resources/repo/a.py", 2),
            ),
        )


def test_semantic_plan_derives_minimal_execution_roots() -> None:
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry(
                    relative_path="src", kind="directory", state="unchanged"
                ),
                SemanticTreeEntry(
                    relative_path="src/a.py", kind="file", state="modified"
                ),
                SemanticTreeEntry(
                    relative_path="docs", kind="directory", state="unchanged"
                ),
                SemanticTreeEntry(
                    relative_path="docs/b.md", kind="file", state="modified"
                ),
            )
        ),
    )

    assert plan.execution_root_uris() == (
        "viking://resources/repo/docs",
        "viking://resources/repo/src",
    )


def test_semantic_plan_added_directory_executes_from_its_parent() -> None:
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("src", "directory", "unchanged"),
                SemanticTreeEntry("src/new", "directory", "added"),
                SemanticTreeEntry("src/new/a.py", "file", "added", md5="a"),
            )
        ),
    )

    assert plan.execution_root_uris() == ("viking://resources/repo/src",)

def test_semantic_plan_modified_directory_executes_the_directory_itself() -> None:
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("src", "directory", "modified"),
                SemanticTreeEntry("src/a.py", "file", "unchanged", md5="same"),
            )
        ),
    )

    assert plan.execution_root_uris() == ("viking://resources/repo/src",)


def test_semantic_plan_keeps_disconnected_nested_execution_root() -> None:
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "unchanged"),
                SemanticTreeEntry("root.py", "file", "modified"),
                SemanticTreeEntry(
                    "docs/deep/tests", "directory", "unchanged"
                ),
                SemanticTreeEntry(
                    "docs/deep/tests/test_a.py", "file", "modified"
                ),
            )
        ),
    )

    # The root is a lexical ancestor of docs/deep/tests, but the sparse plan
    # intentionally omits docs and docs/deep. The root DAG therefore cannot
    # reach the nested change, which must remain a separate execution root.
    assert plan.execution_root_uris() == (
        "viking://resources/repo",
        "viking://resources/repo/docs/deep/tests",
    )


def test_semantic_plan_deduplicates_reachable_nested_execution_root() -> None:
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "unchanged"),
                SemanticTreeEntry("docs", "directory", "unchanged"),
                SemanticTreeEntry("docs/deep", "directory", "unchanged"),
                SemanticTreeEntry(
                    "docs/deep/tests", "directory", "unchanged"
                ),
                SemanticTreeEntry(
                    "docs/deep/tests/test_a.py", "file", "modified"
                ),
            )
        ),
    )

    assert plan.execution_root_uris() == ("viking://resources/repo",)


def test_semantic_plan_rejects_changed_entry_without_direct_parent() -> None:
    plan = SemanticPlan(
        root_uri="viking://resources/repo",
        context_type="resource",
        tree=SemanticTreeSnapshot(
            entries=(
                SemanticTreeEntry("", "directory", "unchanged"),
                SemanticTreeEntry("missing/a.py", "file", "modified"),
            )
        ),
    )

    with pytest.raises(ValueError, match="lacks execution directory 'missing'"):
        plan.execution_root_uris()
