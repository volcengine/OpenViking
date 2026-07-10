#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking vector backend performance benchmark.

This benchmark targets OpenViking's VikingVectorIndexBackend boundary. It uses
the real OV context schema, URI scope filters, tenant context, and backend calls
while still accepting precomputed vectors from synthetic or dir-vector data.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import platform
import random
import shutil
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

from benchmark.vectordb_perf.async_utils import map_bounded_as_completed

from openviking.storage.expr import PathScope
from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OpenVikingConfigSingleton


BENCH_ACCOUNT_ID = "bench_account"
BENCH_USER_ID = "bench_user"
DIR_VECTOR_DATASET_URL = "https://github.com/KurtPatrickHere/dir-vector-dataset"
DEFAULT_DIR_VECTOR_DATASETS = ("wiki", "arxiv")
PROGRESS_INTERVAL_SECONDS = 5.0


PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "smoke": {
        "rows": 128,
        "queries": 8,
        "dim": 32,
        "batch_size": 32,
        "concurrency": 1,
        "top_k": 10,
        "path_depth": 4,
        "path_fanout": 4,
        "filter_selectivity": 0.25,
    },
    "standard": {
        "rows": 10000,
        "queries": 100,
        "dim": 1024,
        "batch_size": 500,
        "concurrency": 4,
        "top_k": 20,
        "path_depth": 6,
        "path_fanout": 8,
        "filter_selectivity": 0.05,
    },
    "stress": {
        "rows": 100000,
        "queries": 500,
        "dim": 1024,
        "batch_size": 1000,
        "concurrency": 8,
        "top_k": 100,
        "path_depth": 8,
        "path_fanout": 10,
        "filter_selectivity": 0.01,
    },
}

DIR_VECTOR_FILES = {
    "wiki": {
        "corpus": "dbpedia_dir_2m_corpus.jsonl",
        "vectors": "dbpedia_dir_2m_corpus_vectors.fvecs",
        "queries": "dbpedia_dir_2m_query.jsonl",
        "query_vectors": "dbpedia_dir_2m_query_vectors.fvecs",
        "ground_truth": "dbpedia_dir_2m_groundtruth.tsv",
    },
    "arxiv": {
        "corpus": "arxiv_corpus_metadata.json",
        "vectors": "arxiv_corpus_vectors.fvecs",
        "queries": "arxiv_query_constraint.json",
        "query_vectors": "arxiv_query_vectors.fvecs",
        "ground_truth": "arxiv_ground_truth.txt",
    },
    "arxiv_category": {
        "corpus": "arxiv_corpus_metadata.json",
        "vectors": "arxiv_corpus_vectors.fvecs",
        "queries": "arxiv_category_query_constraint.json",
        "query_vectors": "arxiv_category_query_vectors.fvecs",
        "ground_truth": "arxiv_category_ground_truth.txt",
    },
}


@dataclass
class BenchOptions:
    config: Optional[str]
    output_dir: Path
    run_id: str
    profile: str
    mode: str
    workload: str
    dataset_root: Optional[Path]
    dataset: str
    full: bool
    rows: int
    queries: int
    dim: int
    batch_size: int
    concurrency: int
    top_k: int
    path_depth: int
    path_fanout: int
    filter_selectivity: float
    seed: int
    distance: Optional[str]
    drop_at_end: bool


@dataclass
class QueryCase:
    query_id: str
    vector: list[float]
    filter_path: str
    ground_truth_ids: list[str] = field(default_factory=list)


@dataclass
class Workload:
    name: str
    dim: int
    records: Callable[[], Iterator[dict[str, Any]]]
    queries: list[QueryCase]
    expected_rows: Optional[int]


@dataclass
class Event:
    phase: str
    operation: str
    started_at: str
    ended_at: str
    latency_ms: float
    success: bool
    count: int = 0
    error: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    run_id: str
    collection_name: str
    output_dir: str
    events: list[Event]
    validation_errors: list[str]
    quality: dict[str, Any]
    workload: dict[str, Any]
    environment: dict[str, Any]
    kept_collection: bool


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def progress(message: str) -> None:
    print(f"[vectordb_perf] {message}", file=sys.stderr, flush=True)


def progress_count(
    phase: str,
    done: int,
    total: Optional[int],
    started_at: float,
    last_reported_at: float,
    *,
    force: bool = False,
) -> float:
    now = time.perf_counter()
    if not force and last_reported_at and now - last_reported_at < PROGRESS_INTERVAL_SECONDS:
        return last_reported_at
    elapsed = max(now - started_at, 1e-9)
    rate = done / elapsed
    if total:
        progress(f"{phase}: {done}/{total} ({done / total * 100.0:.1f}%), {rate:.1f}/s")
    else:
        progress(f"{phase}: {done}, {rate:.1f}/s")
    return now


def timed_event(
    phase: str,
    operation: str,
    fn: Callable[[], Any],
    *,
    count: int = 0,
    extra: Optional[dict[str, Any]] = None,
) -> tuple[Event, Any]:
    started = utc_now()
    start = time.perf_counter()
    try:
        result = fn()
        success = True
        error = None
    except Exception as exc:  # benchmark should record the failing operation
        result = None
        success = False
        error = f"{type(exc).__name__}: {exc}"
    ended = utc_now()
    latency_ms = (time.perf_counter() - start) * 1000.0
    return (
        Event(
            phase=phase,
            operation=operation,
            started_at=started,
            ended_at=ended,
            latency_ms=latency_ms,
            success=success,
            count=count,
            error=error,
            extra=extra or {},
        ),
        result,
    )


