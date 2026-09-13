# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Decision-table tests for the pure incremental DiffPlan builder.

The builder is IO-free: it takes the new-artifact manifest (N), the target file
tree (F) and the target vector records (V) as plain data and classifies every
business file. These tests pin the F/V/N decision table, type conflicts, control
file exclusion, missing-md5 fallback, and the completeness gate that forbids
deletions when a snapshot is untrusted.
"""

import pytest

from openviking.storage.viking_fs._diff_plan import (
    NewEntry,
    TargetFile,
    TargetVector,
    build_diff_plan,
)


def _n(md5: str, is_dir: bool = False) -> NewEntry:
    return NewEntry(md5=md5, is_dir=is_dir)


def _f(is_dir: bool = False) -> TargetFile:
    return TargetFile(is_dir=is_dir)


def _v(md5: str) -> TargetVector:
    return TargetVector(md5=md5, abstract="")


class TestDecisionTable:
    def test_intersection_same_md5_is_unchanged(self) -> None:
        plan = build_diff_plan(
            new={"a.py": _n("m1")},
            target_files={"a.py": _f()},
            target_vectors={"a.py": TargetVector(md5="m1", abstract="old summary")},
        )
        assert plan.unchanged == ["a.py"]
        assert plan.modified == []
        assert plan.added == []
        assert plan.file_abstracts == {"a.py": "old summary"}

    def test_intersection_different_md5_is_modified(self) -> None:
        plan = build_diff_plan(
            new={"a.py": _n("m2")},
            target_files={"a.py": _f()},
            target_vectors={"a.py": _v("m1")},
        )
        assert plan.modified == ["a.py"]
        assert plan.unchanged == []

    def test_file_and_index_present_new_absent_is_deleted(self) -> None:
        plan = build_diff_plan(
            new={},
            target_files={"a.py": _f()},
            target_vectors={"a.py": _v("m1")},
        )
        assert plan.deleted == ["a.py"]

    def test_file_present_index_absent_new_present_is_repair(self) -> None:
        plan = build_diff_plan(
            new={"a.py": _n("m1")},
            target_files={"a.py": _f()},
            target_vectors={},
        )
        assert plan.repair == ["a.py"]

    def test_file_present_index_absent_new_absent_is_deleted(self) -> None:
        plan = build_diff_plan(
            new={},
            target_files={"a.py": _f()},
            target_vectors={},
        )
        assert plan.deleted == ["a.py"]

    def test_index_present_file_absent_new_present_is_added_and_orphan(self) -> None:
        plan = build_diff_plan(
            new={"a.py": _n("m1")},
            target_files={},
            target_vectors={"a.py": _v("m1")},
        )
        assert plan.added == ["a.py"]
        assert plan.orphan_vectors == ["a.py"]

    def test_index_present_file_absent_new_absent_is_orphan(self) -> None:
        plan = build_diff_plan(
            new={},
            target_files={},
            target_vectors={"a.py": _v("m1")},
        )
        assert plan.orphan_vectors == ["a.py"]
        assert plan.deleted == []

    def test_chunk_named_vector_without_exact_file_is_orphan(self) -> None:
        plan = build_diff_plan(
            new={"a.md": _n("m1")},
            target_files={"a.md": _f()},
            target_vectors={
                "a.md": _v("m1"),
                "a.md#chunk_0001": _v("chunk"),
                "ghost.md#chunk_0001": _v("chunk"),
            },
        )

        assert plan.unchanged == ["a.md"]
        assert plan.orphan_vectors == ["a.md#chunk_0001", "ghost.md#chunk_0001"]

    def test_real_filename_with_chunk_suffix_uses_normal_diff_rules(self) -> None:
        name = "notes#chunk_0001"
        plan = build_diff_plan(
            new={name: _n("m1")},
            target_files={name: _f()},
            target_vectors={name: _v("m1")},
        )

        assert plan.unchanged == [name]
        assert plan.orphan_vectors == []

    def test_deleted_real_chunk_named_file_is_orphan_even_when_prefix_file_exists(self) -> None:
        plan = build_diff_plan(
            new={"report": _n("m1")},
            target_files={"report": _f()},
            target_vectors={
                "report": _v("m1"),
                "report#chunk_0001": _v("old"),
            },
        )

        assert plan.orphan_vectors == ["report#chunk_0001"]

    def test_new_only_is_added(self) -> None:
        plan = build_diff_plan(
            new={"a.py": _n("m1")},
            target_files={},
            target_vectors={},
        )
        assert plan.added == ["a.py"]


class TestMissingMd5Fallback:
    def test_missing_side_md5_reported_for_body_compare(self) -> None:
        # When either side lacks md5, the pair cannot be judged equal by hash;
        # it is surfaced as needs_body_compare rather than silently unchanged.
        plan = build_diff_plan(
            new={"a.py": _n("")},
            target_files={"a.py": _f()},
            target_vectors={"a.py": _v("m1")},
        )
        assert "a.py" in plan.needs_body_compare
        assert plan.unchanged == []

    def test_target_missing_md5_reported_for_body_compare(self) -> None:
        plan = build_diff_plan(
            new={"a.py": _n("m1")},
            target_files={"a.py": _f()},
            target_vectors={"a.py": _v("")},
        )
        assert "a.py" in plan.needs_body_compare


class TestTypeConflicts:
    def test_file_becomes_dir_is_structural(self) -> None:
        plan = build_diff_plan(
            new={"a": _n("", is_dir=True)},
            target_files={"a": _f(is_dir=False)},
            target_vectors={"a": _v("m1")},
        )
        assert "a" in plan.structural
        assert plan.unchanged == []
        assert plan.modified == []

    def test_dir_becomes_file_is_structural(self) -> None:
        plan = build_diff_plan(
            new={"a": _n("m1", is_dir=False)},
            target_files={"a": _f(is_dir=True)},
            target_vectors={},
        )
        assert "a" in plan.structural
        assert plan.added == ["a"]


class TestControlFileExclusion:
    def test_sidecars_never_enter_business_diff(self) -> None:
        plan = build_diff_plan(
            new={"a.py": _n("m1")},
            target_files={
                "a.py": _f(),
                ".abstract.md": _f(),
                ".overview.md": _f(),
                "sub/.abstract.md": _f(),
            },
            target_vectors={"a.py": _v("m1")},
        )
        # Control sidecars must not be classified as deleted business files.
        assert plan.deleted == []
        assert plan.unchanged == ["a.py"]

    def test_vector_only_control_path_is_cleaned_as_orphan(self) -> None:
        plan = build_diff_plan(
            new={},
            target_files={},
            target_vectors={".abstract.md": _v("invalid-l2")},
        )

        assert plan.orphan_vectors == [".abstract.md"]

    def test_l2_record_for_directory_path_is_cleaned_as_orphan(self) -> None:
        plan = build_diff_plan(
            new={"sub": _n("", is_dir=True)},
            target_files={"sub": _f(is_dir=True)},
            target_vectors={"sub": _v("invalid-l2")},
        )

        assert plan.orphan_vectors == ["sub"]


class TestCompletenessGate:
    def test_incomplete_target_files_forbids_deletion(self) -> None:
        # A truncated / untrusted F snapshot must never yield deletions: a file
        # we simply failed to read is not "removed".
        with pytest.raises(ValueError, match="incomplete"):
            build_diff_plan(
                new={},
                target_files={"a.py": _f()},
                target_vectors={"a.py": _v("m1")},
                target_files_complete=False,
            )

    def test_incomplete_target_vectors_forbids_orphan_delete(self) -> None:
        with pytest.raises(ValueError, match="incomplete"):
            build_diff_plan(
                new={},
                target_files={},
                target_vectors={"a.py": _v("m1")},
                target_vectors_complete=False,
            )

    def test_incomplete_target_files_forbids_orphan_vector_delete(self) -> None:
        with pytest.raises(ValueError, match="incomplete target file"):
            build_diff_plan(
                new={},
                target_files={},
                target_vectors={"hidden.py": _v("m1")},
                target_files_complete=False,
            )

    def test_incomplete_snapshot_still_allows_pure_additions(self) -> None:
        # Adding never depends on completeness, so an incomplete target may still
        # add as long as no deletion/orphan cleanup is implied.
        plan = build_diff_plan(
            new={"a.py": _n("m1")},
            target_files={},
            target_vectors={},
            target_files_complete=False,
            target_vectors_complete=False,
        )
        assert plan.added == ["a.py"]
