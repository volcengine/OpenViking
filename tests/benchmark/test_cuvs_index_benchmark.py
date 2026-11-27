import json
import sys
from functools import partial

import numpy as np
import pytest

from benchmark.cuvs import run_index_benchmark as benchmark


def test_prepare_dataset_is_normalized_deterministic_and_reusable(tmp_path):
    prepare = partial(
        benchmark.prepare_dataset,
        tmp_path,
        vector_count=32,
        dimension=8,
        query_count=5,
        metric="cosine",
        seed=7,
        generation_chunk_size=11,
        force=False,
    )
    first = prepare()
    dataset = np.load(first.dataset, mmap_mode="r", allow_pickle=False)
    queries = np.load(first.queries, mmap_mode="r", allow_pickle=False)

    assert first.reused is False
    assert dataset.shape == (32, 8)
    assert queries.shape == (5, 8)
    np.testing.assert_allclose(np.linalg.norm(dataset, axis=1), 1.0, atol=1e-5)
    np.testing.assert_allclose(np.linalg.norm(queries, axis=1), 1.0, atol=1e-5)

    second = prepare()
    assert second.reused is True
    assert second.generated_seconds == 0.0
    np.testing.assert_array_equal(dataset, np.load(second.dataset, allow_pickle=False))


def test_prepare_ann_benchmarks_dataset_converts_normalizes_and_reuses(tmp_path, monkeypatch):
    h5py = pytest.importorskip("h5py")
    source = tmp_path / "tiny-angular.hdf5"
    with h5py.File(source, "w") as hdf5:
        hdf5.attrs["distance"] = "angular"
        hdf5.create_dataset(
            "train",
            data=np.asarray(
                [[3.0, 4.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 2.0, 0.0, 0.0]], dtype=np.float32
            ),
        )
        hdf5.create_dataset(
            "test",
            data=np.asarray([[6.0, 8.0, 0.0, 0.0], [2.0, 0.0, 0.0, 0.0]], dtype=np.float32),
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
    assert first.ground_truth is not None
    ground_truth = np.load(first.ground_truth, mmap_mode="r", allow_pickle=False)

    assert first.reused is False
    assert dataset.shape == (3, 4)
    assert queries.shape == (1, 4)
    np.testing.assert_allclose(np.linalg.norm(dataset, axis=1), 1.0, atol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(queries, axis=1), 1.0, atol=1e-6)
    np.testing.assert_array_equal(ground_truth, np.asarray([[0, 1]]))
    metadata = first.metadata.read_text()
    assert str(source) not in metadata
    assert '"source_sha256"' in metadata

    second = benchmark.prepare_ann_benchmarks_dataset(tmp_path / "data", **arguments)
    assert second.reused is True
    assert second.generated_seconds == 0.0
    output = tmp_path / "run.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_benchmark.py",
            "--backends",
            "native",
            "--ann-benchmarks-hdf5",
            str(source),
            "--ann-query-limit",
            "1",
            "--k",
            "2",
            "--data-root",
            str(tmp_path / "data"),
            "--output",
            str(output),
        ],
    )
    benchmark.main()

    run = json.loads(output.read_text())
    [result] = run["results"]
    assert result["recall_at_k"] == pytest.approx(0.5)
    assert result["ground_truth_backend"] == "ann-benchmarks_ground_truth"


def test_lossy_or_approximate_backend_requires_exact_reference(monkeypatch, tmp_path, capsys):
    backends = "cuvs_brute_force_fp16,cuvs_cagra,cuvs_cagra_fp16"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_index_benchmark.py",
            "--backends",
            backends,
            "--vector-count",
            "4",
            "--query-count",
            "2",
            "--dimension",
            "4",
            "--k",
            "1",
            "--data-root",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        benchmark.main()
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "requires native/cuvs_brute_force" in error
    assert "supplied ground truth" in error
    assert all(name in error for name in backends.split(","))

    parser = benchmark.build_parser()
    parsed_backends = benchmark.validate_args(parser, parser.parse_args(["--backends", backends]))
    for exact in ("native", "cuvs_brute_force"):
        benchmark.validate_reference_requirement(
            parser, [*parsed_backends, exact], has_supplied_ground_truth=False
        )
    benchmark.validate_reference_requirement(
        parser, parsed_backends, has_supplied_ground_truth=True
    )


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
    args = parser.parse_args(
        [
            "--cagra-itopk-sizes",
            "64,128",
            "--cagra-search-params",
            '{"search_width":2}',
        ]
    )
    assert benchmark.cagra_search_variants(args) == [
        {"itopk_size": 64, "search_width": 2},
        {"itopk_size": 128, "search_width": 2},
    ]


def test_run_search_records_batches_and_preserves_query_order():
    queries = np.eye(8, dtype=np.float32)[:5]
    backend = benchmark.NativeFlatBackend(dimension=8, metric="cosine", ingest_batch_size=2)
    try:
        backend.build(queries)
        neighbors, summary = benchmark.run_search(
            backend, queries, k=2, batch_size=2, warmup_batches=1, repetitions=2
        )
    finally:
        backend.close()

    np.testing.assert_array_equal(neighbors[:, 0], np.arange(5))
    assert summary["unique_query_count"] == 5
    assert summary["timed_query_count"] == 10
    assert summary["batch_count"] == 6
    assert len(summary["raw_batch_latency_ms"]) == 6
    assert summary["qps"] > 0
    assert benchmark.percentile([4.0, 1.0, 3.0, 2.0], 0.5) == pytest.approx(2.5)