async def async_timed_event(
    phase: str,
    operation: str,
    fn: Callable[[], Any],
    *,
    count: int = 0,
    extra: Optional[dict[str, Any]] = None,
) -> tuple[Event, Any]:
    started = utc_now()
    start = time.perf_counter()
    try:
        result = await fn()
        success = True
        error = None
    except Exception as exc:  # benchmark should record the failing operation
        result = None
        success = False
        error = f"{type(exc).__name__}: {exc}"
    ended = utc_now()
    latency_ms = (time.perf_counter() - start) * 1000.0
    return (
        Event(
            phase=phase,
            operation=operation,
            started_at=started,
            ended_at=ended,
            latency_ms=latency_ms,
            success=success,
            count=count,
            error=error,
            extra=extra or {},
        ),
        result,
    )


def read_fvecs_dim(path: Path) -> int:
    with path.open("rb") as handle:
        raw = handle.read(4)
    if len(raw) != 4:
        raise ValueError(f"empty fvecs file: {path}")
    return struct.unpack("<i", raw)[0]


def count_fvecs(path: Path) -> int:
    dim = read_fvecs_dim(path)
    if dim <= 0:
        raise ValueError(f"invalid fvecs dimension {dim} in {path}")
    stride = 4 + dim * 4
    size = path.stat().st_size
    if size % stride != 0:
        raise ValueError(f"fvecs file size is not aligned with dim={dim}: {path}")
    return size // stride


def iter_fvecs(path: Path, limit: Optional[int] = None) -> Iterator[list[float]]:
    with path.open("rb") as handle:
        count = 0
        while limit is None or count < limit:
            raw_dim = handle.read(4)
            if not raw_dim:
                return
            if len(raw_dim) != 4:
                raise ValueError(f"truncated fvecs dimension in {path}")
            dim = struct.unpack("<i", raw_dim)[0]
            if dim <= 0:
                raise ValueError(f"invalid fvecs dimension {dim} in {path}")
            raw_vec = handle.read(dim * 4)
            if len(raw_vec) != dim * 4:
                raise ValueError(f"truncated fvecs vector in {path}")
            yield list(struct.unpack(f"<{dim}f", raw_vec))
            count += 1


def iter_json_records(path: Path, limit: Optional[int] = None) -> Iterator[dict[str, Any]]:
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if limit is not None and index >= limit:
                    return
                stripped = line.strip()
                if stripped:
                    item = json.loads(stripped)
                    if isinstance(item, dict):
                        yield item
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        iterable: Iterable[Any] = data.values()
    elif isinstance(data, list):
        iterable = data
    else:
        raise ValueError(f"unsupported JSON corpus shape in {path}")
    for index, item in enumerate(iterable):
        if limit is not None and index >= limit:
            return
        if isinstance(item, dict):
            yield item


def pick_value(record: dict[str, Any], candidates: Sequence[str]) -> Any:
    for key in candidates:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_path(path: Any) -> str:
    if isinstance(path, list):
        if not path:
            return "/"
        return ";".join(normalize_path(item) for item in path if item not in (None, ""))
    if not isinstance(path, str):
        return "/"
    stripped = path.strip()
    if not stripped:
        return "/"
    if stripped.startswith("viking://"):
        stripped = "/" + stripped[len("viking://") :].strip("/")
    elif not stripped.startswith("/"):
        stripped = "/" + stripped
    return stripped.rstrip("/") if len(stripped) > 1 else stripped


def extract_path(record: dict[str, Any]) -> str:
    value = pick_value(
        record,
        (
            "dir_path",
            "dir_paths",
            "path",
            "paths",
            "directory",
            "directories",
            "category_path",
            "category_paths",
            "date_path",
        ),
    )
    if value is not None:
        return normalize_path(value)

    # Last resort for dataset variants with nested constraint metadata.
    for value in record.values():
        if isinstance(value, str) and "/" in value:
            return normalize_path(value)
        if isinstance(value, list):
            path_items = [item for item in value if isinstance(item, str) and "/" in item]
            if path_items:
                return normalize_path(path_items)
    return "/"


def record_id(record: dict[str, Any], fallback: int) -> str:
    value = pick_value(
        record,
        ("id", "entity_id", "paper_id", "doc_id", "item_id", "source_id", "qid", "query_id"),
    )
    return str(value) if value is not None else str(fallback)


def path_prefix(path: str, segments: int) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return "/"
    selected = parts[: max(1, min(segments, len(parts)))]
    return "/" + "/".join(selected)


def path_matches(path_value: Any, prefix: str) -> bool:
    paths = str(path_value or "").split(";")
    normalized_prefix = normalize_path(prefix)
    for item in paths:
        path = normalize_path(item)
        if path == normalized_prefix or path.startswith(normalized_prefix.rstrip("/") + "/"):
            return True
    return False


