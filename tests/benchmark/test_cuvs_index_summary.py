import json

import pytest

from benchmark.cuvs import summarize_index_runs as summary_module


def result_document(*, qps, p50, build, dataset_name="public", itopk=512):
    return {
        "format_version": 2,
        "runtime": {
            "timestamp": f"run-{qps}",
            "git_revision": "abc123",
            "git_dirty": False,
            "gpu": {"name": "test-gpu"},
            "cpu_model": "test-cpu",
            "cuvs": "1.0",
            "cupy": "2.0",
        },
        "dataset": {
            "kind": "ann-benchmarks-hdf5",
            "name": dataset_name,
            "source_sha256": "deadbeef",
            "vector_count": 100,
            "dimension": 8,
            "query_count": 10,
            "metric": "cosine",
            "seed": None,
        },
        "parameters": {"k": 10, "query_batch_size": 1},
        "results": [
            {
                "backend": "cuvs_cagra",
                "cagra_search_params": {"itopk_size": itopk},
                "build_seconds": build,
                "first_search_per_query_ms": p50 * 10,
                "recall_at_k": 0.99,
                "rss_delta_bytes": 1000,
                "gpu_used_delta_bytes": 2000,
                "search": {
                    "qps": qps,
                    "per_query_latency_ms": {"p50": p50, "p95": p50 * 2, "p99": p50 * 3},
                },
            }
        ],
    }


def write_result(path, document):
    path.write_text(json.dumps(document))
    return path


def test_summarize_files_reports_process_median_and_mad(tmp_path):
    paths = [
        write_result(tmp_path / "run-1.json", result_document(qps=100, p50=1.0, build=3.0)),
        write_result(tmp_path / "run-2.json", result_document(qps=110, p50=1.2, build=2.0)),
        write_result(tmp_path / "run-3.json", result_document(qps=300, p50=1.1, build=4.0)),
    ]

    for path in paths:
        document = json.loads(path.read_text())
        variant = document["results"][0]
        document["results"].insert(0, {**variant, "cagra_search_params": {"itopk_size": 2048}})
        write_result(path, document)
    summary = summary_module.summarize_files(paths)

    assert summary["run_count"] == 3
    assert summary["runs"][0]["source"] == "run-1.json"
    assert str(tmp_path) not in json.dumps(summary)
    assert [row["cagra_search_params"]["itopk_size"] for row in summary["results"]] == [512, 2048]
    result = summary["results"][0]
    assert result["cagra_search_params"] == {"itopk_size": 512}
    assert result["metrics"]["qps"]["median"] == 110
    assert result["metrics"]["qps"]["mad"] == 10
    assert result["metrics"]["warm_p50_ms"]["median"] == pytest.approx(1.1)
    assert result["metrics"]["warm_p50_ms"]["mad"] == pytest.approx(0.1)


def test_summarize_files_rejects_incompatible_or_repeated_runs(tmp_path):
    paths = [
        write_result(tmp_path / "run-1.json", result_document(qps=100, p50=1.0, build=3.0)),
        write_result(
            tmp_path / "run-2.json",
            result_document(qps=110, p50=1.2, build=2.0, dataset_name="different"),
        ),
    ]

    with pytest.raises(ValueError, match="Dataset metadata differs"):
        summary_module.summarize_files(paths)
    write_result(paths[1], result_document(qps=110, p50=1.2, build=2.0, itopk=2048))
    with pytest.raises(ValueError, match="Backend/search variants differ"):
        summary_module.summarize_files(paths)

    paths[1].write_bytes(paths[0].read_bytes())
    with pytest.raises(ValueError, match="Duplicate process timestamp"):
        summary_module.summarize_files(paths)
