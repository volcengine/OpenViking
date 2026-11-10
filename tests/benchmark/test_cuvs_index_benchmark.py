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


def test_prepare_ann_benchmarks_dataset_converts_normalizes_and_reuses(tmp_path):
    h5py = pytest.importorskip("h5py")
    source = tmp_path / "tiny-angular.hdf5"
    with h5py.File(source, "w") as hdf5:
        hdf5.attrs["distance"] = "angular"
        hdf5.create_dataset(
            "train",
            data=np.asarray([[3.0, 4.0], [1.0, 0.0], [0.0, 2.0]], dtype=np.float32),
        )
        hdf5.create_dataset(
            "test",
            data=np.asarray([[6.0, 8.0], [2.0, 0.0]], dtype=np.float32),
        )
        hdf5.create_dataset("neighbors", data=np.asarray([[0, 1], [1, 0]], dtype=np.int32))

    arguments = {
        "source": source,
        "metric": "cosine",
        "vector_limit": None,
        "query_limit": 1,
        "generation_chunk_size": 2,
        "force": False,
    }
    first = benchmark.prepare_ann_benchmarks_dataset(tmp_path / "data", **arguments)
    dataset = np.load(first.dataset, mmap_mode="r", allow_pickle=False)
    queries = np.load(first.queries, mmap_mode="r", allow_pickle=False)
    ground_truth = np.load(first.ground_truth, mmap_mode="r", allow_pickle=False)

    assert first.reused is False
    assert dataset.shape == (3, 2)
    assert queries.shape == (1, 2)
    np.testing.assert_allclose(np.linalg.norm(dataset, axis=1), 1.0, atol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(queries, axis=1), 1.0, atol=1e-6)
    np.testing.assert_array_equal(ground_truth, np.asarray([[0, 1]]))
    metadata = first.metadata.read_text()
    assert str(source) not in metadata
    assert '"source_sha256"' in metadata

    second = benchmark.prepare_ann_benchmarks_dataset(tmp_path / "data", **arguments)
    assert second.reused is True
    assert second.generated_seconds == 0.0


def test_cagra_itopk_sweep_preserves_shared_search_params():
    parser = benchmark.build_parser()
    args = parser.parse_args(
        [
            "--cagra-search-params",
            '{"search_width":2}',
            "--cagra-itopk-sizes",
            "32,64,128",
        ]
    )

    assert benchmark.cagra_search_variants(args) == [
        {"search_width": 2, "itopk_size": 32},
        {"search_width": 2, "itopk_size": 64},
        {"search_width": 2, "itopk_size": 128},
    ]


def test_validate_args_accepts_float16_backend_variants():
    parser = benchmark.build_parser()
    args = parser.parse_args(
        ["--backends", "cuvs_brute_force,cuvs_brute_force_fp16,cuvs_cagra_fp16"]
    )

    assert benchmark.validate_args(parser, args) == [
        "cuvs_brute_force",
        "cuvs_brute_force_fp16",
        "cuvs_cagra_fp16",
    ]


@pytest.mark.parametrize(
    "backends",
    [
        ["cuvs_brute_force_fp16"],
        ["cuvs_cagra"],
        ["cuvs_cagra_fp16"],
        ["cuvs_brute_force_fp16", "cuvs_cagra"],
    ],
)
def test_lossy_or_approximate_backend_requires_exact_reference(backends):
    parser = benchmark.build_parser()

    with pytest.raises(SystemExit) as exc_info:
        benchmark.validate_reference_requirement(
            parser,
            backends,
            has_supplied_ground_truth=False,
        )
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "backends",
    [
        ["native"],
        ["cuvs_brute_force"],
        ["native", "cuvs_brute_force"],
        ["native", "cuvs_cagra"],
        ["cuvs_brute_force", "cuvs_brute_force_fp16"],
    ],
)
def test_reference_validation_accepts_exact_backend_combinations(backends):
    parser = benchmark.build_parser()

    benchmark.validate_reference_requirement(
        parser,
        backends,
        has_supplied_ground_truth=False,
    )


@pytest.mark.parametrize(
    "backend",
    ["cuvs_brute_force_fp16", "cuvs_cagra", "cuvs_cagra_fp16"],
)
def test_reference_validation_accepts_supplied_ground_truth(backend):
    parser = benchmark.build_parser()

    benchmark.validate_reference_requirement(
        parser,
        [backend],
        has_supplied_ground_truth=True,
    )


def test_print_summary_does_not_invent_missing_recall(capsys):
    benchmark.print_summary(
        [
            {
                "backend": "cuvs_brute_force_fp16",
                "build_seconds": 1.0,
                "first_search_per_query_ms": 2.0,
                "search": {
                    "per_query_latency_ms": {"p50": 3.0, "p95": 4.0},
                    "qps": 5.0,
                },
            }
        ]
    )

    output = capsys.readouterr().out
    assert "N/A" in output
    assert "1.0000" not in output


def test_cagra_itopk_and_search_width_sweeps_form_cartesian_product():
    parser = benchmark.build_parser()
    args = parser.parse_args(
        [
            "--cagra-itopk-sizes",
            "64,128",
            "--cagra-search-widths",
            "1,4",
        ]
    )

    assert benchmark.cagra_search_variants(args) == [
        {"itopk_size": 64, "search_width": 1},
        {"itopk_size": 128, "search_width": 1},
        {"itopk_size": 64, "search_width": 4},
        {"itopk_size": 128, "search_width": 4},
    ]


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