def synthetic_vector(rng: random.Random, dim: int) -> list[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


def synthetic_path(index: int, *, depth: int, fanout: int) -> str:
    fanout = max(1, fanout)
    parts = []
    value = index
    for level in range(max(1, depth)):
        parts.append(f"d{level}_{value % fanout}")
        value //= fanout
    return "/" + "/".join(parts)


def safe_uri_segment(value: Any) -> str:
    text = str(value).strip().replace("/", "_")
    return "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in text) or "item"


def resource_uri(dataset: str, path: str, item_id: Optional[Any] = None) -> str:
    normalized = normalize_path(str(path or "/").split(";")[0])
    suffix = normalized.strip("/")
    base = f"viking://resources/bench/{safe_uri_segment(dataset)}"
    if suffix:
        base = f"{base}/{suffix}"
    if item_id is not None:
        base = f"{base}/{safe_uri_segment(item_id)}"
    return base


def deterministic_time(index: int) -> str:
    return f"2026-01-01T00:{(index // 60) % 60:02d}:{index % 60:02d}Z"


def ov_context_record(
    *,
    record_id: str,
    vector: list[float],
    uri: str,
    index: int,
    category: str = "",
    content: str = "",
) -> dict[str, Any]:
    timestamp = deterministic_time(index)
    label = category or "resource"
    return {
        "id": record_id,
        "uri": uri,
        "type": "file",
        "context_type": "resource",
        "vector": vector,
        "created_at": timestamp,
        "updated_at": timestamp,
        "active_count": 0,
        "level": 2,
        "name": safe_uri_segment(record_id),
        "description": label,
        "tags": label,
        "search_tags": [label],
        "abstract": f"source_id:{record_id} category:{label}",
        "content": content or f"benchmark content for {record_id} in {label}",
        "account_id": BENCH_ACCOUNT_ID,
        "owner_user_id": BENCH_USER_ID,
    }


def filter_segments(*, selectivity: float, fanout: int, depth: int) -> int:
    selectivity = min(1.0, max(1e-9, selectivity))
    fanout = max(2, fanout)
    segments = math.ceil(math.log(selectivity) / math.log(1.0 / fanout))
    return max(1, min(depth, segments))


def build_synthetic_workload(options: BenchOptions) -> Workload:
    rows = max(1, options.rows)
    dim = max(4, options.dim)
    segment_count = filter_segments(
        selectivity=options.filter_selectivity,
        fanout=options.path_fanout,
        depth=options.path_depth,
    )

    def records() -> Iterator[dict[str, Any]]:
        rng = random.Random(options.seed)
        for index in range(rows):
            record_path = synthetic_path(
                index, depth=options.path_depth, fanout=options.path_fanout
            )
            rid = f"syn-{index}"
            yield ov_context_record(
                record_id=rid,
                vector=synthetic_vector(rng, dim),
                uri=resource_uri("synthetic", record_path, rid),
                index=index,
                category=f"cat_{index % 16}",
            )

    query_rng = random.Random(options.seed)
    vectors = [synthetic_vector(query_rng, dim) for _ in range(rows)]
    query_count = max(1, min(options.queries, rows))
    cases: list[QueryCase] = []
    for qindex in range(query_count):
        record_index = (qindex * max(1, rows // query_count)) % rows
        full_path = synthetic_path(record_index, depth=options.path_depth, fanout=options.path_fanout)
        cases.append(
            QueryCase(
                query_id=f"syn-q{qindex}",
                vector=vectors[record_index],
                filter_path=resource_uri("synthetic", path_prefix(full_path, segment_count)),
                ground_truth_ids=[f"syn-{record_index}"],
            )
        )
    return Workload(
        name="synthetic",
        dim=dim,
        records=records,
        queries=cases,
        expected_rows=rows,
    )


def load_ground_truth(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            stripped = line.strip()
            if not stripped:
                continue
            if "\t" in stripped:
                parts = stripped.split("\t")
                if parts[0].lower() in {"query-id", "query_id"}:
                    continue
                if len(parts) >= 3 and is_number(parts[2]):
                    result.setdefault(str(parts[0]), []).append(str(parts[1]))
                    continue
                if len(parts) >= 2:
                    result[str(parts[0])] = [item for item in parts[1:] if item]
                    continue
            else:
                parts = stripped.replace(",", " ").split()
            if len(parts) < 2:
                continue
            # Plain-text ground-truth files omit query ids; line number is the query id.
            result[str(index)] = [item for item in parts if item]
    return result


def is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def load_query_records(path: Path, limit: Optional[int]) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return list(iter_json_records(path, limit))
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data[:limit] if isinstance(item, dict)] if limit else data
    if isinstance(data, dict):
        items = []
        for key, value in data.items():
            if limit is not None and len(items) >= limit:
                break
            if isinstance(value, dict):
                row = dict(value)
            else:
                row = {"constraint": value}
            row.setdefault("query_id", key)
            items.append(row)
        return items
    raise ValueError(f"unsupported query JSON shape in {path}")


def build_dir_vector_workload(options: BenchOptions) -> Workload:
    paths = dir_vector_paths(options.dataset_root, options.dataset)

    dim = read_fvecs_dim(paths["vectors"])
    total_rows = count_fvecs(paths["vectors"])
    total_queries = count_fvecs(paths["query_vectors"])
    row_limit = total_rows if options.full else min(options.rows, total_rows)
    query_limit = total_queries if options.full else min(options.queries, total_queries)
    sample_metadata = None if options.full else list(iter_json_records(paths["corpus"], row_limit))
    sampled_ids = (
        None
        if sample_metadata is None
        else {record_id(meta, index) for index, meta in enumerate(sample_metadata)}
    )

    def records() -> Iterator[dict[str, Any]]:
        vector_iter = iter_fvecs(paths["vectors"], row_limit)
        metadata_iter = (
            iter(sample_metadata)
            if sample_metadata is not None
            else iter_json_records(paths["corpus"], row_limit)
        )
        for index, (meta, vector) in enumerate(zip(metadata_iter, vector_iter)):
            rid = record_id(meta, index)
            path = extract_path(meta)
            category = str(pick_value(meta, ("category", "primary_category", "type")) or "")
            title = str(pick_value(meta, ("title", "name", "label")) or rid)
            yield ov_context_record(
                record_id=rid,
                vector=vector,
                uri=resource_uri(options.dataset, path, rid),
                index=index,
                category=category,
                content=title,
            )

    query_records = load_query_records(paths["queries"], query_limit if options.full else None)
    query_vectors = list(iter_fvecs(paths["query_vectors"], len(query_records)))
    ground_truth = load_ground_truth(paths["ground_truth"])
    covered_cases: list[QueryCase] = []
    fallback_cases: list[QueryCase] = []
    for index, (query_meta, vector) in enumerate(zip(query_records, query_vectors)):
        qid = record_id(query_meta, index)
        gt = [str(item) for item in (ground_truth.get(qid) or ground_truth.get(str(index)) or [])]
        if sampled_ids is not None:
            gt = [item for item in gt if item in sampled_ids]
        case = QueryCase(
            query_id=qid,
            vector=vector,
            filter_path=resource_uri(options.dataset, extract_path(query_meta)),
            ground_truth_ids=gt,
        )
        if gt or sampled_ids is None:
            covered_cases.append(case)
        else:
            fallback_cases.append(case)
    cases = covered_cases[:query_limit]
    if len(cases) < query_limit:
        cases.extend(fallback_cases[: query_limit - len(cases)])
    if not cases:
        raise ValueError("dir-vector query set is empty")
    return Workload(
        name=f"dir-vector:{options.dataset}",
        dim=dim,
        records=records,
        queries=cases,
        expected_rows=row_limit,
    )


def dir_vector_paths(dataset_root: Optional[Path], dataset: str) -> dict[str, Path]:
    if dataset_root is None:
        raise ValueError("--dataset-root is required for dir-vector workload")
    dataset_files = DIR_VECTOR_FILES[dataset]
    expected = ", ".join(dataset_files.values())
    if not dataset_root.exists():
        raise FileNotFoundError(
            f"--dataset-root does not exist: {dataset_root}\n"
            f"Download dir-vector data first: {DIR_VECTOR_DATASET_URL}\n"
            f"Expected --dataset {dataset} files in that directory: {expected}"
        )
    paths = {name: dataset_root / filename for name, filename in dataset_files.items()}
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "dir-vector dataset files missing:\n"
            + "\n".join(f"- {path}" for path in missing)
            + f"\nDownload dir-vector data first: {DIR_VECTOR_DATASET_URL}"
            + f"\nExpected --dataset {dataset} files: {expected}"
        )
    return paths


def build_schema(collection_name: str, dim: int) -> dict[str, Any]:
    return CollectionSchemas.context_collection(collection_name, dim)


def load_backend_config(options: BenchOptions, collection_name: Optional[str] = None):
    OpenVikingConfigSingleton.reset_instance()
    config = OpenVikingConfigSingleton.initialize(config_path=options.config)
    vectordb = config.storage.vectordb.model_copy(deep=True)
    if collection_name is not None:
        vectordb.name = collection_name
    return vectordb


def make_collection_name(config_name: str, run_id: str) -> str:
    base = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in (config_name or "context"))
    if not base or not base[0].isalpha():
        base = f"bench_{base}"
    suffix = "".join(ch if ch.isalnum() else "_" for ch in run_id)
    return f"{base}_bench_{suffix}"[:120]


def batched(iterable: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def sample_record_ids(workload: Workload, limit: int) -> list[str]:
    ids: list[str] = []
    for record in workload.records():
        value = record.get("id")
        if value is not None:
            ids.append(str(value))
        if len(ids) >= limit:
            break
    return ids


async def run_search_phase(
    *,
    backend: VikingVectorIndexBackend,
    ctx: RequestContext,
    phase: str,
    queries: list[QueryCase],
    top_k: int,
    concurrency: int,
    filtered: bool,
) -> tuple[list[Event], list[str], dict[str, Any]]:
    validation_errors: list[str] = []
    quality_hits = 0
    quality_total = 0

    async def one_query(query: QueryCase) -> tuple[Event, list[dict[str, Any]]]:
        target_directories = [query.filter_path] if filtered else None
        return await async_timed_event(
            phase,
            "targeted_search_in_tenant" if filtered else "search_in_tenant",
            lambda: backend.search_in_tenant(
                ctx=ctx,
                query_vector=query.vector,
                context_type="resource",
                target_directories=target_directories,
                level=[2],
                limit=top_k,
            ),
            extra={"query_id": query.query_id, "filter_path": query.filter_path if filtered else ""},
        )

    events: list[Event] = []
    results: list[tuple[QueryCase, list[dict[str, Any]]]] = []
    workers = max(1, concurrency)
    progress(f"{phase}: start queries={len(queries)} concurrency={workers} top_k={top_k}")
    started_at = time.perf_counter()
    last_reported_at = 0.0
    done = 0
    if workers == 1:
        for query in queries:
            event, rows = await one_query(query)
            events.append(event)
            results.append((query, rows or []))
            done += 1
            last_reported_at = progress_count(
                phase,
                done,
                len(queries),
                started_at,
                last_reported_at,
                force=done == len(queries),
            )
    else:
        async def tagged_query(query: QueryCase):
            event, rows = await one_query(query)
            return query, event, rows

        async for query, event, rows in map_bounded_as_completed(
            queries, tagged_query, workers
        ):
            events.append(event)
            results.append((query, rows or []))
            done += 1
            last_reported_at = progress_count(
                phase,
                done,
                len(queries),
                started_at,
                last_reported_at,
                force=done == len(queries),
            )

    for query, rows in results:
        if not rows:
            validation_errors.append(f"{phase}: query {query.query_id} returned no results")
            continue
        if filtered:
            leaked = [
                row.get("id")
                for row in rows
                if not path_matches(row.get("uri", ""), query.filter_path)
            ]
            if leaked:
                validation_errors.append(
                    f"{phase}: query {query.query_id} returned rows outside {query.filter_path}: {leaked[:5]}"
                )
        if query.ground_truth_ids:
            quality_total += 1
            if result_hits_ground_truth(rows, query.ground_truth_ids):
                quality_hits += 1

    quality = {
        "queries_with_ground_truth": quality_total,
        "hit_rate_at_k": (quality_hits / quality_total) if quality_total else None,
        "hits": quality_hits,
    }
    return events, validation_errors, quality


def result_hits_ground_truth(rows: list[dict[str, Any]], ground_truth_ids: list[str]) -> bool:
    returned_ids = {str(row.get("id")) for row in rows if row.get("id") is not None}
    return any(str(item) in returned_ids for item in ground_truth_ids)


def benchmark_context() -> RequestContext:
    return RequestContext(user=UserIdentifier(BENCH_ACCOUNT_ID, BENCH_USER_ID), role=Role.USER)


async def upsert_records(
    backend: VikingVectorIndexBackend, ctx: RequestContext, records: list[dict[str, Any]]
) -> list[str]:
    ids: list[str] = []
    for record in records:
        inserted = await backend.upsert(record, ctx=ctx)
        if inserted:
            ids.append(inserted)
    return ids


def run_benchmark(options: BenchOptions) -> RunResult:
    return asyncio.run(run_benchmark_async(options))


def selected_dir_vector_datasets(dataset: str) -> tuple[str, ...]:
    return DEFAULT_DIR_VECTOR_DATASETS if dataset == "all" else (dataset,)


def run_benchmark_suite(options: BenchOptions) -> list[tuple[BenchOptions, RunResult]]:
    if options.workload != "dir-vector" or options.dataset != "all":
        return [(options, run_benchmark(options))]

    datasets = selected_dir_vector_datasets(options.dataset)
    for dataset in datasets:
        dir_vector_paths(options.dataset_root, dataset)

    runs = []
    for dataset in datasets:
        child = replace(
            options,
            dataset=dataset,
            run_id=f"{options.run_id}_{dataset}",
            output_dir=options.output_dir / dataset,
        )
        runs.append((child, run_benchmark(child)))
    return runs


async def run_benchmark_async(options: BenchOptions) -> RunResult:
    workload = (
        build_synthetic_workload(options)
        if options.workload == "synthetic"
        else build_dir_vector_workload(options)
    )
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load once to discover the configured base name, then reload with the run-specific name.
    base_config = load_backend_config(options)
    collection_name = make_collection_name(base_config.name or "context", options.run_id)
    vectordb_config = load_backend_config(options, collection_name)
    if options.distance:
        vectordb_config.distance_metric = options.distance
    vectordb_config.dimension = workload.dim
    backend = VikingVectorIndexBackend(config=vectordb_config)
    ctx = benchmark_context()

    progress(
        "start "
        f"run_id={options.run_id} workload={workload.name} mode={options.mode} "
        f"records={workload.expected_rows} queries={len(workload.queries)} dim={workload.dim}"
    )
    progress(f"collection={collection_name}")

    events: list[Event] = []
    validation_errors: list[str] = []
    quality: dict[str, Any] = {}
    kept_collection = True
    inserted = 0
    fetch_ids: list[str] = []

    if options.mode != "read-only":
        progress("setup: create collection")
        event, created = await async_timed_event(
            "setup",
            "create_collection",
            lambda: backend.create_collection(
                collection_name,
                build_schema(collection_name, workload.dim),
            ),
        )
        events.append(event)
        if not event.success:
            validation_errors.append(event.error or "create_collection failed")
            await close_backend(backend)
            return finalize_result(
                options, workload, collection_name, events, validation_errors, quality, True
            )
        if created is False:
            validation_errors.append(f"collection already exists: {collection_name}")
            await close_backend(backend)
            return finalize_result(
                options, workload, collection_name, events, validation_errors, quality, True
            )
        progress("setup: collection ready")

        batch_size = max(1, options.batch_size)
        progress(f"ingest: start batch_size={batch_size}")
        ingest_started_at = time.perf_counter()
        last_ingest_reported_at = 0.0
        for batch in batched(workload.records(), batch_size):
            if len(fetch_ids) < 20:
                fetch_ids.extend(str(row["id"]) for row in batch[: 20 - len(fetch_ids)])
            event, ids = await async_timed_event(
                "ingest",
                "upsert_rows_serial",
                lambda batch=batch: upsert_records(backend, ctx, batch),
                count=len(batch),
            )
            events.append(event)
            if not event.success:
                validation_errors.append(event.error or "upsert_rows_serial failed")
                break
            inserted += len(ids or [])
            last_ingest_reported_at = progress_count(
                "ingest",
                inserted,
                workload.expected_rows,
                ingest_started_at,
                last_ingest_reported_at,
                force=inserted == workload.expected_rows,
            )

    if options.mode != "read-only" and inserted == 0:
        validation_errors.append("no records inserted")
        await close_backend(backend)
        return finalize_result(
            options,
            workload,
            collection_name,
            events,
            validation_errors,
            quality,
            kept_collection=True,
        )

    if options.mode == "write-only":
        if options.drop_at_end:
            progress("cleanup: drop collection")
            event, dropped = await async_timed_event(
                "cleanup", "drop_collection", lambda: backend.drop_collection()
            )
            events.append(event)
            kept_collection = not bool(dropped)
            if not event.success:
                validation_errors.append(event.error or "drop_collection failed")
        await close_backend(backend)
        return finalize_result(
            options,
            workload,
            collection_name,
            events,
            validation_errors,
            quality,
            kept_collection=kept_collection,
        )

    if options.mode == "read-only":
        fetch_ids = sample_record_ids(workload, 20)

    progress("validate: count/get")
    event, count_result = await async_timed_event(
        "validate", "count_all", lambda: backend.count(ctx=ctx)
    )
    events.append(event)
    if event.success and workload.expected_rows is not None and int(count_result or 0) < inserted:
        validation_errors.append(f"count_all={count_result} smaller than inserted={inserted}")
    elif not event.success:
        validation_errors.append(event.error or "count_all failed")

    event, fetched = await async_timed_event(
        "validate", "fetch_by_ids", lambda: backend.get(fetch_ids, ctx=ctx), count=len(fetch_ids)
    )
    events.append(event)
    if not event.success:
        validation_errors.append(event.error or "fetch_by_ids failed")
    elif len(fetched or []) != len(fetch_ids):
        validation_errors.append(f"fetch_by_ids returned {len(fetched or [])}, expected {len(fetch_ids)}")

    selected_queries = workload.queries
    search_events, errors, vector_quality = await run_search_phase(
        backend=backend,
        ctx=ctx,
        phase="vector_search",
        queries=selected_queries,
        top_k=options.top_k,
        concurrency=options.concurrency,
        filtered=False,
    )
    events.extend(search_events)
    validation_errors.extend(errors)
    quality["vector_search"] = vector_quality

    filtered_events, errors, filtered_quality = await run_search_phase(
        backend=backend,
        ctx=ctx,
        phase="filtered_vector_search",
        queries=selected_queries,
        top_k=options.top_k,
        concurrency=options.concurrency,
        filtered=True,
    )
    events.extend(filtered_events)
    validation_errors.extend(errors)
    quality["filtered_vector_search"] = filtered_quality

    first_filter = selected_queries[0].filter_path
    event, _ = await async_timed_event(
        "validate",
        "count_filtered",
        lambda: backend.count(PathScope("uri", first_filter, depth=-1), ctx=ctx),
        extra={"filter_path": first_filter},
    )
    events.append(event)
    if not event.success:
        validation_errors.append(event.error or "count_filtered failed")

    if options.drop_at_end:
        progress("cleanup: drop collection")
        event, dropped = await async_timed_event(
            "cleanup", "drop_collection", lambda: backend.drop_collection()
        )
        events.append(event)
        kept_collection = not bool(dropped)
        if not event.success:
            validation_errors.append(event.error or "drop_collection failed")

    await close_backend(backend)
    return finalize_result(
        options,
        workload,
        collection_name,
        events,
        validation_errors,
        quality,
        kept_collection=kept_collection,
    )


async def close_backend(backend: VikingVectorIndexBackend) -> None:
    try:
        await backend.close()
    except Exception:
        pass


def finalize_result(
    options: BenchOptions,
    workload: Workload,
    collection_name: str,
    events: list[Event],
    validation_errors: list[str],
    quality: dict[str, Any],
    kept_collection: bool,
) -> RunResult:
    return RunResult(
        run_id=options.run_id,
        collection_name=collection_name,
        output_dir=str(options.output_dir),
        events=events,
        validation_errors=validation_errors,
        quality=quality,
        workload={
            "name": workload.name,
            "mode": options.mode,
            "workload": options.workload,
            "dataset": options.dataset if options.workload == "dir-vector" else "synthetic",
            "record_count": workload.expected_rows,
            "query_count": len(workload.queries),
            "vector_dim": workload.dim,
            "top_k": options.top_k,
            "full": options.full,
            "profile": options.profile,
        },
        environment=collect_environment(),
        kept_collection=kept_collection,
    )


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, math.ceil((pct / 100.0) * len(sorted_values)) - 1))
    return sorted_values[index]


