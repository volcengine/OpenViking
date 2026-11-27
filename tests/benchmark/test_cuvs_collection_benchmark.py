import json
import sys

import pytest

from benchmark.cuvs import run_collection_benchmark as benchmark


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


def test_recall_at_k_handles_short_filtered_results():
    actual = [[3, 2], [], [9, 1, 4]]
    expected = [[2, 3], [], [9, 8, 7]]

    assert benchmark.recall_at_k(actual, expected, 3) == pytest.approx((1.0 + 1.0 + 1 / 3) / 3)


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
    assert benchmark.prebuild_selective_scenario(100_000, 99) is None
    assert benchmark.prebuild_selective_scenario(100_000, 0) is None

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
