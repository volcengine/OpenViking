# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.storage.resource_rnfv import (
    BASE_VECTOR_PROJECTION,
    FormalTreeSnapshot,
    NewArtifactSnapshot,
    RequestIntent,
    RNFVSnapshot,
    VectorIndexSnapshot,
    VectorRecordSnapshot,
)
from openviking.storage.viking_fs._diff_plan import (
    NewEntry,
    TargetFile,
    apply_request_scalar_intents,
    build_diff_plan,
)
from openviking.utils.ingest_options import IngestOptions


def _snapshot(*, tags, requested_tags, mode="replace", md5="same") -> RNFVSnapshot:
    root = "viking://resources/repo"
    request = RequestIntent.from_ingest_options(
        target_uri=root,
        processing_mode="semantic_and_vectors",
        ingest_options=IngestOptions(search_tags=requested_tags, search_tag_mode=mode),
    )
    record = VectorRecordSnapshot(
        record_id="a-l2",
        uri=f"{root}/a.py",
        relative_path="a.py",
        level=2,
        fields={"md5": md5, "search_tags": tags},
    )
    return RNFVSnapshot(
        request=request,
        new=NewArtifactSnapshot(entries={"a.py": NewEntry(md5="same")}),
        formal=FormalTreeSnapshot(entries={"a.py": TargetFile()}),
        vectors=VectorIndexSnapshot(
            records_by_id={record.record_id: record},
            record_ids_by_key={("a.py", 2): (record.record_id,)},
            projected_fields=request.required_vector_fields(),
        ),
    )


def test_request_without_tags_keeps_minimal_vector_projection() -> None:
    request = RequestIntent.from_ingest_options(
        target_uri="viking://resources/repo",
        processing_mode="semantic_and_vectors",
        ingest_options=IngestOptions(),
    )

    assert request.scalar_intents == ()
    assert request.required_vector_fields() == BASE_VECTOR_PROJECTION


def test_tags_request_adds_search_tags_to_vector_projection() -> None:
    request = RequestIntent.from_ingest_options(
        target_uri="viking://resources/repo",
        processing_mode="semantic_and_vectors",
        ingest_options=IngestOptions(search_tags=["team=search"], search_tag_mode="append"),
    )

    assert request.required_vector_fields() == BASE_VECTOR_PROJECTION | {"search_tags"}
    assert request.scalar_intents[0].target_levels == {0, 1, 2}


def test_vectors_only_tags_target_l2_records_only() -> None:
    request = RequestIntent.from_ingest_options(
        target_uri="viking://resources/repo",
        processing_mode="vectors_only",
        ingest_options=IngestOptions(search_tags=["team=search"], search_tag_mode="append"),
    )

    assert request.scalar_intents[0].target_levels == {2}


def test_unchanged_content_with_replace_tags_produces_scalar_update() -> None:
    plan = build_diff_plan(
        _snapshot(tags=["env=test"], requested_tags=["team=search"])
    )

    assert plan.unchanged == ["a.py"]
    assert plan.scalar_updates[0].record_id == "a-l2"
    assert plan.scalar_updates[0].fields == {"search_tags": ["team=search"]}
    assert plan.is_noop() is False


def test_unchanged_content_with_append_tags_merges_by_key() -> None:
    plan = build_diff_plan(
        _snapshot(
            tags=["env=test", "owner=alice"],
            requested_tags=["env=prod", "team=search"],
            mode="append",
        )
    )

    assert plan.scalar_updates[0].fields == {
        "search_tags": ["env=prod", "owner=alice", "team=search"]
    }


def test_explicit_empty_replace_clears_tags() -> None:
    plan = build_diff_plan(_snapshot(tags=["env=test"], requested_tags=[]))

    assert plan.scalar_updates[0].fields == {"search_tags": []}


def test_effectively_equal_tags_are_a_true_noop() -> None:
    plan = build_diff_plan(
        _snapshot(
            tags=["env=test", "team=search"],
            requested_tags=["team=search"],
            mode="append",
        )
    )

    assert plan.scalar_updates == []
    assert plan.is_noop() is True


