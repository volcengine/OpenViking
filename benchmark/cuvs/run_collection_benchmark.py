#!/usr/bin/env python3
"""Benchmark OpenViking collection-level native and cuVS vector search.

Unlike the index microbenchmark, this harness goes through CollectionAdapter
and therefore includes filter compilation, label mapping, record lookup, and
result normalization. It also measures the lazy rebuild paid by the first
query after upsert, delete, and process restart.
"""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from run_index_benchmark import (  # noqa: E402
    current_rss_bytes,
    percentile,
    prepare_dataset,
    runtime_metadata,
)

from openviking.storage.vectordb_adapters.local_adapter import (  # noqa: E402
    CuVSCollectionAdapter,
    LocalCollectionAdapter,
)

SUPPORTED_BACKENDS = ("native", "cuvs_brute_force", "auto_cuvs")


@dataclass(frozen=True)
class FilterScenario:
    name: str
    filter: dict[str, Any] | None
    distribution: str
    selectivity: float


def parse_positive_int_list(value: str, option: str) -> list[int]:
    try:
        parsed = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{option} must contain integers") from exc
    if not parsed or any(item <= 0 for item in parsed):
        raise argparse.ArgumentTypeError(f"{option} must contain positive integers")
    if len(parsed) != len(set(parsed)):
        raise argparse.ArgumentTypeError(f"{option} cannot contain duplicates")
    return parsed


def filter_scenarios() -> list[FilterScenario]:
    scenarios = [FilterScenario("unfiltered", None, "none", 1.0)]
    for distribution, field in (
        ("uniform", "uniform_bucket"),
        ("clustered", "cluster_bucket"),
    ):
        for label, upper_bound, selectivity in (
            ("10pct", 100, 0.10),
            ("1pct", 10, 0.01),
            ("0_1pct", 1, 0.001),
        ):
            scenarios.append(
                FilterScenario(
                    name=f"{distribution}_{label}",
                    filter={"op": "range", "field": field, "lt": upper_bound},
                    distribution=distribution,
                    selectivity=selectivity,
                )
            )
    for label, prefix, selectivity in (
        ("10pct", "/docs/g0", 0.10),
        ("1pct", "/docs/g0/h0", 0.01),
        ("0_1pct", "/docs/g0/h0/i0", 0.001),
    ):
        scenarios.append(
            FilterScenario(
                name=f"path_{label}",
                filter={
                    "op": "must",
                    "field": "uri",
                    "conds": [prefix],
                    "para": "-d=-1",
                },
                distribution="path",
                selectivity=selectivity,
            )
        )
    return scenarios


def prebuild_selective_scenario(vector_count: int, native_threshold: int) -> FilterScenario | None:
    """Return a filter that should route native before the first GPU build."""

    eligible_count = (vector_count + 999) // 1000
    if native_threshold <= 0 or eligible_count > native_threshold:
        return None
    return FilterScenario(
        name="prebuild_selective_0_1pct",
        filter={"op": "must", "field": "uniform_bucket", "conds": [0]},
        distribution="uniform",
        selectivity=eligible_count / vector_count,
    )


