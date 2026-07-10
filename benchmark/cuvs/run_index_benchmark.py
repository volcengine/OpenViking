#!/usr/bin/env python3
"""Benchmark OpenViking native flat search against cuVS indexes.

This is an index-level benchmark: it deliberately excludes embedding, HTTP,
record lookup, and LLM work. Datasets are generated as NumPy memory maps so a
large run does not need a second full host-memory copy.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from openviking.storage.vectordb.index.local_index import LocalIndex  # noqa: E402
from openviking.storage.vectordb.meta.collection_meta import (  # noqa: E402
    create_collection_meta,
)
from openviking.storage.vectordb.meta.index_meta import create_index_meta  # noqa: E402
from openviking.storage.vectordb.store.data import DeltaRecord  # noqa: E402

EXACT_BACKENDS = {"native", "cuvs_brute_force"}
SUPPORTED_BACKENDS = (
    *sorted(EXACT_BACKENDS),
    "cuvs_brute_force_fp16",
    "cuvs_cagra",
    "cuvs_cagra_fp16",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except (OSError, IndexError):
        return None
    return None


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def recall_at_k(actual: np.ndarray, expected: np.ndarray, k: int) -> float:
    if actual.shape[0] != expected.shape[0]:
        raise ValueError("Actual and expected query counts differ")
    effective_k = min(k, actual.shape[1], expected.shape[1])
    if effective_k <= 0 or actual.shape[0] == 0:
        return 1.0
    recalls = []
    for actual_row, expected_row in zip(actual, expected, strict=True):
        recalls.append(
            len(set(actual_row[:effective_k]).intersection(expected_row[:effective_k]))
            / effective_k
        )
    return statistics.fmean(recalls)


def normalize_rows(values: np.ndarray) -> None:
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    np.divide(values, norms, out=values, where=norms != 0)


def preload_dataset(values: np.ndarray, chunk_size: int) -> tuple[float, float]:
    """Read every dataset page before backend build timing starts."""

    started = time.perf_counter()
    checksum = 0.0
    for start in range(0, values.shape[0], chunk_size):
        checksum += float(np.sum(values[start : start + chunk_size], dtype=np.float64))
    return time.perf_counter() - started, checksum


@dataclass(frozen=True)
class DatasetFiles:
    dataset: Path
    queries: Path
    metadata: Path
    generated_seconds: float
    reused: bool
    ground_truth: Path | None = None


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_dataset_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    return normalized or "dataset"


def _copy_float_matrix(
    source: Any,
    destination: np.ndarray,
    *,
    chunk_size: int,
    normalize: bool,
) -> None:
    for start in range(0, destination.shape[0], chunk_size):
        stop = min(start + chunk_size, destination.shape[0])
        chunk = np.asarray(source[start:stop], dtype=np.float32)
        if normalize:
            normalize_rows(chunk)
        destination[start:stop] = chunk
    if hasattr(destination, "flush"):
        destination.flush()


def prepare_ann_benchmarks_dataset(
    root: Path,
    *,
    source: Path,
    metric: str,
    vector_limit: int | None,
    query_limit: int | None,
    generation_chunk_size: int,
    force: bool,
) -> DatasetFiles:
    """Convert an ann-benchmarks HDF5 dataset into reusable NumPy memmaps."""

    try:
        import h5py
    except ImportError as exc:
        raise RuntimeError(
            "Reading ann-benchmarks HDF5 files requires h5py; install h5py or use the "
            "cuVS development image"
        ) from exc

    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"ann-benchmarks dataset not found: {source}")

    started = time.perf_counter()
    source_digest = sha256_file(source)
    with h5py.File(source, "r") as hdf5:
        missing = sorted({"train", "test"}.difference(hdf5.keys()))
        if missing:
            raise ValueError(f"ann-benchmarks dataset is missing HDF5 keys: {', '.join(missing)}")
        train = hdf5["train"]
        test = hdf5["test"]
        if len(train.shape) != 2 or len(test.shape) != 2 or train.shape[1] != test.shape[1]:
            raise ValueError("ann-benchmarks train/test matrices must be 2D with equal dimensions")

        full_vector_count = int(train.shape[0])
        full_query_count = int(test.shape[0])
        vector_count = min(vector_limit or full_vector_count, full_vector_count)
        query_count = min(query_limit or full_query_count, full_query_count)
        dimension = int(train.shape[1])
        source_distance = hdf5.attrs.get("distance")
        if isinstance(source_distance, bytes):
            source_distance = source_distance.decode(errors="replace")
        source_distance = str(source_distance) if source_distance is not None else None
        expected_metric = {"angular": "cosine", "euclidean": "l2"}.get(source_distance)
        if expected_metric is not None and metric != expected_metric:
            raise ValueError(
                f"dataset distance={source_distance!r} requires --metric {expected_metric} "
                "for its provided ground truth"
            )

        has_usable_ground_truth = (
            "neighbors" in hdf5
            and vector_count == full_vector_count
            and len(hdf5["neighbors"].shape) == 2
            and int(hdf5["neighbors"].shape[0]) >= query_count
        )
        ground_truth_width = int(hdf5["neighbors"].shape[1]) if has_usable_ground_truth else 0
        dataset_name = _safe_dataset_name(source.stem)
        dataset_id = (
            f"ann-{dataset_name}-{source_digest[:12]}-n{vector_count}-q{query_count}-{metric}"
        )
        dataset_dir = root / "datasets" / dataset_id
        dataset_path = dataset_dir / "base.npy"
        query_path = dataset_dir / "queries.npy"
        ground_truth_path = dataset_dir / "ground_truth.npy"
        metadata_path = dataset_dir / "metadata.json"
        expected_metadata = {
            "format_version": 1,
            "dataset_kind": "ann-benchmarks-hdf5",
            "dataset_name": dataset_name,
            "source_sha256": source_digest,
            "source_size_bytes": source.stat().st_size,
            "source_distance": source_distance,
            "full_vector_count": full_vector_count,
            "full_query_count": full_query_count,
            "vector_count": vector_count,
            "query_count": query_count,
            "dimension": dimension,
            "metric": metric,
            "dtype": "float32",
            "normalized": metric == "cosine",
            "ground_truth_width": ground_truth_width,
        }

        expected_files = [dataset_path, query_path]
        if has_usable_ground_truth:
            expected_files.append(ground_truth_path)
        if not force and metadata_path.exists() and all(path.exists() for path in expected_files):
            try:
                if json.loads(metadata_path.read_text()) == expected_metadata:
                    return DatasetFiles(
                        dataset=dataset_path,
                        queries=query_path,
                        metadata=metadata_path,
                        generated_seconds=0.0,
                        reused=True,
                        ground_truth=(ground_truth_path if has_usable_ground_truth else None),
                    )
            except (OSError, json.JSONDecodeError):
                pass

        dataset_dir.mkdir(parents=True, exist_ok=True)
        dataset = np.lib.format.open_memmap(
            dataset_path,
            mode="w+",
            dtype=np.float32,
            shape=(vector_count, dimension),
        )
        _copy_float_matrix(
            train,
            dataset,
            chunk_size=generation_chunk_size,
            normalize=metric == "cosine",
        )
        del dataset

        queries = np.lib.format.open_memmap(
            query_path,
            mode="w+",
            dtype=np.float32,
            shape=(query_count, dimension),
        )
        _copy_float_matrix(
            test,
            queries,
            chunk_size=generation_chunk_size,
            normalize=metric == "cosine",
        )
        del queries

        if has_usable_ground_truth:
            ground_truth = np.lib.format.open_memmap(
                ground_truth_path,
                mode="w+",
                dtype=np.int64,
                shape=(query_count, ground_truth_width),
            )
            for start in range(0, query_count, generation_chunk_size):
                stop = min(start + generation_chunk_size, query_count)
                ground_truth[start:stop] = np.asarray(hdf5["neighbors"][start:stop], dtype=np.int64)
            ground_truth.flush()
            del ground_truth

    metadata_path.write_text(json.dumps(expected_metadata, indent=2, sort_keys=True) + "\n")
    return DatasetFiles(
        dataset=dataset_path,
        queries=query_path,
        metadata=metadata_path,
        generated_seconds=time.perf_counter() - started,
        reused=False,
        ground_truth=(ground_truth_path if has_usable_ground_truth else None),
    )


def prepare_dataset(
    root: Path,
    *,
    vector_count: int,
    dimension: int,
    query_count: int,
    metric: str,
    seed: int,
    generation_chunk_size: int,
    force: bool,
) -> DatasetFiles:
    dataset_id = f"random-n{vector_count}-d{dimension}-q{query_count}-{metric}-s{seed}"
    dataset_dir = root / "datasets" / dataset_id
    dataset_path = dataset_dir / "base.npy"
    query_path = dataset_dir / "queries.npy"
    metadata_path = dataset_dir / "metadata.json"
    expected_metadata = {
        "format_version": 1,
        "generator": "numpy.default_rng.standard_normal",
        "vector_count": vector_count,
        "dimension": dimension,
        "query_count": query_count,
        "metric": metric,
        "seed": seed,
        "dtype": "float32",
        "normalized": metric == "cosine",
    }

    if not force and dataset_path.exists() and query_path.exists() and metadata_path.exists():
        try:
            if json.loads(metadata_path.read_text()) == expected_metadata:
                return DatasetFiles(
                    dataset=dataset_path,
                    queries=query_path,
                    metadata=metadata_path,
                    generated_seconds=0.0,
                    reused=True,
                )
        except (OSError, json.JSONDecodeError):
            pass

    dataset_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    rng = np.random.default_rng(seed)
    dataset = np.lib.format.open_memmap(
        dataset_path,
        mode="w+",
        dtype=np.float32,
        shape=(vector_count, dimension),
    )
    for start in range(0, vector_count, generation_chunk_size):
        stop = min(start + generation_chunk_size, vector_count)
        chunk = rng.standard_normal((stop - start, dimension)).astype(np.float32)
        if metric == "cosine":
            normalize_rows(chunk)
        dataset[start:stop] = chunk
    dataset.flush()
    del dataset

    queries = rng.standard_normal((query_count, dimension)).astype(np.float32)
    if metric == "cosine":
        normalize_rows(queries)
    np.save(query_path, queries, allow_pickle=False)
    metadata_path.write_text(json.dumps(expected_metadata, indent=2, sort_keys=True) + "\n")
    return DatasetFiles(
        dataset=dataset_path,
        queries=query_path,
        metadata=metadata_path,
        generated_seconds=time.perf_counter() - started,
        reused=False,
    )


class Backend(Protocol):
    name: str

    def build(self, dataset: np.ndarray) -> None: ...

    def search(self, queries: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]: ...

    def gpu_memory_used_bytes(self) -> int | None: ...

    def close(self) -> None: ...


class NativeFlatBackend:
    name = "native"
    dtype = "unknown"

    def __init__(self, dimension: int, metric: str, ingest_batch_size: int):
        self.dimension = dimension
        self.metric = metric
        self.ingest_batch_size = ingest_batch_size
        self.index: LocalIndex | None = None

    def build(self, dataset: np.ndarray) -> None:
        collection_meta = create_collection_meta(
            "",
            {
                "CollectionName": "cuvs_index_benchmark",
                "Fields": [
                    {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
                    {"FieldName": "vector", "FieldType": "vector", "Dim": self.dimension},
                ],
            },
        )
        index_meta = create_index_meta(
            collection_meta,
            user_meta={
                "IndexName": "default",
                "VectorIndex": {
                    "IndexType": "flat",
                    # Inputs are normalized once by the dataset generator.
                    "Distance": "ip" if self.metric == "cosine" else "l2",
                },
                "ScalarIndex": [],
            },
        )
        config = index_meta.get_build_index_dict()
        self.dtype = str(config["VectorIndex"].get("Quant", "float")).lower()
        config["VectorIndex"]["ElementCount"] = 0
        config["VectorIndex"]["MaxElementCount"] = int(dataset.shape[0])
        self.index = LocalIndex(json.dumps(config), index_meta)

        for start in range(0, dataset.shape[0], self.ingest_batch_size):
            stop = min(start + self.ingest_batch_size, dataset.shape[0])
            records = [
                DeltaRecord(
                    type=DeltaRecord.Type.UPSERT,
                    label=start + offset,
                    vector=row.tolist(),
                )
                for offset, row in enumerate(np.asarray(dataset[start:stop]))
            ]
            self.index.upsert_data(records)

    def search(self, queries: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        if self.index is None:
            raise RuntimeError("Native index has not been built")
        all_neighbors: list[list[int]] = []
        all_scores: list[list[float]] = []
        for query in queries:
            labels, scores = self.index.search(query.tolist(), k, None, [], [])
            all_neighbors.append(labels)
            all_scores.append(scores)
        return np.asarray(all_neighbors, dtype=np.int64), np.asarray(all_scores, dtype=np.float32)

    def gpu_memory_used_bytes(self) -> int | None:
        return None

    def close(self) -> None:
        if self.index is not None:
            self.index.drop()
            self.index = None
        gc.collect()


class CuVSBackend:
    def __init__(
        self,
        algorithm: str,
        metric: str,
        build_params: dict[str, Any],
        search_params: dict[str, Any],
        dtype: str = "float32",
    ):
        import cupy as cp
        from cuvs.neighbors import brute_force, cagra

        if cp.cuda.runtime.getDeviceCount() < 1:
            raise RuntimeError("cuVS benchmark requires a visible CUDA device")
        self.cp = cp
        self.brute_force = brute_force
        self.cagra = cagra
        self.algorithm = algorithm
        self.metric = "inner_product" if metric == "cosine" else "sqeuclidean"
        self.build_params = dict(build_params)
        self.search_params = dict(search_params)
        self.dtype = dtype
        self.device_dtype = cp.float16 if dtype == "float16" else cp.float32
        self.dataset = None
        self.index = None
        self.name = f"cuvs_{algorithm}"

    def set_search_params(self, search_params: dict[str, Any]) -> None:
        self.search_params = dict(search_params)

    def build(self, dataset: np.ndarray) -> None:
        self.dataset = self.cp.asarray(dataset, dtype=self.device_dtype)
        if self.algorithm == "brute_force":
            self.index = self.brute_force.build(self.dataset, metric=self.metric)
        else:
            params = self.cagra.IndexParams(metric=self.metric, **self.build_params)
            self.index = self.cagra.build(params, self.dataset)
        self.cp.cuda.Stream.null.synchronize()

    def search(self, queries: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        if self.index is None:
            raise RuntimeError("cuVS index has not been built")
        device_queries = self.cp.asarray(queries, dtype=self.device_dtype)
        if self.algorithm == "brute_force":
            distances, neighbors = self.brute_force.search(self.index, device_queries, k)
        else:
            search_params = dict(self.search_params)
            configured_itopk = int(search_params.get("itopk_size", 64))
            minimum_itopk = ((k + 31) // 32) * 32
            search_params["itopk_size"] = max(configured_itopk, minimum_itopk)
            params = self.cagra.SearchParams(**search_params)
            distances, neighbors = self.cagra.search(params, self.index, device_queries, k)
        # Host copies synchronize the GPU and make the timing end-to-end.
        host_neighbors = self.cp.asnumpy(neighbors).astype(np.int64, copy=False)
        host_distances = self.cp.asnumpy(distances).astype(np.float32, copy=False)
        return host_neighbors, host_distances

    def gpu_memory_used_bytes(self) -> int | None:
        free, total = self.cp.cuda.runtime.memGetInfo()
        return int(total - free)

    def close(self) -> None:
        self.index = None
        self.dataset = None
        gc.collect()
        self.cp.get_default_memory_pool().free_all_blocks()
        self.cp.get_default_pinned_memory_pool().free_all_blocks()
        self.cp.cuda.Stream.null.synchronize()


def batches(values: np.ndarray, batch_size: int) -> Iterable[np.ndarray]:
    for start in range(0, values.shape[0], batch_size):
        yield values[start : start + batch_size]


def run_search(
    backend: Backend,
    queries: np.ndarray,
    *,
    k: int,
    batch_size: int,
    warmup_batches: int,
    repetitions: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    if queries.shape[0] == 0:
        raise ValueError("At least one query is required")
    warmup = queries[: min(queries.shape[0], batch_size)]
    for _ in range(warmup_batches):
        backend.search(warmup, k)

    neighbors: list[np.ndarray] = []
    batch_latency_ms: list[float] = []
    query_counts: list[int] = []
    for repetition in range(repetitions):
        for query_batch in batches(queries, batch_size):
            started = time.perf_counter()
            batch_neighbors, _ = backend.search(query_batch, k)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if repetition == 0:
                neighbors.append(batch_neighbors)
            batch_latency_ms.append(elapsed_ms)
            query_counts.append(query_batch.shape[0])

    total_seconds = sum(batch_latency_ms) / 1000.0
    timed_query_count = int(queries.shape[0]) * repetitions
    # Weight percentile samples by the number of queries in each batch. This
    # prevents a final short batch from receiving the same percentile weight
    # as a full batch (for example, 16 queries versus 128).
    per_query_latency_ms = [
        latency / count
        for latency, count in zip(batch_latency_ms, query_counts, strict=True)
        for _ in range(count)
    ]
    summary = {
        "unique_query_count": int(queries.shape[0]),
        "timed_query_count": timed_query_count,
        "repetitions": repetitions,
        "batch_size": batch_size,
        "batch_count": len(batch_latency_ms),
        "warmup_batches": warmup_batches,
        "total_seconds": total_seconds,
        "qps": timed_query_count / total_seconds if total_seconds else 0.0,
        "batch_latency_ms": {
            "p50": percentile(batch_latency_ms, 0.50),
            "p95": percentile(batch_latency_ms, 0.95),
            "p99": percentile(batch_latency_ms, 0.99),
            "mean": statistics.fmean(batch_latency_ms),
        },
        "per_query_latency_ms": {
            "p50": percentile(per_query_latency_ms, 0.50),
            "p95": percentile(per_query_latency_ms, 0.95),
            "p99": percentile(per_query_latency_ms, 0.99),
            "mean": statistics.fmean(per_query_latency_ms),
        },
        "raw_batch_latency_ms": batch_latency_ms,
    }
    return np.concatenate(neighbors, axis=0), summary


def parse_json_object(value: str, option: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"{option} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError(f"{option} must be a JSON object")
    return parsed


def parse_positive_int_list(value: str, option: str) -> list[int]:
    try:
        values = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{option} must be a comma-separated list of integers"
        ) from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError(f"{option} values must be positive")
    if len(values) != len(set(values)):
        raise argparse.ArgumentTypeError(f"{option} cannot contain duplicates")
    return values


def cagra_search_variants(args: argparse.Namespace) -> list[dict[str, Any]]:
    minimum_itopk = ((args.k + 31) // 32) * 32
    itopk_sizes = args.cagra_itopk_sizes or [int(args.cagra_search_params.get("itopk_size", 64))]
    configured_width = args.cagra_search_params.get("search_width")
    search_widths: list[int | None] = args.cagra_search_widths or [
        int(configured_width) if configured_width is not None else None
    ]
    variants = []
    for search_width in search_widths:
        for itopk_size in itopk_sizes:
            params = dict(args.cagra_search_params)
            params["itopk_size"] = max(itopk_size, minimum_itopk)
            if search_width is not None:
                params["search_width"] = search_width
            variants.append(params)
    return variants


def runtime_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "timestamp": utc_now(),
        "platform": platform.platform(),
        "python": sys.version,
        "numpy": np.__version__,
        "cpu_count": os.cpu_count(),
        "cpu_affinity_count": (
            len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
        ),
        "cpu_model": cpu_model(),
    }
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        metadata["git_revision"] = revision.stdout.strip()
        metadata["git_dirty"] = bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        metadata["git_revision"] = None
        metadata["git_dirty"] = None
    try:
        import cupy as cp
        import cuvs

        device = cp.cuda.Device()
        properties = cp.cuda.runtime.getDeviceProperties(device.id)
        name = properties.get("name", "unknown")
        if isinstance(name, bytes):
            name = name.decode(errors="replace")
        metadata["gpu"] = {
            "device_id": device.id,
            "name": name,
            "total_memory_bytes": int(properties.get("totalGlobalMem", 0)),
            "cuda_driver_api_version": int(cp.cuda.runtime.driverGetVersion()),
            "cuda_runtime_version": int(cp.cuda.runtime.runtimeGetVersion()),
        }
        metadata["cupy"] = cp.__version__
        metadata["cuvs"] = getattr(cuvs, "__version__", "unknown")
    except Exception as exc:
        metadata["gpu"] = None
        metadata["gpu_probe_error"] = str(exc)
    return metadata


def make_backend(name: str, args: argparse.Namespace) -> Backend:
    if name == "native":
        return NativeFlatBackend(args.dimension, args.metric, args.native_ingest_batch_size)
    if name == "cuvs_brute_force":
        return CuVSBackend("brute_force", args.metric, {}, {})
    if name == "cuvs_brute_force_fp16":
        return CuVSBackend("brute_force", args.metric, {}, {}, dtype="float16")
    if name == "cuvs_cagra":
        return CuVSBackend(
            "cagra",
            args.metric,
            args.cagra_build_params,
            args.cagra_search_params,
        )
    if name == "cuvs_cagra_fp16":
        return CuVSBackend(
            "cagra",
            args.metric,
            args.cagra_build_params,
            args.cagra_search_params,
            dtype="float16",
        )
    raise ValueError(f"Unsupported backend: {name}")


def print_summary(results: Sequence[dict[str, Any]]) -> None:
    print(
        "\nbackend                       build_s  first_q_ms   p50_ms   p95_ms       qps   recall"
    )
    print("----------------------------  --------  ----------  -------  -------  --------  -------")
    for result in results:
        search = result["search"]
        label = result["backend"]
        recall_at_k_value = result.get("recall_at_k")
        recall_text = f"{float(recall_at_k_value):.4f}" if recall_at_k_value is not None else "N/A"
        if result.get("cagra_search_params"):
            itopk_size = result["cagra_search_params"].get("itopk_size")
            search_width = result["cagra_search_params"].get("search_width", "auto")
            label = f"{label}[i={itopk_size},w={search_width}]"
        print(
            f"{label:<28}  "
            f"{result['build_seconds']:>8.3f}  "
            f"{result['first_search_per_query_ms']:>10.3f}  "
            f"{search['per_query_latency_ms']['p50']:>7.3f}  "
            f"{search['per_query_latency_ms']['p95']:>7.3f}  "
            f"{search['qps']:>8.1f}  "
            f"{recall_text:>7}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backends",
        default="native,cuvs_brute_force,cuvs_cagra",
        help=f"Comma-separated backends: {','.join(SUPPORTED_BACKENDS)}",
    )
    parser.add_argument("--vector-count", type=int, default=10_000)
    parser.add_argument("--dimension", type=int, default=128)
    parser.add_argument("--query-count", type=int, default=100)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--metric", choices=("cosine", "l2"), default="cosine")
    parser.add_argument(
        "--ann-benchmarks-hdf5",
        type=Path,
        help=(
            "Use train/test/neighbors from an ann-benchmarks HDF5 file instead of "
            "generating random vectors"
        ),
    )
    parser.add_argument(
        "--ann-vector-limit",
        type=int,
        help="Use only the first N public-dataset vectors; disables supplied ground truth",
    )
    parser.add_argument(
        "--ann-query-limit",
        type=int,
        help="Use only the first N public-dataset queries",
    )
    parser.add_argument("--query-batch-size", type=int, default=1)
    parser.add_argument("--warmup-batches", type=int, default=10)
    parser.add_argument("--search-repetitions", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data-root", type=Path, default=Path("/tmp/openviking-cuvs-benchmark"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force-generate", action="store_true")
    parser.add_argument(
        "--no-preload-dataset",
        action="store_true",
        help="Do not read the full memory-mapped dataset before backend build timing",
    )
    parser.add_argument("--generation-chunk-size", type=int, default=16_384)
    parser.add_argument("--native-ingest-batch-size", type=int, default=2_048)
    parser.add_argument(
        "--cagra-build-params",
        type=lambda value: parse_json_object(value, "--cagra-build-params"),
        default={"graph_degree": 32, "intermediate_graph_degree": 64},
    )
    parser.add_argument(
        "--cagra-search-params",
        type=lambda value: parse_json_object(value, "--cagra-search-params"),
        default={"itopk_size": 64},
    )
    parser.add_argument(
        "--cagra-itopk-sizes",
        type=lambda value: parse_positive_int_list(value, "--cagra-itopk-sizes"),
        help=(
            "Comma-separated itopk_size sweep. CAGRA is built once and searched once per "
            "value; other --cagra-search-params are shared"
        ),
    )
    parser.add_argument(
        "--cagra-search-widths",
        type=lambda value: parse_positive_int_list(value, "--cagra-search-widths"),
        help=(
            "Comma-separated search_width sweep. Combined with --cagra-itopk-sizes "
            "as a Cartesian product while sharing one CAGRA build"
        ),
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[str]:
    for name in (
        "vector_count",
        "dimension",
        "query_count",
        "k",
        "query_batch_size",
        "generation_chunk_size",
        "native_ingest_batch_size",
        "search_repetitions",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.ann_benchmarks_hdf5 is None and args.k > args.vector_count:
        parser.error("--k cannot exceed --vector-count")
    for name in ("ann_vector_limit", "ann_query_limit"):
        value = getattr(args, name)
        if value is not None and value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.ann_benchmarks_hdf5 is None and (
        args.ann_vector_limit is not None or args.ann_query_limit is not None
    ):
        parser.error("--ann-vector-limit/--ann-query-limit require --ann-benchmarks-hdf5")
    backends = [item.strip() for item in args.backends.split(",") if item.strip()]
    unknown = sorted(set(backends).difference(SUPPORTED_BACKENDS))
    if unknown:
        parser.error(f"Unsupported backends: {', '.join(unknown)}")
    if len(backends) != len(set(backends)):
        parser.error("--backends cannot contain duplicates")
    return backends


def validate_reference_requirement(
    parser: argparse.ArgumentParser,
    backends: Sequence[str],
    *,
    has_supplied_ground_truth: bool,
) -> None:
    """Require an exact reference for every lossy or approximate backend."""

    reference_required_backends = [name for name in backends if name not in EXACT_BACKENDS]
    if (
        reference_required_backends
        and not has_supplied_ground_truth
        and not set(backends).intersection(EXACT_BACKENDS)
    ):
        parser.error(
            f"{', '.join(reference_required_backends)} requires native/cuvs_brute_force "
            "or a full ann-benchmarks dataset with supplied ground truth"
        )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    backends = validate_args(parser, args)
    # Run an exact backend first so approximate results always have a reference.
    backends.sort(key=lambda name: (name not in EXACT_BACKENDS, name))

    if args.ann_benchmarks_hdf5 is not None:
        files = prepare_ann_benchmarks_dataset(
            args.data_root,
            source=args.ann_benchmarks_hdf5,
            metric=args.metric,
            vector_limit=args.ann_vector_limit,
            query_limit=args.ann_query_limit,
            generation_chunk_size=args.generation_chunk_size,
            force=args.force_generate,
        )
    else:
        files = prepare_dataset(
            args.data_root,
            vector_count=args.vector_count,
            dimension=args.dimension,
            query_count=args.query_count,
            metric=args.metric,
            seed=args.seed,
            generation_chunk_size=args.generation_chunk_size,
            force=args.force_generate,
        )
    dataset = np.load(files.dataset, mmap_mode="r", allow_pickle=False)
    queries = np.load(files.queries, mmap_mode="r", allow_pickle=False)
    dataset_metadata = json.loads(files.metadata.read_text())
    args.dimension = int(dataset.shape[1])
    if args.k > dataset.shape[0]:
        parser.error(f"--k cannot exceed dataset vector count ({dataset.shape[0]})")

    reference_neighbors: np.ndarray | None = None
    reference_backend: str | None = None
    if files.ground_truth is not None:
        supplied_ground_truth = np.load(files.ground_truth, mmap_mode="r", allow_pickle=False)
        if supplied_ground_truth.shape[0] != queries.shape[0]:
            parser.error("supplied ground truth query count does not match the query matrix")
        if args.k > supplied_ground_truth.shape[1]:
            parser.error(
                f"--k cannot exceed supplied ground truth width ({supplied_ground_truth.shape[1]})"
            )
        reference_neighbors = supplied_ground_truth[:, : args.k]
        reference_backend = "ann-benchmarks_ground_truth"
    validate_reference_requirement(
        parser,
        backends,
        has_supplied_ground_truth=reference_neighbors is not None,
    )

    cagra_variants = cagra_search_variants(args)
    variant_keys = [json.dumps(value, sort_keys=True) for value in cagra_variants]
    if len(variant_keys) != len(set(variant_keys)):
        parser.error("CAGRA search sweep contains duplicate effective parameter sets")

    preload_seconds = 0.0
    preload_checksum = None
    if not args.no_preload_dataset:
        preload_seconds, preload_checksum = preload_dataset(dataset, args.generation_chunk_size)
    result_document: dict[str, Any] = {
        "format_version": 2,
        "runtime": runtime_metadata(),
        "dataset": {
            "path": str(files.dataset.relative_to(args.data_root)),
            "query_path": str(files.queries.relative_to(args.data_root)),
            "metadata_path": str(files.metadata.relative_to(args.data_root)),
            "ground_truth_path": (
                str(files.ground_truth.relative_to(args.data_root))
                if files.ground_truth is not None
                else None
            ),
            "generated_seconds": files.generated_seconds,
            "preload_seconds": preload_seconds,
            "preload_checksum": preload_checksum,
            "reused": files.reused,
            "kind": dataset_metadata.get("dataset_kind", "random"),
            "name": dataset_metadata.get("dataset_name"),
            "source_sha256": dataset_metadata.get("source_sha256"),
            "source_distance": dataset_metadata.get("source_distance"),
            "vector_count": int(dataset.shape[0]),
            "dimension": int(dataset.shape[1]),
            "query_count": int(queries.shape[0]),
            "metric": args.metric,
            "seed": args.seed if args.ann_benchmarks_hdf5 is None else None,
        },
        "parameters": {
            "k": args.k,
            "query_batch_size": args.query_batch_size,
            "warmup_batches": args.warmup_batches,
            "search_repetitions": args.search_repetitions,
            "native_ingest_batch_size": args.native_ingest_batch_size,
            "cagra_build_params": args.cagra_build_params,
            "cagra_search_params": args.cagra_search_params,
            "cagra_itopk_sizes": args.cagra_itopk_sizes,
            "cagra_search_widths": args.cagra_search_widths,
        },
        "results": [],
    }

    for backend_name in backends:
        print(
            f"Building {backend_name} for {dataset.shape[0]} x {dataset.shape[1]} ...",
            flush=True,
        )
        backend = make_backend(backend_name, args)
        rss_before = current_rss_bytes()
        gpu_before = backend.gpu_memory_used_bytes()
        build_started = time.perf_counter()
        try:
            backend.build(dataset)
            build_seconds = time.perf_counter() - build_started
            rss_after = current_rss_bytes()
            gpu_after = backend.gpu_memory_used_bytes()

            search_variants = cagra_variants if backend_name.startswith("cuvs_cagra") else [None]
            for variant_index, search_params in enumerate(search_variants):
                if search_params is not None:
                    if not isinstance(backend, CuVSBackend):
                        raise TypeError("CAGRA search parameters require a cuVS backend")
                    backend.set_search_params(search_params)

                first_query_count = min(args.query_batch_size, queries.shape[0])
                first_started = time.perf_counter()
                backend.search(queries[:first_query_count], args.k)
                first_search_batch_ms = (time.perf_counter() - first_started) * 1000.0

                neighbors, search_summary = run_search(
                    backend,
                    queries,
                    k=args.k,
                    batch_size=args.query_batch_size,
                    warmup_batches=args.warmup_batches,
                    repetitions=args.search_repetitions,
                )
                result = {
                    "backend": backend_name,
                    "dtype": getattr(backend, "dtype", "unknown"),
                    "build_seconds": build_seconds,
                    "build_shared_across_variants": len(search_variants) > 1,
                    "search_variant_index": variant_index,
                    "first_search_batch_ms": first_search_batch_ms,
                    "first_search_per_query_ms": first_search_batch_ms / first_query_count,
                    "rss_before_bytes": rss_before,
                    "rss_after_build_bytes": rss_after,
                    "rss_delta_bytes": (
                        rss_after - rss_before
                        if rss_before is not None and rss_after is not None
                        else None
                    ),
                    "gpu_used_before_bytes": gpu_before,
                    "gpu_used_after_build_bytes": gpu_after,
                    "gpu_used_delta_bytes": (
                        gpu_after - gpu_before
                        if gpu_before is not None and gpu_after is not None
                        else None
                    ),
                    "cagra_search_params": search_params,
                    "search": search_summary,
                }
                if reference_neighbors is None and backend_name in EXACT_BACKENDS:
                    reference_neighbors = neighbors
                    reference_backend = backend_name
                    result["recall_at_k"] = 1.0
                elif reference_neighbors is not None:
                    result["recall_at_k"] = recall_at_k(neighbors, reference_neighbors, args.k)
                    result["ground_truth_backend"] = reference_backend
                result_document["results"].append(result)
        finally:
            backend.close()

    output = args.output
    if output is None:
        result_dir = args.data_root / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = result_dir / (
            f"index-n{dataset.shape[0]}-d{dataset.shape[1]}-q{queries.shape[0]}-"
            f"b{args.query_batch_size}-{stamp}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result_document, indent=2, sort_keys=True) + "\n")
    print_summary(result_document["results"])
    print(f"\nWrote {output}")


if __name__ == "__main__":
    main()