def phase_summary_rows(events: list[Event]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Event]] = {}
    for event in events:
        grouped.setdefault((event.phase, event.operation), []).append(event)

    rows: list[dict[str, Any]] = []
    for (phase, operation), items in sorted(grouped.items()):
        latencies = [item.latency_ms for item in items]
        wall_ms = phase_wall_ms(items)
        count = sum(item.count or 1 for item in items)
        rows.append(
            {
                "phase": phase,
                "operation": operation,
                "events": len(items),
                "count": count,
                "wall_ms": round(wall_ms, 3),
                "success": sum(1 for item in items if item.success),
                "errors": sum(1 for item in items if not item.success),
                "avg_ms": round(sum(latencies) / len(latencies), 3),
                "p50_ms": round(percentile(latencies, 50), 3),
                "p95_ms": round(percentile(latencies, 95), 3),
                "p99_ms": round(percentile(latencies, 99), 3),
                "max_ms": round(max(latencies), 3),
                "throughput_per_sec": round((count / wall_ms) * 1000.0, 3)
                if wall_ms > 0
                else 0.0,
            }
        )
    return rows


def phase_wall_ms(events: list[Event]) -> float:
    starts: list[datetime] = []
    ends: list[datetime] = []
    for event in events:
        try:
            starts.append(datetime.fromisoformat(event.started_at))
            ends.append(datetime.fromisoformat(event.ended_at))
        except ValueError:
            continue
    if starts and ends:
        return max(0.0, (max(ends) - min(starts)).total_seconds() * 1000.0)
    return sum(event.latency_ms for event in events)