def scalar_fields(row_index: int, vector_count: int) -> tuple[int, int]:
    uniform_bucket = row_index % 1000
    cluster_bucket = min(999, (row_index * 1000) // vector_count)
    return uniform_bucket, cluster_bucket


def make_record(
    row_index: int,
    vector: Sequence[float],
    vector_count: int,
) -> dict[str, Any]:
    uniform_bucket, cluster_bucket = scalar_fields(row_index, vector_count)
    return {
        "id": row_index + 1,
        "vector": list(vector),
        "uniform_bucket": uniform_bucket,
        "cluster_bucket": cluster_bucket,
        "uri": (
            f"/docs/g{row_index % 10}/h{(row_index // 10) % 10}/"
            f"i{(row_index // 100) % 10}/item-{row_index}"
        ),
    }


def make_adapter(
    backend: str,
    project_path: Path,
    collection_name: str,
    *,
    filter_cache_size: int,
    auto_filter_native_threshold: int,
    auto_path_filter_native_threshold: int,
):
    if backend == "native":
        return LocalCollectionAdapter(
            collection_name=collection_name,
            project_path=str(project_path),
            index_name="default",
        )
    if backend == "cuvs_brute_force":
        return CuVSCollectionAdapter(
            collection_name=collection_name,
            project_path=str(project_path),
            index_name="default",
            cuvs_config={
                "algorithm": "brute_force",
                "fallback_to_native": False,
                "filter_cache_size": filter_cache_size,
            },
        )
    if backend == "auto_cuvs":
        return LocalCollectionAdapter(
            collection_name=collection_name,
            project_path=str(project_path),
            index_name="default",
            collection_config={
                "dense_search": {
                    "backend": "auto_cuvs",
                    "algorithm": "brute_force",
                    "fallback_to_native": True,
                    "filter_cache_size": filter_cache_size,
                    "auto_filter_native_threshold": auto_filter_native_threshold,
                    "auto_path_filter_native_threshold": auto_path_filter_native_threshold,
                }
            },
        )
    raise ValueError(f"Unsupported backend: {backend}")


def collection_schema(collection_name: str, dimension: int) -> dict[str, Any]:
    return {
        "CollectionName": collection_name,
        "Fields": [
            {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": dimension},
            {"FieldName": "uniform_bucket", "FieldType": "int64"},
            {"FieldName": "cluster_bucket", "FieldType": "int64"},
            {"FieldName": "uri", "FieldType": "path"},
        ],
        "ScalarIndex": ["uniform_bucket", "cluster_bucket", "uri"],
    }


def gpu_memory_used_bytes() -> int | None:
    try:
        import cupy as cp

        free, total = cp.cuda.runtime.memGetInfo()
        return int(total - free)
    except Exception:
        return None


def summarize_latency(latencies_ms: Sequence[float], query_count: int) -> dict[str, Any]:
    total_seconds = sum(latencies_ms) / 1000.0
    return {
        "query_count": query_count,
        "total_seconds": total_seconds,
        "qps": query_count / total_seconds if total_seconds else 0.0,
        "latency_ms": {
            "mean": statistics.fmean(latencies_ms),
            "p50": percentile(latencies_ms, 0.50),
            "p95": percentile(latencies_ms, 0.95),
            "p99": percentile(latencies_ms, 0.99),
        },
        "raw_latency_ms": list(latencies_ms),
    }


def result_ids(records: Sequence[dict[str, Any]]) -> list[int]:
    return [int(record["id"]) for record in records]


def run_search_scenario(
    adapter: Any,
    queries: np.ndarray,
    *,
    scenario: FilterScenario,
    k: int,
    warmup_queries: int,
) -> dict[str, Any]:
    query_lists = [row.tolist() for row in queries]
    first_started = time.perf_counter()
    first_records = adapter.query(
        query_vector=query_lists[0],
        filter=scenario.filter,
        limit=k,
        output_fields=["id"],
    )
    first_query_ms = (time.perf_counter() - first_started) * 1000.0

    for index in range(warmup_queries):
        adapter.query(
            query_vector=query_lists[index % len(query_lists)],
            filter=scenario.filter,
            limit=k,
            output_fields=["id"],
        )

    latencies_ms: list[float] = []
    neighbors: list[list[int]] = []
    for query in query_lists:
        started = time.perf_counter()
        records = adapter.query(
            query_vector=query,
            filter=scenario.filter,
            limit=k,
            output_fields=["id"],
        )
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        neighbors.append(result_ids(records))

    return {
        "name": scenario.name,
        "filter": scenario.filter,
        "distribution": scenario.distribution,
        "target_selectivity": scenario.selectivity,
        "first_query_ms": first_query_ms,
        "first_result_count": len(first_records),
        "neighbors": neighbors,
        "search": summarize_latency(latencies_ms, len(query_lists)),
    }


def run_single_query_scenario(
    adapter: Any,
    query: Sequence[float],
    *,
    scenario: FilterScenario,
    k: int,
) -> dict[str, Any]:
    gpu_before = gpu_memory_used_bytes()
    started = time.perf_counter()
    records = adapter.query(
        query_vector=list(query),
        filter=scenario.filter,
        limit=k,
        output_fields=["id"],
    )
    latency_ms = (time.perf_counter() - started) * 1000.0
    gpu_after = gpu_memory_used_bytes()
    return {
        "name": scenario.name,
        "filter": scenario.filter,
        "distribution": scenario.distribution,
        "target_selectivity": scenario.selectivity,
        "latency_ms": latency_ms,
        "result_count": len(records),
        "gpu_before_bytes": gpu_before,
        "gpu_after_bytes": gpu_after,
        "gpu_delta_bytes": (
            gpu_after - gpu_before if gpu_before is not None and gpu_after is not None else None
        ),
    }


def recall_at_k(
    actual: Sequence[Sequence[int]], expected: Sequence[Sequence[int]], k: int
) -> float:
    if len(actual) != len(expected):
        raise ValueError("Actual and expected query counts differ")
    per_query: list[float] = []
    for actual_row, expected_row in zip(actual, expected, strict=True):
        denominator = min(k, len(expected_row))
        if denominator == 0:
            per_query.append(1.0 if not actual_row else 0.0)
            continue
        overlap = len(set(actual_row[:k]).intersection(expected_row[:k]))
        per_query.append(overlap / denominator)
    return statistics.fmean(per_query) if per_query else 1.0


def batched_upsert(adapter: Any, records: Sequence[dict[str, Any]], batch_size: int) -> float:
    api_seconds = 0.0
    for start in range(0, len(records), batch_size):
        started = time.perf_counter()
        adapter.upsert(list(records[start : start + batch_size]))
        api_seconds += time.perf_counter() - started
    return api_seconds


def ingest_dataset(
    adapter: Any,
    dataset: np.ndarray,
    *,
    batch_size: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    api_seconds = 0.0
    for start in range(0, dataset.shape[0], batch_size):
        stop = min(start + batch_size, dataset.shape[0])
        records = [
            make_record(index, dataset[index].tolist(), dataset.shape[0])
            for index in range(start, stop)
        ]
        api_seconds += batched_upsert(adapter, records, batch_size)
    total_seconds = time.perf_counter() - started
    return {
        "api_seconds": api_seconds,
        "total_seconds": total_seconds,
        "records_per_second": dataset.shape[0] / total_seconds if total_seconds else 0.0,
    }


def mutation_records(
    dataset: np.ndarray,
    queries: np.ndarray,
    count: int,
) -> list[dict[str, Any]]:
    records = []
    for index in range(count):
        # Reuse normalized query vectors so the update changes dense ranking.
        vector = queries[index % queries.shape[0]].tolist()
        records.append(make_record(index, vector, dataset.shape[0]))
    return records


def time_query(adapter: Any, query: Sequence[float], k: int) -> tuple[float, list[int]]:
    started = time.perf_counter()
    records = adapter.query(query_vector=list(query), limit=k, output_fields=["id"])
    return (time.perf_counter() - started) * 1000.0, result_ids(records)


def run_mutation_lifecycle(
    adapter: Any,
    dataset: np.ndarray,
    queries: np.ndarray,
    *,
    mutation_sizes: Sequence[int],
    delete_count: int,
    batch_size: int,
    k: int,
) -> dict[str, Any]:
    updates = []
    for count in mutation_sizes:
        if count > dataset.shape[0]:
            continue
        records = mutation_records(dataset, queries, count)
        wall_started = time.perf_counter()
        api_seconds = batched_upsert(adapter, records, batch_size)
        write_seconds = time.perf_counter() - wall_started
        next_query_ms, next_ids = time_query(adapter, queries[0], k)
        warm_query_ms, _ = time_query(adapter, queries[0], k)
        updates.append(
            {
                "count": count,
                "write_api_seconds": api_seconds,
                "write_wall_seconds": write_seconds,
                "next_query_ms": next_query_ms,
                "warm_query_ms": warm_query_ms,
                "next_result_count": len(next_ids),
            }
        )
        del records
        gc.collect()

    actual_delete_count = min(delete_count, dataset.shape[0])
    delete_ids = list(range(1, actual_delete_count + 1))
    delete_started = time.perf_counter()
    deleted = adapter.delete(ids=delete_ids)
    delete_seconds = time.perf_counter() - delete_started
    delete_next_query_ms, delete_next_ids = time_query(adapter, queries[0], k)
    delete_warm_query_ms, _ = time_query(adapter, queries[0], k)
    return {
        "updates": updates,
        "delete": {
            "count": deleted,
            "write_seconds": delete_seconds,
            "next_query_ms": delete_next_query_ms,
            "warm_query_ms": delete_warm_query_ms,
            "next_result_count": len(delete_next_ids),
        },
    }


def run_backend(
    backend: str,
    dataset: np.ndarray,
    queries: np.ndarray,
    *,
    run_root: Path,
    ingest_batch_size: int,
    warmup_queries: int,
    k: int,
    mutation_sizes: Sequence[int],
    delete_count: int,
    filter_cache_size: int,
    auto_filter_native_threshold: int,
    auto_path_filter_native_threshold: int,
) -> dict[str, Any]:
    collection_name = f"collection_benchmark_{backend}"
    project_path = run_root / backend
    adapter = make_adapter(
        backend,
        project_path,
        collection_name,
        filter_cache_size=filter_cache_size,
        auto_filter_native_threshold=auto_filter_native_threshold,
        auto_path_filter_native_threshold=auto_path_filter_native_threshold,
    )
    reopened = None
    rss_before = current_rss_bytes()
    gpu_before = gpu_memory_used_bytes()
    try:
        created = adapter.create_collection(
            collection_name,
            collection_schema(collection_name, dataset.shape[1]),
            distance="cosine",
            sparse_weight=0.0,
            index_name="default",
        )
        if not created:
            raise RuntimeError(f"Collection already exists for backend {backend}")

        ingest = ingest_dataset(adapter, dataset, batch_size=ingest_batch_size)
        record_count = adapter.count()
        if record_count != dataset.shape[0]:
            raise RuntimeError(
                f"Collection count mismatch for {backend}: {record_count} != {dataset.shape[0]}"
            )
        rss_after_ingest = current_rss_bytes()
        gpu_after_ingest = gpu_memory_used_bytes()

        prebuild_selective_query = None
        if backend == "auto_cuvs":
            scenario = prebuild_selective_scenario(dataset.shape[0], auto_filter_native_threshold)
            if scenario is not None:
                prebuild_selective_query = run_single_query_scenario(
                    adapter,
                    queries[0],
                    scenario=scenario,
                    k=k,
                )

        searches = [
            run_search_scenario(
                adapter,
                queries,
                scenario=scenario,
                k=k,
                warmup_queries=warmup_queries,
            )
            for scenario in filter_scenarios()
        ]
        rss_after_search = current_rss_bytes()
        gpu_after_search = gpu_memory_used_bytes()

        lifecycle = run_mutation_lifecycle(
            adapter,
            dataset,
            queries,
            mutation_sizes=mutation_sizes,
            delete_count=delete_count,
            batch_size=ingest_batch_size,
            k=k,
        )

        close_started = time.perf_counter()
        adapter.close()
        close_seconds = time.perf_counter() - close_started
        adapter = None

        construct_started = time.perf_counter()
        reopened = make_adapter(
            backend,
            project_path,
            collection_name,
            filter_cache_size=filter_cache_size,
            auto_filter_native_threshold=auto_filter_native_threshold,
            auto_path_filter_native_threshold=auto_path_filter_native_threshold,
        )
        construct_seconds = time.perf_counter() - construct_started
        reopen_query_ms, reopen_ids = time_query(reopened, queries[0], k)
        reopen_warm_query_ms, _ = time_query(reopened, queries[0], k)
        lifecycle["restart"] = {
            "close_seconds": close_seconds,
            "adapter_construct_seconds": construct_seconds,
            "first_query_ms": reopen_query_ms,
            "warm_query_ms": reopen_warm_query_ms,
            "first_result_count": len(reopen_ids),
        }

        return {
            "backend": backend,
            "ingest": ingest,
            "record_count": record_count,
            "rss_before_bytes": rss_before,
            "rss_after_ingest_bytes": rss_after_ingest,
            "rss_after_search_bytes": rss_after_search,
            "rss_ingest_delta_bytes": (
                rss_after_ingest - rss_before
                if rss_before is not None and rss_after_ingest is not None
                else None
            ),
            "gpu_before_bytes": gpu_before,
            "gpu_after_ingest_bytes": gpu_after_ingest,
            "gpu_after_search_bytes": gpu_after_search,
            "gpu_search_delta_bytes": (
                gpu_after_search - gpu_before
                if gpu_before is not None and gpu_after_search is not None
                else None
            ),
            "prebuild_selective_query": prebuild_selective_query,
            "searches": searches,
            "lifecycle": lifecycle,
        }
    finally:
        if adapter is not None:
            adapter.close()
        if reopened is not None:
            reopened.close()
        gc.collect()


def attach_recall(results: Sequence[dict[str, Any]], k: int) -> None:
    by_backend = {result["backend"]: result for result in results}
    reference = by_backend.get("cuvs_brute_force")
    if reference is None:
        return
    reference_searches = {item["name"]: item for item in reference["searches"]}
    for result in results:
        for search in result["searches"]:
            expected = reference_searches[search["name"]]["neighbors"]
            search["recall_at_k"] = recall_at_k(search["neighbors"], expected, k)
            search["ground_truth_backend"] = "cuvs_brute_force"


def strip_neighbors(results: Iterable[dict[str, Any]]) -> None:
    for result in results:
        for search in result["searches"]:
            search.pop("neighbors", None)


def print_summary(results: Sequence[dict[str, Any]]) -> None:
    print("\nbackend                 scenario              p50_ms   p95_ms       qps   recall")
    print("----------------------  --------------------  -------  -------  --------  -------")
    for result in results:
        for search in result["searches"]:
            latency = search["search"]["latency_ms"]
            print(
                f"{result['backend']:<22}  {search['name']:<20}  "
                f"{latency['p50']:>7.3f}  {latency['p95']:>7.3f}  "
                f"{search['search']['qps']:>8.1f}  "
                f"{search.get('recall_at_k', 1.0):>7.4f}"
            )
    print("\nbackend                 mutation   write_s  next_query_ms  warm_query_ms")
    print("----------------------  ---------  -------  -------------  -------------")
    for result in results:
        for update in result["lifecycle"]["updates"]:
            print(
                f"{result['backend']:<22}  {update['count']:>9}  "
                f"{update['write_wall_seconds']:>7.3f}  "
                f"{update['next_query_ms']:>13.3f}  {update['warm_query_ms']:>13.3f}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backends", default="native,cuvs_brute_force")
    parser.add_argument("--vector-count", type=int, default=10_000)
    parser.add_argument("--dimension", type=int, default=128)
    parser.add_argument("--query-count", type=int, default=20)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--warmup-queries", type=int, default=3)
    parser.add_argument("--ingest-batch-size", type=int, default=256)
    parser.add_argument(
        "--mutation-sizes",
        type=lambda value: parse_positive_int_list(value, "--mutation-sizes"),
        default=[1, 100, 1_000, 10_000],
    )
    parser.add_argument("--delete-count", type=int, default=100)
    parser.add_argument("--filter-cache-size", type=int, default=16)
    parser.add_argument("--auto-filter-native-threshold", type=int, default=2000)
    parser.add_argument("--auto-path-filter-native-threshold", type=int, default=200)
    parser.add_argument("--data-root", type=Path, default=Path("/tmp/openviking-cuvs-benchmark"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep-collections", action="store_true")
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[str]:
    for name in (
        "vector_count",
        "dimension",
        "query_count",
        "k",
        "warmup_queries",
        "ingest_batch_size",
        "delete_count",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.k > args.vector_count:
        parser.error("--k cannot exceed --vector-count")
    if args.filter_cache_size < 0:
        parser.error("--filter-cache-size cannot be negative")
    if args.auto_filter_native_threshold < 0:
        parser.error("--auto-filter-native-threshold cannot be negative")
    if args.auto_path_filter_native_threshold < 0:
        parser.error("--auto-path-filter-native-threshold cannot be negative")
    backends = [item.strip() for item in args.backends.split(",") if item.strip()]
    unknown = sorted(set(backends).difference(SUPPORTED_BACKENDS))
    if unknown:
        parser.error(f"Unsupported backends: {', '.join(unknown)}")
    if len(backends) != len(set(backends)):
        parser.error("--backends cannot contain duplicates")
    return backends


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    backends = validate_args(parser, args)
    backends.sort(key=SUPPORTED_BACKENDS.index)

    files = prepare_dataset(
        args.data_root,
        vector_count=args.vector_count,
        dimension=args.dimension,
        query_count=args.query_count,
        metric="cosine",
        seed=args.seed,
        generation_chunk_size=16_384,
        force=False,
    )
    dataset = np.load(files.dataset, mmap_mode="r", allow_pickle=False)
    queries = np.load(files.queries, mmap_mode="r", allow_pickle=False)

    collection_root = args.data_root / "collection-runs"
    collection_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="run-", dir=collection_root))
    results: list[dict[str, Any]] = []
    try:
        for backend in backends:
            print(f"Running collection benchmark for {backend} ...", flush=True)
            results.append(
                run_backend(
                    backend,
                    dataset,
                    queries,
                    run_root=run_root,
                    ingest_batch_size=args.ingest_batch_size,
                    warmup_queries=args.warmup_queries,
                    k=args.k,
                    mutation_sizes=args.mutation_sizes,
                    delete_count=args.delete_count,
                    filter_cache_size=args.filter_cache_size,
                    auto_filter_native_threshold=args.auto_filter_native_threshold,
                    auto_path_filter_native_threshold=(args.auto_path_filter_native_threshold),
                )
            )
        attach_recall(results, args.k)
        strip_neighbors(results)

        output_document = {
            "format_version": 1,
            "runtime": runtime_metadata(),
            "dataset": {
                "kind": "random",
                "vector_count": int(dataset.shape[0]),
                "dimension": int(dataset.shape[1]),
                "query_count": int(queries.shape[0]),
                "metric": "cosine",
                "seed": args.seed,
                "reused": files.reused,
                "generated_seconds": files.generated_seconds,
            },
            "parameters": {
                "k": args.k,
                "warmup_queries": args.warmup_queries,
                "ingest_batch_size": args.ingest_batch_size,
                "mutation_sizes": args.mutation_sizes,
                "delete_count": args.delete_count,
                "filter_cache_size": args.filter_cache_size,
                "auto_filter_native_threshold": args.auto_filter_native_threshold,
                "auto_path_filter_native_threshold": (args.auto_path_filter_native_threshold),
                "filter_scenarios": [scenario.name for scenario in filter_scenarios()],
            },
            "results": results,
        }
        output = args.output
        if output is None:
            result_dir = args.data_root / "results"
            result_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output = result_dir / (
                f"collection-n{dataset.shape[0]}-d{dataset.shape[1]}-"
                f"q{queries.shape[0]}-{stamp}.json"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(output_document, indent=2, sort_keys=True) + "\n")
        print_summary(results)
        print(f"\nWrote {output}")
    finally:
        if not args.keep_collections:
            shutil.rmtree(run_root, ignore_errors=True)


if __name__ == "__main__":
    main()
