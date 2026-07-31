import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "benchmark" / "cuvs" / "run_collection_benchmark.py"
)
SPEC = importlib.util.spec_from_file_location("run_cuvs_collection_benchmark", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def test_filter_scenarios_cover_scalar_and_path_selectivity():
    scenarios = benchmark.filter_scenarios()

    assert [scenario.name for scenario in scenarios] == [
        "unfiltered",
        "uniform_10pct",
        "uniform_1pct",
        "uniform_0_1pct",
        "clustered_10pct",
        "clustered_1pct",
        "clustered_0_1pct",
        "path_10pct",
        "path_1pct",
        "path_0_1pct",
    ]
    assert [scenario.selectivity for scenario in scenarios] == [
        1.0,
        0.1,
        0.01,
        0.001,
        0.1,
        0.01,
        0.001,
        0.1,
        0.01,
        0.001,
    ]
    assert [scenario.distribution for scenario in scenarios[-3:]] == ["path"] * 3
    assert [scenario.filter["conds"][0] for scenario in scenarios[-3:]] == [
        "/docs/g0",
        "/docs/g0/h0",
        "/docs/g0/h0/i0",
    ]
    assert all(scenario.filter["para"] == "-d=-1" for scenario in scenarios[-3:])


def test_scalar_fields_have_expected_uniform_and_clustered_counts():
    vector_count = 10_000
    values = [benchmark.scalar_fields(index, vector_count) for index in range(vector_count)]

    assert sum(uniform < 10 for uniform, _ in values) == 100
    assert sum(clustered < 10 for _, clustered in values) == 100
    assert [clustered for _, clustered in values[:10]] == [0] * 10
    assert [uniform for uniform, _ in values[:10]] == list(range(10))


def test_prebuild_selective_scenario_respects_native_threshold():
    scenario = benchmark.prebuild_selective_scenario(100_000, 2_000)

    assert scenario is not None
    assert scenario.name == "prebuild_selective_0_1pct"
    assert scenario.filter == {
        "op": "must",
        "field": "uniform_bucket",
        "conds": [0],
    }
    assert scenario.selectivity == 0.001
    assert benchmark.prebuild_selective_scenario(100_000, 99) is None
    assert benchmark.prebuild_selective_scenario(100_000, 0) is None


def test_recall_at_k_handles_short_filtered_results():
    actual = [[3, 2], [], [9, 1, 4]]
    expected = [[2, 3], [], [9, 8, 7]]

    assert benchmark.recall_at_k(actual, expected, 3) == pytest.approx((1.0 + 1.0 + 1 / 3) / 3)


def test_run_search_scenario_times_adapter_path_and_preserves_ids():
    class FakeAdapter:
        def __init__(self):
            self.calls = []

        def query(self, *, query_vector, filter, limit, output_fields):
            self.calls.append((query_vector, filter, limit, output_fields))
            return [{"id": int(query_vector[0]) + 1}]

    adapter = FakeAdapter()
    queries = np.asarray([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32)
    scenario = benchmark.FilterScenario(
        "filtered",
        {"op": "range", "field": "uniform_bucket", "lt": 10},
        "uniform",
        0.01,
    )

    result = benchmark.run_search_scenario(
        adapter,
        queries,
        scenario=scenario,
        k=10,
        warmup_queries=2,
    )

    assert result["neighbors"] == [[1], [2]]
    assert result["search"]["query_count"] == 2
    assert result["search"]["qps"] > 0
    assert len(adapter.calls) == 5
    assert all(call[1] == scenario.filter for call in adapter.calls)


def test_run_single_query_scenario_records_latency_and_result_count(monkeypatch):
    class FakeAdapter:
        def query(self, *, query_vector, filter, limit, output_fields):
            assert query_vector == [1.0, 0.0]
            assert filter["conds"] == [0]
            assert limit == 10
            assert output_fields == ["id"]
            return [{"id": 1}]

    gpu_samples = iter([100, 104])
    monkeypatch.setattr(benchmark, "gpu_memory_used_bytes", lambda: next(gpu_samples))
    scenario = benchmark.prebuild_selective_scenario(100_000, 2_000)
    assert scenario is not None

    result = benchmark.run_single_query_scenario(
        FakeAdapter(),
        [1.0, 0.0],
        scenario=scenario,
        k=10,
    )

    assert result["name"] == "prebuild_selective_0_1pct"
    assert result["result_count"] == 1
    assert result["latency_ms"] >= 0
    assert result["gpu_delta_bytes"] == 4