def collect_environment() -> dict[str, Any]:
    env: dict[str, Any] = {
        "observed_at": utc_now(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "pid": os.getpid(),
        "cpu_count": os.cpu_count(),
        "memory_bytes": total_memory_bytes(),
        "cgroup": read_cgroup_limits(),
        "gpu": nvidia_smi(),
        "remote_resources_verified": False,
    }
    return env


def total_memory_bytes() -> Optional[int]:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size)
    except (AttributeError, OSError, ValueError):
        return None


def read_int_file(path: Path) -> Optional[int | str]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw == "max":
        return "max"
    try:
        return int(raw.split()[0])
    except (ValueError, IndexError):
        return raw


def read_cgroup_limits() -> dict[str, Any]:
    return {
        "cpu_max": read_int_file(Path("/sys/fs/cgroup/cpu.max")),
        "memory_max": read_int_file(Path("/sys/fs/cgroup/memory.max")),
        "memory_current": read_int_file(Path("/sys/fs/cgroup/memory.current")),
    }


def nvidia_smi() -> list[dict[str, str]]:
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 4:
            rows.append(
                {
                    "name": parts[0],
                    "memory_total_mb": parts[1],
                    "memory_used_mb": parts[2],
                    "utilization_gpu_percent": parts[3],
                }
            )
    return rows


