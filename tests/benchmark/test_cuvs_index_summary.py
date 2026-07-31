import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "benchmark" / "cuvs" / "summarize_index_runs.py"
SPEC = importlib.util.spec_from_file_location("summarize_cuvs_index_runs", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
summary_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = summary_module
SPEC.loader.exec_module(summary_module)


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

    summary = summary_module.summarize_files(paths)

    assert summary["run_count"] == 3
    assert summary["runs"][0]["source"] == "run-1.json"
    assert str(tmp_path) not in json.dumps(summary)
    result = summary["results"][0]
    assert result["cagra_search_params"] == {"itopk_size": 512}
    assert result["metrics"]["qps"]["median"] == 110
    assert result["metrics"]["qps"]["mad"] == 10
    assert result["metrics"]["warm_p50_ms"]["median"] == pytest.approx(1.1)
    assert result["metrics"]["warm_p50_ms"]["mad"] == pytest.approx(0.1)


def test_variant_sort_key_orders_backends_and_numeric_itopk():
    keys = [
        ("cuvs_cagra", summary_module.canonical({"itopk_size": 2048})),
        ("cuvs_cagra_fp16", summary_module.canonical({"itopk_size": 512})),
        ("cuvs_brute_force", summary_module.canonical(None)),
        ("cuvs_brute_force_fp16", summary_module.canonical(None)),
        ("cuvs_cagra", summary_module.canonical({"itopk_size": 512})),
        ("native", summary_module.canonical(None)),
    ]

    assert sorted(keys, key=summary_module.variant_sort_key) == [
        ("native", "null"),
        ("cuvs_brute_force", "null"),
        ("cuvs_brute_force_fp16", "null"),
        ("cuvs_cagra", '{"itopk_size":512}'),
        ("cuvs_cagra", '{"itopk_size":2048}'),
        ("cuvs_cagra_fp16", '{"itopk_size":512}'),
    ]


def test_summarize_files_rejects_mismatched_dataset(tmp_path):
    paths = [
        write_result(tmp_path / "run-1.json", result_document(qps=100, p50=1.0, build=3.0)),
        write_result(
            tmp_path / "run-2.json",
            result_document(qps=110, p50=1.2, build=2.0, dataset_name="different"),
        ),
    ]

    with pytest.raises(ValueError, match="Dataset metadata differs"):
        summary_module.summarize_files(paths)


def test_summarize_files_rejects_mismatched_variants(tmp_path):
    paths = [
        write_result(tmp_path / "run-1.json", result_document(qps=100, p50=1.0, build=3.0)),
        write_result(
            tmp_path / "run-2.json",
            result_document(qps=110, p50=1.2, build=2.0, itopk=2048),
        ),
    ]

    with pytest.raises(ValueError, match="Backend/search variants differ"):
        summary_module.summarize_files(paths)


def test_summarize_files_rejects_repeated_path(tmp_path):
    path = write_result(tmp_path / "run-1.json", result_document(qps=100, p50=1.0, build=3.0))

    with pytest.raises(ValueError, match="cannot be repeated"):
        summary_module.summarize_files([path, path])


def test_summarize_files_rejects_renamed_duplicate_process(tmp_path):
    document = result_document(qps=100, p50=1.0, build=3.0)
    paths = [
        write_result(tmp_path / "run-1.json", document),
        write_result(tmp_path / "renamed-run-1.json", document),
    ]

    with pytest.raises(ValueError, match="Duplicate process timestamp"):
        summary_module.summarize_files(paths)