def test_modified_record_folds_tag_result_into_scalar_override() -> None:
    snapshot = _snapshot(
        tags=["env=test"], requested_tags=["team=search"], md5="old"
    )

    plan = build_diff_plan(snapshot)

    assert plan.modified == ["a.py"]
    assert [update.record_id for update in plan.scalar_updates] == ["a-l2"]
    assert plan.scalar_overrides == {"a-l2": {"search_tags": ["team=search"]}}


def test_modified_record_keeps_scalar_update_when_vectorization_is_disabled() -> None:
    original = _snapshot(
        tags=["env=test"], requested_tags=["team=search"], md5="old"
    )
    snapshot = RNFVSnapshot(
        request=RequestIntent(
            target_uri=original.request.target_uri,
            processing_mode=original.request.processing_mode,
            vectorize=False,
            scalar_intents=original.request.scalar_intents,
        ),
        new=original.new,
        formal=original.formal,
        vectors=original.vectors,
    )

    plan = build_diff_plan(snapshot)

    assert plan.modified == ["a.py"]
    assert [update.record_id for update in plan.scalar_updates] == ["a-l2"]


def test_body_compare_replans_scalar_action_after_content_result() -> None:
    snapshot = _snapshot(
        tags=["env=test"], requested_tags=["team=search"], md5=""
    )
    plan = build_diff_plan(snapshot)
    assert plan.needs_body_compare == ["a.py"]
    assert plan.scalar_updates

    plan.needs_body_compare = []
    plan.modified = ["a.py"]
    apply_request_scalar_intents(snapshot, plan)

    assert [update.record_id for update in plan.scalar_updates] == ["a-l2"]
    assert plan.scalar_overrides == {"a-l2": {"search_tags": ["team=search"]}}


def test_changed_tree_keeps_directory_scalar_update_if_summary_stays_equal() -> None:
    root = "viking://resources/repo"
    request = RequestIntent.from_ingest_options(
        target_uri=root,
        processing_mode="semantic_and_vectors",
        ingest_options=IngestOptions(search_tags=["team=search"]),
    )
    root_record = VectorRecordSnapshot(
        record_id="root-l0",
        uri=root,
        relative_path="",
        level=0,
        fields={"md5": "", "search_tags": ["env=test"]},
    )
    file_record = VectorRecordSnapshot(
        record_id="a-l2",
        uri=f"{root}/a.py",
        relative_path="a.py",
        level=2,
        fields={"md5": "old", "search_tags": ["env=test"]},
    )
    snapshot = RNFVSnapshot(
        request=request,
        new=NewArtifactSnapshot(entries={"a.py": NewEntry(md5="new")}),
        formal=FormalTreeSnapshot(entries={"a.py": TargetFile()}),
        vectors=VectorIndexSnapshot(
            records_by_id={"root-l0": root_record, "a-l2": file_record},
            record_ids_by_key={("", 0): ("root-l0",), ("a.py", 2): ("a-l2",)},
            projected_fields=request.required_vector_fields(),
        ),
    )

    plan = build_diff_plan(snapshot)

    assert [update.record_id for update in plan.scalar_updates] == ["root-l0", "a-l2"]
    assert plan.scalar_overrides == {
        "root-l0": {"search_tags": ["team=search"]},
        "a-l2": {"search_tags": ["team=search"]},
    }


def test_rnfv_validation_rejects_missing_request_projection() -> None:
    snapshot = _snapshot(tags=["env=test"], requested_tags=["team=search"])
    invalid = RNFVSnapshot(
        request=snapshot.request,
        new=snapshot.new,
        formal=snapshot.formal,
        vectors=VectorIndexSnapshot(
            records_by_id=snapshot.vectors.records_by_id,
            record_ids_by_key=snapshot.vectors.record_ids_by_key,
            projected_fields=BASE_VECTOR_PROJECTION,
        ),
    )

    try:
        build_diff_plan(invalid)
    except ValueError as exc:
        assert "search_tags" in str(exc)
    else:
        raise AssertionError("missing R-required projection must fail closed")
