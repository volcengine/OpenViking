#!/usr/bin/env python3
"""Benchmark OpenViking's async vector-service facade under concurrency.

This is the pre-embedding service layer: requests go through
VikingVectorIndexBackend and its asyncio.to_thread adapter, but use precomputed
query vectors so embedding and HTTP do not obscure vector-search scheduling.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from run_collection_benchmark import parse_positive_int_list  # noqa: E402
from run_index_benchmark import percentile, prepare_dataset, runtime_metadata  # noqa: E402

from openviking.server.identity import RequestContext, Role  # noqa: E402
from openviking.storage.viking_vector_index_backend import (  # noqa: E402
    VikingVectorIndexBackend,
)
from openviking_cli.session.user_id import UserIdentifier  # noqa: E402
from openviking_cli.utils.config.vectordb_config import (  # noqa: E402
    CuVSConfig,
    VectorDBBackendConfig,
)

SUPPORTED_BACKENDS = ("native", "cuvs_brute_force", "auto_cuvs_background")
ACCOUNT_ID = "benchmark"


def collection_schema(collection_name: str, dimension: int) -> dict[str, Any]:
    return {
        "CollectionName": collection_name,
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": dimension},
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "uniform_bucket", "FieldType": "int64"},
            {"FieldName": "row_number", "FieldType": "int64"},
        ],
        "ScalarIndex": ["account_id", "uniform_bucket", "row_number"],
    }


def make_record(row_index: int, vector: Sequence[float]) -> dict[str, Any]:
    return {
        "id": f"record-{row_index}",
        "vector": list(vector),
        "account_id": ACCOUNT_ID,
        "uniform_bucket": row_index % 1000,
        "row_number": row_index,
    }


def make_config(
    backend: str,
    *,
    project_path: Path,
    collection_name: str,
    dimension: int,
    filter_cache_size: int,
    auto_rebuild_debounce_ms: int,
) -> VectorDBBackendConfig:
    common = {
        "name": collection_name,
        "path": str(project_path),
        "index_name": "default",
        "distance_metric": "cosine",
        "dimension": dimension,
        "sparse_weight": 0.0,
    }
    if backend == "native":
        return VectorDBBackendConfig(backend="local", **common)
    if backend == "cuvs_brute_force":
        return VectorDBBackendConfig(
            backend="cuvs",
            cuvs=CuVSConfig(
                algorithm="brute_force",
                fallback_to_native=False,
                filter_cache_size=filter_cache_size,
            ),
            **common,
        )
    if backend == "auto_cuvs_background":
        return VectorDBBackendConfig(
            backend="local",
            cuvs=CuVSConfig(
                algorithm="brute_force",
                auto_enable=True,
                auto_background_rebuild=True,
                auto_rebuild_debounce_ms=auto_rebuild_debounce_ms,
                filter_cache_size=filter_cache_size,
            ),
            **common,
        )
    raise ValueError(f"Unsupported backend: {backend}")


async def wait_for_background_rebuild(manager: VikingVectorIndexBackend, timeout: float) -> float:
    index = manager._shared_adapter.get_collection().get_index("default")
    wait = getattr(index, "wait_for_background_rebuild", None)
    if wait is None:
        raise RuntimeError("auto background benchmark could not access the local index worker")
    started = time.perf_counter()
    completed = await asyncio.to_thread(wait, timeout)
    elapsed = time.perf_counter() - started
    if not completed:
        raise RuntimeError(f"auto background rebuild did not complete within {timeout}s")
    return elapsed


def concurrent_summary(
    latencies_ms: Sequence[float],
    *,
    wall_seconds: float,
    request_count: int,
    success_count: int,
    error_count: int,
) -> dict[str, Any]:
    return {
        "request_count": request_count,
        "success_count": success_count,
        "error_count": error_count,
        "wall_seconds": wall_seconds,
        "qps": success_count / wall_seconds if wall_seconds else 0.0,
        "latency_ms": {
            "mean": statistics.fmean(latencies_ms) if latencies_ms else 0.0,
            "p50": percentile(latencies_ms, 0.50),
            "p95": percentile(latencies_ms, 0.95),
            "p99": percentile(latencies_ms, 0.99),
            "max": max(latencies_ms, default=0.0),
        },
        "raw_latency_ms": list(latencies_ms),
    }


async def run_request_set(
    manager: Any,
    ctx: RequestContext,
    queries: np.ndarray,
    *,
    concurrency: int,
    request_count: int,
    k: int,
    filter_factory: Callable[[int], dict[str, Any] | None],
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    start_event = asyncio.Event()
    query_lists = [row.tolist() for row in queries]

    async def one_request(request_index: int) -> tuple[float, str | None]:
        await start_event.wait()
        async with semaphore:
            started = time.perf_counter()
            try:
                records = await manager.query(
                    query_vector=query_lists[request_index % len(query_lists)],
                    filter=filter_factory(request_index),
                    limit=k,
                    output_fields=["id"],
                    ctx=ctx,
                )
                latency_ms = (time.perf_counter() - started) * 1000.0
                if len(records) != k:
                    return latency_ms, f"expected {k} results, received {len(records)}"
                return latency_ms, None
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000.0
                return latency_ms, f"{type(exc).__name__}: {exc}"

    tasks = [asyncio.create_task(one_request(index)) for index in range(request_count)]
    await asyncio.sleep(0)
    wall_started = time.perf_counter()
    start_event.set()
    outcomes = await asyncio.gather(*tasks)
    wall_seconds = time.perf_counter() - wall_started
    latencies = [latency for latency, _ in outcomes]
    errors = [error for _, error in outcomes if error is not None]
    result = concurrent_summary(
        latencies,
        wall_seconds=wall_seconds,
        request_count=request_count,
        success_count=request_count - len(errors),
        error_count=len(errors),
    )
    result["errors"] = errors[:10]
    return result


def uniform_filter(selectivity: float) -> dict[str, Any]:
    upper_bound = max(1, int(1000 * selectivity))
    return {"op": "range", "field": "uniform_bucket", "lt": upper_bound}


def unique_filter(request_index: int, vector_count: int, salt: int) -> dict[str, Any]:
    width = max(10, min(100, vector_count))
    span = max(1, vector_count - width + 1)
    start = ((request_index + salt) * 997) % span
    return {
        "op": "range",
        "field": "row_number",
        "gte": start,
        "lt": start + width,
    }


async def ingest_dataset(
    manager: VikingVectorIndexBackend,
    dataset: np.ndarray,
    *,
    batch_size: int,
) -> dict[str, Any]:
    # Benchmark setup uses the shared adapter's batch API. Timed requests below
    # go through the public async service facade.
    adapter = manager._shared_adapter
    started = time.perf_counter()
    for start in range(0, dataset.shape[0], batch_size):
        stop = min(start + batch_size, dataset.shape[0])
        records = [make_record(index, dataset[index].tolist()) for index in range(start, stop)]
        await asyncio.to_thread(adapter.upsert, records)
    total_seconds = time.perf_counter() - started
    return {
        "total_seconds": total_seconds,
        "records_per_second": dataset.shape[0] / total_seconds if total_seconds else 0.0,
    }


async def warm_query(
    manager: VikingVectorIndexBackend,
    ctx: RequestContext,
    query: Sequence[float],
    *,
    filter: dict[str, Any] | None,
    k: int,
) -> float:
    started = time.perf_counter()
    records = await manager.query(
        query_vector=list(query),
        filter=filter,
        limit=k,
        output_fields=["id"],
        ctx=ctx,
    )
    if len(records) != k:
        raise RuntimeError(f"Warm query returned {len(records)} records, expected {k}")
    return (time.perf_counter() - started) * 1000.0


async def run_cached_scenario(
    manager: VikingVectorIndexBackend,
    ctx: RequestContext,
    queries: np.ndarray,
    *,
    name: str,
    filter: dict[str, Any] | None,
    concurrency_levels: Sequence[int],
    request_count: int,
    k: int,
) -> dict[str, Any]:
    initial_filter_ms = await warm_query(manager, ctx, queries[0], filter=filter, k=k)
    results = []
    for concurrency in concurrency_levels:
        await warm_query(manager, ctx, queries[0], filter=filter, k=k)
        result = await run_request_set(
            manager,
            ctx,
            queries,
            concurrency=concurrency,
            request_count=request_count,
            k=k,
            filter_factory=lambda _index, value=filter: value,
        )
        result["concurrency"] = concurrency
        results.append(result)
    return {
        "name": name,
        "filter_mode": "cached",
        "filter": filter,
        "initial_filter_query_ms": initial_filter_ms,
        "results": results,
    }


async def run_unique_filter_scenario(
    manager: VikingVectorIndexBackend,
    ctx: RequestContext,
    queries: np.ndarray,
    *,
    vector_count: int,
    concurrency_levels: Sequence[int],
    request_count: int,
    k: int,
) -> dict[str, Any]:
    results = []
    for matrix_index, concurrency in enumerate(concurrency_levels):
        salt = (matrix_index + 1) * 10_000
        result = await run_request_set(
            manager,
            ctx,
            queries,
            concurrency=concurrency,
            request_count=request_count,
            k=k,
            filter_factory=lambda index, s=salt: unique_filter(index, vector_count, s),
        )
        result["concurrency"] = concurrency
        results.append(result)
    return {
        "name": "unique_filter",
        "filter_mode": "new_per_request",
        "candidate_window": max(10, min(100, vector_count)),
        "results": results,
    }


async def run_post_mutation_scenario(
    manager: VikingVectorIndexBackend,
    ctx: RequestContext,
    dataset: np.ndarray,
    queries: np.ndarray,
    *,
    concurrency_levels: Sequence[int],
    k: int,
    wait_for_background: bool,
    background_timeout: float,
) -> dict[str, Any]:
    results = []
    for matrix_index, concurrency in enumerate(concurrency_levels):
        background_wait_seconds = 0.0
        if wait_for_background:
            background_wait_seconds = await wait_for_background_rebuild(
                manager, background_timeout
            )
        row_index = matrix_index % dataset.shape[0]
        record = make_record(row_index, queries[matrix_index % len(queries)].tolist())
        write_started = time.perf_counter()
        record_id = await manager.upsert(record, ctx=ctx)
        write_ms = (time.perf_counter() - write_started) * 1000.0
        if not record_id:
            raise RuntimeError("Post-mutation upsert failed")
        result = await run_request_set(
            manager,
            ctx,
            queries,
            concurrency=concurrency,
            request_count=concurrency,
            k=k,
            filter_factory=lambda _index: None,
        )
        result["concurrency"] = concurrency
        result["write_ms"] = write_ms
        result["pre_write_background_wait_seconds"] = background_wait_seconds
        results.append(result)
    return {
        "name": "post_mutation_burst",
        "filter_mode": "tenant_filter_after_cache_invalidation",
        "results": results,
    }


async def run_backend(
    backend: str,
    dataset: np.ndarray,
    queries: np.ndarray,
    *,
    run_root: Path,
    ingest_batch_size: int,
    filter_cache_size: int,
    auto_rebuild_debounce_ms: int,
    background_timeout: float,
    concurrency_levels: Sequence[int],
    cached_request_count: int,
    unique_request_count: int,
    k: int,
) -> dict[str, Any]:
    collection_name = f"service_benchmark_{backend}"
    config = make_config(
        backend,
        project_path=run_root / backend,
        collection_name=collection_name,
        dimension=dataset.shape[1],
        filter_cache_size=filter_cache_size,
        auto_rebuild_debounce_ms=auto_rebuild_debounce_ms,
    )
    manager = VikingVectorIndexBackend(config)
    ctx = RequestContext(user=UserIdentifier(ACCOUNT_ID, "user"), role=Role.USER)
    try:
        created = await manager.create_collection(
            collection_name,
            collection_schema(collection_name, dataset.shape[1]),
        )
        if not created:
            raise RuntimeError(f"Collection already exists for backend {backend}")
        ingest = await ingest_dataset(manager, dataset, batch_size=ingest_batch_size)
        record_count = await manager.count(ctx=ctx)
        if record_count != dataset.shape[0]:
            raise RuntimeError(
                f"Collection count mismatch for {backend}: {record_count} != {dataset.shape[0]}"
            )

        initial_background_wait_seconds = 0.0
        background_enabled = backend == "auto_cuvs_background"
        if background_enabled:
            initial_background_wait_seconds = await wait_for_background_rebuild(
                manager, background_timeout
            )

        scenarios = [
            await run_cached_scenario(
                manager,
                ctx,
                queries,
                name="tenant_cached",
                filter=None,
                concurrency_levels=concurrency_levels,
                request_count=cached_request_count,
                k=k,
            ),
            await run_cached_scenario(
                manager,
                ctx,
                queries,
                name="tenant_uniform_10pct_cached",
                filter=uniform_filter(0.10),
                concurrency_levels=concurrency_levels,
                request_count=cached_request_count,
                k=k,
            ),
            await run_unique_filter_scenario(
                manager,
                ctx,
                queries,
                vector_count=dataset.shape[0],
                concurrency_levels=concurrency_levels,
                request_count=unique_request_count,
                k=k,
            ),
            await run_post_mutation_scenario(
                manager,
                ctx,
                dataset,
                queries,
                concurrency_levels=concurrency_levels,
                k=k,
                wait_for_background=background_enabled,
                background_timeout=background_timeout,
            ),
        ]
        return {
            "backend": backend,
            "ingest": ingest,
            "record_count": record_count,
            "initial_background_wait_seconds": initial_background_wait_seconds,
            "scenarios": scenarios,
        }
    finally:
        await manager.close()
        gc.collect()


def print_summary(results: Sequence[dict[str, Any]]) -> None:
    print(
        "\nbackend                 scenario                         conc     p50_ms     p99_ms       qps  errors"
    )
    print(
        "----------------------  -------------------------------  ----  ----------  ----------  --------  ------"
    )
    for backend_result in results:
        for scenario in backend_result["scenarios"]:
            for result in scenario["results"]:
                latency = result["latency_ms"]
                print(
                    f"{backend_result['backend']:<22}  {scenario['name']:<31}  "
                    f"{result['concurrency']:>4}  {latency['p50']:>10.3f}  "
                    f"{latency['p99']:>10.3f}  {result['qps']:>8.1f}  "
                    f"{result['error_count']:>6}"
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backends", default="native,cuvs_brute_force")
    parser.add_argument("--vector-count", type=int, default=10_000)
    parser.add_argument("--dimension", type=int, default=128)
    parser.add_argument("--query-count", type=int, default=64)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ingest-batch-size", type=int, default=256)
    parser.add_argument("--filter-cache-size", type=int, default=16)
    parser.add_argument("--auto-rebuild-debounce-ms", type=int, default=500)
    parser.add_argument("--background-timeout", type=float, default=120.0)
    parser.add_argument(
        "--concurrency",
        type=lambda value: parse_positive_int_list(value, "--concurrency"),
        default=[1, 4, 16, 32, 64],
    )
    parser.add_argument("--cached-request-count", type=int, default=200)
    parser.add_argument("--unique-request-count", type=int, default=32)
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
        "ingest_batch_size",
        "cached_request_count",
        "unique_request_count",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.k > args.vector_count:
        parser.error("--k cannot exceed --vector-count")
    if args.filter_cache_size < 0:
        parser.error("--filter-cache-size cannot be negative")
    if args.auto_rebuild_debounce_ms < 0:
        parser.error("--auto-rebuild-debounce-ms cannot be negative")
    if args.background_timeout <= 0:
        parser.error("--background-timeout must be positive")
    backends = [item.strip() for item in args.backends.split(",") if item.strip()]
    if not backends:
        parser.error("--backends cannot be empty")
    unknown = sorted(set(backends).difference(SUPPORTED_BACKENDS))
    if unknown:
        parser.error(f"Unsupported backends: {', '.join(unknown)}")
    if len(backends) != len(set(backends)):
        parser.error("--backends cannot contain duplicates")
    return backends


async def async_main(args: argparse.Namespace, backends: Sequence[str]) -> dict[str, Any]:
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
    collection_root = args.data_root / "service-runs"
    collection_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="run-", dir=collection_root))
    results = []
    try:
        for backend in sorted(backends, key=SUPPORTED_BACKENDS.index):
            print(f"Running service concurrency benchmark for {backend} ...", flush=True)
            results.append(
                await run_backend(
                    backend,
                    dataset,
                    queries,
                    run_root=run_root,
                    ingest_batch_size=args.ingest_batch_size,
                    filter_cache_size=args.filter_cache_size,
                    auto_rebuild_debounce_ms=args.auto_rebuild_debounce_ms,
                    background_timeout=args.background_timeout,
                    concurrency_levels=args.concurrency,
                    cached_request_count=args.cached_request_count,
                    unique_request_count=args.unique_request_count,
                    k=args.k,
                )
            )
        return {
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
                "ingest_batch_size": args.ingest_batch_size,
                "filter_cache_size": args.filter_cache_size,
                "auto_rebuild_debounce_ms": args.auto_rebuild_debounce_ms,
                "background_timeout": args.background_timeout,
                "concurrency": args.concurrency,
                "cached_request_count": args.cached_request_count,
                "unique_request_count": args.unique_request_count,
                "asyncio_default_thread_pool_limit": min(32, (os.cpu_count() or 1) + 4),
            },
            "results": results,
        }
    finally:
        if not args.keep_collections:
            shutil.rmtree(run_root, ignore_errors=True)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    backends = validate_args(parser, args)
    output_document = asyncio.run(async_main(args, backends))
    output = args.output
    if output is None:
        result_dir = args.data_root / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dataset = output_document["dataset"]
        output = result_dir / (
            f"service-n{dataset['vector_count']}-d{dataset['dimension']}-"
            f"q{dataset['query_count']}-{stamp}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(output_document, indent=2, sort_keys=True) + "\n")
    print_summary(output_document["results"])
    print(f"\nWrote {output}")


if __name__ == "__main__":
    main()
