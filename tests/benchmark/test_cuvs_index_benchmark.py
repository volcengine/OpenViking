import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "benchmark" / "cuvs" / "run_index_benchmark.py"
SPEC = importlib.util.spec_from_file_location("run_cuvs_index_benchmark", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def test_percentile_interpolates_and_recall_ignores_order():
    assert benchmark.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    actual = np.asarray([[2, 1, 8], [4, 5, 6]])
    expected = np.asarray([[1, 2, 3], [4, 7, 8]])
    assert benchmark.recall_at_k(actual, expected, 3) == pytest.approx(0.5)


def test_prepare_dataset_is_normalized_deterministic_and_reusable(tmp_path):
    arguments = {
        "vector_count": 32,
        "dimension": 8,
        "query_count": 5,
        "metric": "cosine",
        "seed": 7,
        "generation_chunk_size": 11,
        "force": False,
    }
    first = benchmark.prepare_dataset(tmp_path, **arguments)
    dataset = np.load(first.dataset, mmap_mode="r", allow_pickle=False)
    queries = np.load(first.queries, mmap_mode="r", allow_pickle=False)

    assert first.reused is False
    assert dataset.shape == (32, 8)
    assert queries.shape == (5, 8)
    np.testing.assert_allclose(np.linalg.norm(dataset, axis=1), 1.0, atol=1e-5)
    np.testing.assert_allclose(np.linalg.norm(queries, axis=1), 1.0, atol=1e-5)

    second = benchmark.prepare_dataset(tmp_path, **arguments)
    assert second.reused is True
    assert second.generated_seconds == 0.0
    np.testing.assert_array_equal(dataset, np.load(second.dataset, allow_pickle=False))


def test_run_search_records_batches_and_preserves_query_order():
    class FakeBackend:
        def search(self, queries, k):
            labels = np.repeat(queries[:, :1].astype(np.int64), k, axis=1)
            return labels, np.zeros_like(labels, dtype=np.float32)

    queries = np.arange(5, dtype=np.float32).reshape(5, 1)
    neighbors, summary = benchmark.run_search(
        FakeBackend(),
        queries,
        k=2,
        batch_size=2,
        warmup_batches=1,
        repetitions=2,
    )

    np.testing.assert_array_equal(neighbors[:, 0], np.arange(5))
    assert summary["unique_query_count"] == 5
    assert summary["timed_query_count"] == 10
    assert summary["batch_count"] == 6
    assert len(summary["raw_batch_latency_ms"]) == 6
    assert summary["qps"] > 0