def write_outputs(result: RunResult, options: BenchOptions) -> None:
    output_dir = Path(result.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = phase_summary_rows(result.events)
    write_json(output_dir / "environment.json", result.environment)
    config_json = {
        **asdict(options),
        "output_dir": str(options.output_dir),
        "dataset_root": str(options.dataset_root) if options.dataset_root else None,
    }
    write_json(output_dir / "run_config.json", config_json)
    write_json(
        output_dir / "run_summary.json",
        {
            "run_id": result.run_id,
            "collection_name": result.collection_name,
            "kept_collection": result.kept_collection,
            "validation_errors": result.validation_errors,
            "workload": result.workload,
            "quality": result.quality,
            "phase_summary": summary_rows,
        },
    )
    write_jsonl(output_dir / "events.jsonl", [asdict(event) for event in result.events])
    write_csv(output_dir / "phase_summary.csv", summary_rows)
    write_text(output_dir / "summary_zh.md", build_markdown_report(result, summary_rows, options))


def write_suite_outputs(
    output_dir: Path, runs: list[tuple[BenchOptions, RunResult]]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_summaries = {
        run_options.dataset: phase_summary_rows(result.events) for run_options, result in runs
    }
    rows = [
        {
            "dataset": run_options.dataset,
            "mode": result.workload.get("mode"),
            "records": result.workload.get("record_count"),
            "inserted": metric_value(run_summaries[run_options.dataset], "ingest", "count"),
            "queries": result.workload.get("query_count"),
            "dim": result.workload.get("vector_dim"),
            "top_k": result.workload.get("top_k"),
            "vector_qps": metric_value(run_summaries[run_options.dataset], "vector_search", "throughput_per_sec"),
            "vector_recall": quality_rate(result.quality, "vector_search"),
            "filtered_qps": metric_value(
                run_summaries[run_options.dataset], "filtered_vector_search", "throughput_per_sec"
            ),
            "filtered_recall": quality_rate(result.quality, "filtered_vector_search"),
            "recall_scope": recall_scope(result),
            "summary": str(Path(result.output_dir) / "summary_zh.md"),
            "collection": result.collection_name,
            "errors": len(result.validation_errors),
            "kept_collection": result.kept_collection,
        }
        for run_options, result in runs
    ]
    write_json(
        output_dir / "run_summary.json",
        {
            "datasets": rows,
            "validation_errors": {
                run_options.dataset: result.validation_errors for run_options, result in runs
            },
        },
    )
    lines = [
        "# OpenViking Vector Backend 真实数据汇总",
        "",
        "本次 `dir-vector` 真实数据 benchmark 覆盖 `wiki` 和 `arxiv` 两个数据集。",
        "",
        markdown_table(rows),
        "",
    ]
    for run_options, result in runs:
        lines.extend(["", f"## {run_options.dataset} 明细", ""])
        detail = build_markdown_report(
            result, run_summaries[run_options.dataset], run_options
        ).strip().splitlines()
        for line in detail[1:]:
            lines.append("#" + line if line.startswith("#") else line)
    write_text(output_dir / "summary_zh.md", "\n".join(lines))


def metric_row(summary_rows: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    for row in summary_rows:
        if row.get("phase") == phase:
            return row
    return {}


def metric_value(summary_rows: list[dict[str, Any]], phase: str, key: str) -> Any:
    value = metric_row(summary_rows, phase).get(key)
    return "-" if value is None else value


def quality_rate(quality: dict[str, Any], phase: str) -> str:
    value = quality.get(phase, {}).get("hit_rate_at_k")
    if value is None:
        return "-"
    return f"{float(value) * 100.0:.2f}%"


def recall_scope(result: RunResult) -> str:
    if result.workload.get("workload") == "dir-vector" and not result.workload.get("full"):
        return "sampled_subset"
    return "official_full"


def workload_rows(result: RunResult, summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    workload = result.workload
    return [
        {
            "workload": workload.get("name"),
            "mode": workload.get("mode"),
            "dataset": workload.get("dataset"),
            "records": workload.get("record_count"),
            "inserted": metric_value(summary_rows, "ingest", "count"),
            "queries": workload.get("query_count"),
            "dim": workload.get("vector_dim"),
            "top_k": workload.get("top_k"),
            "full": workload.get("full"),
        }
    ]


def recall_qps_rows(
    result: RunResult, summary_rows: list[dict[str, Any]], options: BenchOptions
) -> list[dict[str, Any]]:
    recall_header = f"gt_recall@{options.top_k}"
    return [
        {
            "phase": "vector_search",
            "qps": metric_value(summary_rows, "vector_search", "throughput_per_sec"),
            "avg_ms": metric_value(summary_rows, "vector_search", "avg_ms"),
            "p95_ms": metric_value(summary_rows, "vector_search", "p95_ms"),
            "scope": recall_scope(result),
            recall_header: quality_rate(result.quality, "vector_search"),
        },
        {
            "phase": "filtered_vector_search",
            "qps": metric_value(summary_rows, "filtered_vector_search", "throughput_per_sec"),
            "avg_ms": metric_value(summary_rows, "filtered_vector_search", "avg_ms"),
            "p95_ms": metric_value(summary_rows, "filtered_vector_search", "p95_ms"),
            "scope": recall_scope(result),
            recall_header: quality_rate(result.quality, "filtered_vector_search"),
        },
    ]


def build_markdown_report(
    result: RunResult, summary_rows: list[dict[str, Any]], options: BenchOptions
) -> str:
    lines = [
        "# OpenViking Vector Backend 性能验收报告",
        "",
        f"- Run ID: `{result.run_id}`",
        f"- Workload: `{options.workload}`",
        f"- Mode: `{options.mode}`",
        f"- Collection: `{result.collection_name}`",
        f"- 功能错误: `{len(result.validation_errors)}`",
        f"- Collection 保留: `{result.kept_collection}`",
        "",
        "## 结论",
        "",
    ]
    if result.validation_errors:
        lines.append("功能验收未通过。性能慢只记录，以下功能错误会导致非零退出码：")
        for error in result.validation_errors[:20]:
            lines.append(f"- {error}")
    else:
        lines.append("功能验收通过。")

    lines.extend(
        [
            "",
            "## 数据规模",
            "",
            markdown_table(workload_rows(result, summary_rows)),
            "",
            "## 召回与 QPS",
            "",
            markdown_table(recall_qps_rows(result, summary_rows, options)),
            "",
            "gt_recall@K 按 query 的 ground truth 命中率统计；`sampled_subset` 只用于 smoke sanity，不代表官方全量 recall。官方 recall 请用 `--full`。",
            "",
            "## 性能汇总",
            "",
            markdown_table(summary_rows),
            "",
            "## 环境说明",
            "",
            f"- Platform: `{result.environment.get('platform')}`",
            f"- CPU count: `{result.environment.get('cpu_count')}`",
            f"- Memory bytes: `{result.environment.get('memory_bytes')}`",
            f"- GPU: `{result.environment.get('gpu') or 'not detected'}`",
            "- 远端/GPU 服务资源未由本 runner 验证；报告只记录 runner 本机观测信息。",
            "",
            "## 清理",
            "",
        ]
    )
    if result.kept_collection:
        lines.append(
            "默认保留测试 collection。需要清理时，用相同配置连接后删除 "
            f"`{result.collection_name}`。"
        )
    else:
        lines.append("本次运行已尝试删除测试 collection。")

    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            "- `run_summary.json`: 汇总数据",
            "- `events.jsonl`: 每次操作明细",
            "- `phase_summary.csv`: 阶段聚合",
            "- `environment.json`: 本机环境观测",
        ]
    )
    return "\n".join(lines) + "\n"


def markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "无数据。"
    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return "\n".join(lines)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def write_jsonl(path: Path, values: Iterable[Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: Optional[Sequence[str]] = None) -> BenchOptions:
    parser = argparse.ArgumentParser(description="Run OpenViking vector backend performance benchmark")
    parser.add_argument("--config", help="Path to ov.conf. Defaults to OpenViking config lookup.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="smoke")
    parser.add_argument(
        "--mode",
        choices=["read-write", "write-only", "read-only"],
        default="read-write",
        help="read-write creates, writes, and reads; write-only skips reads; read-only skips create/upsert",
    )
    parser.add_argument("--workload", choices=["synthetic", "dir-vector"], default="synthetic")
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument(
        "--dataset",
        choices=["all", *sorted(DIR_VECTOR_FILES)],
        default="all",
        help="dir-vector dataset; default runs wiki and arxiv",
    )
    parser.add_argument("--full", action="store_true", help="Run full dir-vector corpus/query set")
    parser.add_argument("--rows", type=int)
    parser.add_argument("--queries", type=int)
    parser.add_argument("--dim", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--path-depth", type=int)
    parser.add_argument("--path-fanout", type=int)
    parser.add_argument("--filter-selectivity", type=float)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--distance", choices=["ip", "l2", "cosine"])
    parser.add_argument("--drop-at-end", action="store_true", help="Drop benchmark collection")
    args = parser.parse_args(argv)

    if args.mode == "read-only" and args.drop_at_end:
        raise ValueError("--drop-at-end is not allowed with --mode read-only")

    defaults = PROFILE_DEFAULTS[args.profile]
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    output_dir = args.output_dir or Path("benchmark/results/vectordb_perf") / run_id
    return BenchOptions(
        config=args.config,
        output_dir=output_dir,
        run_id=run_id,
        profile=args.profile,
        mode=args.mode,
        workload=args.workload,
        dataset_root=args.dataset_root,
        dataset=args.dataset,
        full=args.full,
        rows=args.rows if args.rows is not None else defaults["rows"],
        queries=args.queries if args.queries is not None else defaults["queries"],
        dim=args.dim if args.dim is not None else defaults["dim"],
        batch_size=args.batch_size if args.batch_size is not None else defaults["batch_size"],
        concurrency=args.concurrency if args.concurrency is not None else defaults["concurrency"],
        top_k=args.top_k if args.top_k is not None else defaults["top_k"],
        path_depth=args.path_depth if args.path_depth is not None else defaults["path_depth"],
        path_fanout=args.path_fanout if args.path_fanout is not None else defaults["path_fanout"],
        filter_selectivity=args.filter_selectivity
        if args.filter_selectivity is not None
        else defaults["filter_selectivity"],
        seed=args.seed,
        distance=args.distance,
        drop_at_end=args.drop_at_end,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        options = parse_args(argv)
        runs = run_benchmark_suite(options)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for run_options, result in runs:
        write_outputs(result, run_options)
    if len(runs) > 1:
        write_suite_outputs(options.output_dir, runs)
        print(f"summary: {options.output_dir / 'summary_zh.md'}")
    else:
        print(f"summary: {Path(runs[0][1].output_dir) / 'summary_zh.md'}")
    return 1 if any(result.validation_errors for _, result in runs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
