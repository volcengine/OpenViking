# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.vectordb.index.cuvs_index import (
    CuVSDenseIndex,
    CuVSMemoryBudgetError,
    CuVSNativeRouteError,
    CuVSUnavailableError,
    UnsupportedCuVSFilterError,
    estimate_cuvs_memory,
    matches_filter,
)
from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord


class FakeCuVSRuntime:
    """CPU implementation of the tiny runtime boundary used by unit tests."""

    def __init__(self, metric="inner_product"):
        self.metric = metric
        self.dataset = []
        self.build_count = 0
        self.prepare_filter_count = 0
        self.closed = False
        self.free_memory_bytes = 1 << 60
        self.total_memory_bytes = 1 << 60
        self.release_count = 0

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

    def prepare_filter(self, mask):
        self.prepare_filter_count += 1
        return tuple(mask)

    def memory_info(self):
        return self.free_memory_bytes, self.total_memory_bytes

    def release_index(self):
        self.dataset = []
        self.release_count += 1

    @staticmethod
    def is_out_of_memory(_exc):
        return False

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


def test_cuvs_memory_estimate_accounts_for_fp32_graphs_and_filter_cache():
    estimate = estimate_cuvs_memory(
        vector_count=1_000_000,
        dimension=768,
        algorithm="cagra",
        build_params={"graph_degree": 64, "intermediate_graph_degree": 128},
        filter_cache_size=16,
        safety_factor=2.0,
    )

    assert estimate.vector_bytes == 1_000_000 * 768 * 4
    assert estimate.graph_bytes == 1_000_000 * 64 * 4
    assert estimate.build_graph_bytes == 1_000_000 * 128 * 4
    assert estimate.filter_cache_bytes == ((1_000_000 + 31) // 32) * 4 * 16
    assert estimate.estimated_peak_bytes == 2 * (
        estimate.vector_bytes
        + estimate.graph_bytes
        + estimate.build_graph_bytes
        + estimate.filter_cache_bytes
    )


def test_auto_cuvs_retries_after_gpu_memory_becomes_available():
    runtime = FakeCuVSRuntime()
    runtime.free_memory_bytes = 15
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={
            "algorithm": "brute_force",
            "filter_cache_size": 0,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(1, [1.0, 0.0]), candidate(2, [0.0, 1.0])])

    with pytest.raises(CuVSMemoryBudgetError, match="estimated GPU peak"):
        index.search([1.0, 0.0], 1, None)
    assert runtime.build_count == 0

    runtime.free_memory_bytes = 16
    assert index.search([1.0, 0.0], 1, None)[0] == [1]
    assert runtime.build_count == 1
    assert runtime.release_count == 2


def test_auto_cuvs_converts_gpu_allocation_failure_to_native_fallback_signal():
    class OutOfMemoryRuntime(FakeCuVSRuntime):
        def build(self, _dataset):
            raise RuntimeError("out of memory")

        @staticmethod
        def is_out_of_memory(_exc):
            return True

    runtime = OutOfMemoryRuntime()
    index = CuVSDenseIndex(
        dimension=4,
        distance="ip",
        normalize_vectors=False,
        field_types={},
        config={
            "filter_cache_size": 0,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates([candidate(1, [1.0, 0.0, 0.0, 0.0])])

    with pytest.raises(CuVSMemoryBudgetError, match="allocation failure"):
        index.search([1.0, 0.0, 0.0, 0.0], 1, None)
    assert runtime.release_count == 2


def test_filter_cache_reuses_prepared_mask_and_invalidates_on_mutation():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 2},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(1, [1.0, 0.0], account_id="a"),
            candidate(2, [0.0, 1.0], account_id="b"),
        ]
    )
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}

    assert index.search([1.0, 0.0], 1, filter_a)[0] == [1]
    assert index.search([1.0, 0.0], 1, filter_a)[0] == [1]
    assert runtime.prepare_filter_count == 1

    index.upsert([delta(2, [0.0, 1.0], account_id="a")])
    assert index.search([0.0, 1.0], 1, filter_a)[0] == [2]
    assert runtime.prepare_filter_count == 2

    index.delete([DeltaRecord(label=1)])
    assert index.search([1.0, 0.0], 2, filter_a)[0] == [2]
    assert runtime.prepare_filter_count == 3


def test_native_filter_resolver_projects_bitset_in_cuvs_row_order():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 2},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(30, [0.0, 1.0], account_id="b"),
            candidate(10, [1.0, 0.0], account_id="a"),
            candidate(20, [0.5, 0.5], account_id="a"),
        ]
    )
    calls = []

    def register(ordered_labels):
        calls.append(("register", list(ordered_labels)))

    def resolve(filters):
        calls.append(("resolve", filters))
        # Rows 1 and 2 are eligible in the cuVS dataset order above.
        return [0b110], 2

    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    assert index.search([1.0, 0.0], 3, filter_a, resolve, register)[0] == [10, 20]
    assert index.search([1.0, 0.0], 3, filter_a, resolve, register)[0] == [10, 20]
    assert calls == [("register", [30, 10, 20]), ("resolve", filter_a)]
    # Native packed words bypass the Python predicate/mask packer.
    assert runtime.prepare_filter_count == 0


def test_auto_mode_caches_native_route_for_selective_filter():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={
            "auto_filter_native_threshold": 1,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], account_id="a"),
            candidate(20, [0.0, 1.0], account_id="b"),
        ]
    )
    calls = []

    def register(ordered_labels):
        calls.append(("register", list(ordered_labels)))

    def resolve(filters):
        calls.append(("resolve", filters))
        return [0b01], 1

    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    for _ in range(2):
        with pytest.raises(CuVSNativeRouteError, match="1 candidates"):
            index.search([1.0, 0.0], 1, filter_a, resolve, register)

    assert calls == [("register", [10, 20]), ("resolve", filter_a)]
    assert runtime.prepare_filter_count == 0


def test_auto_mode_uses_lower_native_threshold_for_path_filters():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"uri": "path"},
        config={
            "auto_filter_native_threshold": 2,
            "auto_path_filter_native_threshold": 0,
            "auto_memory_reserve_mb": 0,
            "auto_memory_safety_factor": 1.0,
        },
        runtime=runtime,
        auto_memory=True,
    )
    index.add_candidates(
        [
            candidate(10, [1.0, 0.0], uri="/docs/one"),
            candidate(20, [0.0, 1.0], uri="/other/two"),
        ]
    )

    def resolve(_filters):
        return [0b01], 1

    path_filter = {
        "op": "must",
        "field": "uri",
        "conds": ["/docs"],
        "para": "-d=-1",
    }
    assert index.search([1.0, 0.0], 1, path_filter, resolve, lambda _labels: None)[0] == [10]


def test_filter_cache_uses_lru_bound():
    runtime = FakeCuVSRuntime()
    index = CuVSDenseIndex(
        dimension=2,
        distance="ip",
        normalize_vectors=False,
        field_types={"account_id": "string"},
        config={"filter_cache_size": 1},
        runtime=runtime,
    )
    index.add_candidates(
        [
            candidate(1, [1.0, 0.0], account_id="a"),
            candidate(2, [0.0, 1.0], account_id="b"),
        ]
    )
    filter_a = {"op": "must", "field": "account_id", "conds": ["a"]}
    filter_b = {"op": "must", "field": "account_id", "conds": ["b"]}

    index.search([1.0, 0.0], 1, filter_a)
    index.search([0.0, 1.0], 1, filter_b)
    index.search([1.0, 0.0], 1, filter_a)

    assert runtime.prepare_filter_count == 3


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
