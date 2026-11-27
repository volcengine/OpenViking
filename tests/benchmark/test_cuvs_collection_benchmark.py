import importlib.util
import json
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


def test_collection_cli_reports_search_write_and_restart(tmp_path, monkeypatch, capsys):
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_collection_benchmark.py",
            "--backends",
            "native",
            "--vector-count",
            "1000",
            "--dimension",
            "4",
            "--query-count",
            "2",
            "--k",
            "128",
            "--warmup-queries",
            "1",
            "--mutation-sizes",
            "1",
            "--delete-count",
            "1",
            "--data-root",
            str(tmp_path),
            "--output",
            str(output),
        ],
    )
    benchmark.main()

    [native] = json.loads(output.read_text())["results"]
    searches = {row["name"]: row for row in native["searches"]}
    counts = {"unfiltered": 128}
    for distribution in ("uniform", "clustered", "path"):
        counts.update(
            {
                f"{distribution}_10pct": 100,
                f"{distribution}_1pct": 10,
                f"{distribution}_0_1pct": 1,
            }
        )
    assert {name: row["first_result_count"] for name, row in searches.items()} == counts
    for row in searches.values():
        assert row["search"]["query_count"] == 2
        assert "recall_at_k" not in row
    recall_columns = {}
    for line in capsys.readouterr().out.splitlines():
        columns = line.split()
        if len(columns) == 6 and columns[0] == "native" and columns[1] in searches:
            recall_columns[columns[1]] = columns[-1]
    assert recall_columns == dict.fromkeys(searches, "N/A")
    for rate, scope in (
        ("10pct", "/docs/g0"),
        ("1pct", "/docs/g0/h0"),
        ("0_1pct", "/docs/g0/h0/i0"),
    ):
        row = searches[f"path_{rate}"]
        assert row["distribution"] == "path"
        assert row["filter"]["conds"] == [scope]
        assert row["filter"]["para"] == "-d=-1"
    [update] = native["lifecycle"]["updates"]
    assert update["count"] == 1
    assert native["lifecycle"]["delete"]["count"] == 1
    assert native["lifecycle"]["restart"]["first_result_count"] == 128


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
