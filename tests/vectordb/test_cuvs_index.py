# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.vectordb.index.cuvs_index import (
    CuVSDenseIndex,
    CuVSUnavailableError,
    UnsupportedCuVSFilterError,
    matches_filter,
)
from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord


class FakeCuVSRuntime:
    """CPU implementation of the tiny runtime boundary used by unit tests."""

    def __init__(self, metric="inner_product"):
        self.metric = metric
        self.dataset = []
        self.build_count = 0
        self.closed = False

    def build(self, dataset):
        self.dataset = [list(vector) for vector in dataset]
        self.build_count += 1
        return self.dataset

    def search(self, index, query, limit, mask):
        rows = []
        for offset, vector in enumerate(index):
            if mask is not None and not mask[offset]:
                continue
            if self.metric == "sqeuclidean":
                distance = sum(
                    (left - right) ** 2 for left, right in zip(query, vector, strict=True)
                )
                sort_key = distance
            else:
                distance = sum(left * right for left, right in zip(query, vector, strict=True))
                sort_key = -distance
            rows.append((sort_key, offset, distance))
        rows.sort()
        selected = rows[:limit]
        return [row[1] for row in selected], [row[2] for row in selected]

    def close(self):
        self.closed = True


def candidate(label, vector, **fields):
    import json

    return CandidateData(label=label, vector=vector, fields=json.dumps(fields))


def delta(label, vector, **fields):
    import json

    return DeltaRecord(label=label, vector=vector, fields=json.dumps(fields))


def test_cuvs_dense_search_handles_filter_upsert_delete_and_lazy_rebuild():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=True,
        field_types={"account_id": "string", "uri": "path"},
        config={"algorithm": "brute_force"},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], account_id="a", uri="/docs/one"),
            candidate(20, [0.8, 0.2], account_id="a", uri="/docs/deep/two"),
            candidate(30, [0.0, 1.0], account_id="b", uri="/other/three"),
        ]
    )

    labels, scores = index.search(
        [10.0, 0.0],
        10,
        {
            "op": "and",
            "conds": [
                {"op": "must", "field": "account_id", "conds": ["a"]},
                {"op": "must", "field": "uri", "conds": ["/docs"], "para": "-d=1"},
            ],
        },
    )
    assert labels == [10]
    assert scores == [1.0]
    assert runtime.build_count == 1

    # Repeated reads reuse the GPU index; a mutation invalidates it exactly once.
    assert index.search([1.0, 0.0], 1, None)[0] == [10]
    assert runtime.build_count == 1
    index.upsert([delta(30, [2.0, 0.0], account_id="a", uri="/docs/three")])
    assert index.search([1.0, 0.0], 3, None)[0] == [10, 30, 20]
    assert runtime.build_count == 2
    index.delete([DeltaRecord(label=10)])
    assert index.search([1.0, 0.0], 3, None)[0] == [30, 20]
    assert runtime.build_count == 3


def test_cuvs_l2_scores_match_openviking_score_convention():
    runtime = FakeCuVSRuntime(metric="sqeuclidean")
    index = CuVSDenseIndex(
        dimension=2,
        distance="l2",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=runtime,
    )
    index.add_candidates([candidate(1, [0.0, 0.0]), candidate(2, [2.0, 0.0])])

    labels, scores = index.search([1.0, 0.0], 2, None)
    assert labels == [1, 2]
    assert scores == [0.0, 0.0]  # OpenViking exposes 1 - squared-L2.


def test_filter_evaluator_covers_lists_ranges_contains_and_path_depth():
    fields = {
        "tags": ["a", "b"],
        "count": 7,
        "title": "cuVS integration",
        "uri": "docs/deep/item",
    }
    field_types = {
        "tags": "list<string>",
        "count": "int64",
        "title": "string",
        "uri": "path",
    }
    node = {
        "op": "and",
        "conds": [
            {"op": "must", "field": "tags", "conds": ["b"]},
            {"op": "range", "field": "count", "gte": 5, "lt": 10},
            {"op": "contains", "field": "title", "substring": "cuVS"},
            {"op": "must", "field": "uri", "conds": ["/docs"], "para": "-d=2"},
        ],
    }
    assert matches_filter(fields, node, field_types)
    node["conds"][-1]["para"] = "-d=1"
    assert not matches_filter(fields, node, field_types)


def test_filter_evaluator_rejects_type_sensitive_filters_for_native_fallback():
    with pytest.raises(UnsupportedCuVSFilterError):
        matches_filter(
            {"created_at": "2026-07-02T00:00:00Z"},
            {"op": "range", "field": "created_at", "gte": "2026-07-01T00:00:00Z"},
            {"created_at": "date_time"},
        )


def test_dimension_mismatch_is_reported_before_runtime_call():
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={},
        runtime=FakeCuVSRuntime(),
    )
    with pytest.raises(ValueError, match="dimension mismatch"):
        index.add_candidates([candidate(1, [1.0, 2.0, 3.0])])


def test_missing_cuvs_runtime_has_actionable_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def import_without_cupy(name, *args, **kwargs):
        if name == "cupy":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_cupy)
    with pytest.raises(CuVSUnavailableError, match="cuvs-cu12 or cuvs-cu13"):
        CuVSDenseIndex(
            dimension=2,
            distance="ip",
            normalize_vectors=False,
            field_types={},
            config={},
        )
