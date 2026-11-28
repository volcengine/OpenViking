import json

import pytest

from benchmark.cuvs import summarize_service_runs as summary_module


def result_document(*, qps, p50, timestamp="run-1", dimension=8):
    return {
        "format_version": 1,
        "runtime": {
            "timestamp": timestamp,
            "git_revision": "abc123",
            "git_dirty": False,
            "gpu": {"name": "test-gpu"},
            "cpu_model": "test-cpu",
            "cuvs": "1.0",
            "cupy": "2.0",
        },
        "dataset": {
            "kind": "random",
            "vector_count": 100,
            "dimension": dimension,
            "query_count": 10,
            "metric": "cosine",
            "seed": 42,
        },
        "parameters": {"k": 10, "concurrency": [4]},
        "results": [
            {
                "backend": "cuvs_brute_force",
                "ingest": {"total_seconds": 2.0, "records_per_second": 50.0},
                "scenarios": [
                    {
                        "name": "tenant_cached",
                        "filter_mode": "cached",
                        "filter": None,
                        "initial_filter_query_ms": 20.0,
                        "results": [
                            {
                                "concurrency": 4,
                                "wall_seconds": 1.0,
                                "qps": qps,
                                "error_count": 0,
                                "latency_ms": {
                                    "p50": p50,
                                    "p95": p50 * 2,
                                    "p99": p50 * 3,
                                    "max": p50 * 4,
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def write_result(path, document):
    path.write_text(json.dumps(document))
    return path


def test_summarize_service_runs_reports_process_median_and_mad(tmp_path):
    paths = [
        write_result(tmp_path / "run-1.json", result_document(qps=100, p50=1.0, timestamp="run-1")),
        write_result(tmp_path / "run-2.json", result_document(qps=110, p50=1.2, timestamp="run-2")),
        write_result(tmp_path / "run-3.json", result_document(qps=300, p50=1.1, timestamp="run-3")),
    ]

    summary = summary_module.summarize_files(paths)

    assert summary["run_count"] == 3
    assert str(tmp_path) not in json.dumps(summary)
    result = summary["results"][0]["scenarios"][0]["results"][0]
    assert result["metrics"]["qps"]["median"] == 110
    assert result["metrics"]["qps"]["mad"] == 10
    assert result["metrics"]["p50_ms"]["median"] == pytest.approx(1.1)


def test_summarize_service_runs_rejects_incompatible_or_repeated_runs(tmp_path):
    paths = [
        write_result(tmp_path / "run-1.json", result_document(qps=100, p50=1.0, timestamp="run-1")),
        write_result(
            tmp_path / "run-2.json",
            result_document(qps=110, p50=1.2, timestamp="run-2", dimension=16),
        ),
    ]

    with pytest.raises(ValueError, match="Dataset metadata differs"):
        summary_module.summarize_files(paths)
    paths[1].write_bytes(paths[0].read_bytes())
    with pytest.raises(ValueError, match="Duplicate process timestamp"):
        summary_module.summarize_files(paths)
