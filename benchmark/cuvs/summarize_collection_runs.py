#!/usr/bin/env python3
"""Aggregate independent OpenViking collection benchmark processes."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

BENCHMARK_DIR = Path(__file__).resolve().parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

from summarize_index_runs import canonical, metric_summary, nested_value  # noqa: E402

DATASET_FIELDS = ("kind", "vector_count", "dimension", "query_count", "metric", "seed")
BACKEND_METRICS = {
    "ingest_seconds": ("ingest", "total_seconds"),
    "ingest_records_per_second": ("ingest", "records_per_second"),
    "rss_ingest_delta_bytes": ("rss_ingest_delta_bytes",),
    "gpu_search_delta_bytes": ("gpu_search_delta_bytes",),
}
PREBUILD_SELECTIVE_METRICS = {
    "latency_ms": ("latency_ms",),
    "result_count": ("result_count",),
    "gpu_delta_bytes": ("gpu_delta_bytes",),
}
SEARCH_METRICS = {
    "first_query_ms": ("first_query_ms",),
    "warm_p50_ms": ("search", "latency_ms", "p50"),
    "warm_p95_ms": ("search", "latency_ms", "p95"),
    "warm_p99_ms": ("search", "latency_ms", "p99"),
    "qps": ("search", "qps"),
    "recall_at_k": ("recall_at_k",),
}
UPDATE_METRICS = {
    "write_seconds": ("write_wall_seconds",),
    "next_query_ms": ("next_query_ms",),
    "warm_query_ms": ("warm_query_ms",),
}
DELETE_METRICS = {
    "write_seconds": ("write_seconds",),
    "next_query_ms": ("next_query_ms",),
    "warm_query_ms": ("warm_query_ms",),
}
RESTART_METRICS = {
    "close_seconds": ("close_seconds",),
    "adapter_construct_seconds": ("adapter_construct_seconds",),
    "first_query_ms": ("first_query_ms",),
    "warm_query_ms": ("warm_query_ms",),
}
BACKEND_ORDER = {"native": 0, "cuvs_brute_force": 1, "auto_cuvs": 2}


def dataset_signature(document: dict[str, Any]) -> dict[str, Any]:
    dataset = document.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("Result document is missing dataset metadata")
    return {field: dataset.get(field) for field in DATASET_FIELDS}


def load_result(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read benchmark result {path}: {exc}") from exc
    if document.get("format_version") != 1:
        raise ValueError(f"Unsupported collection result format in {path}")
    if not isinstance(document.get("parameters"), dict):
        raise ValueError(f"Result document {path} is missing parameters")
    if not isinstance(document.get("results"), list) or not document["results"]:
        raise ValueError(f"Result document {path} has no backend results")
    return document


def keyed_results(document: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for result in document["results"]:
        backend = result.get("backend")
        if not isinstance(backend, str) or not backend:
            raise ValueError(f"Every result in {path} requires a non-empty backend")
        if backend in keyed:
            raise ValueError(f"Duplicate backend in {path}: {backend}")
        keyed[backend] = result
    return keyed


def summarize_metrics(
    documents: Sequence[dict[str, Any]], paths: dict[str, tuple[str, ...]]
) -> dict[str, dict[str, float | int | None]]:
    return {
        name: metric_summary(nested_value(document, path) for document in documents)
        for name, path in paths.items()
    }


def summarize_backend(backend: str, variants: Sequence[dict[str, Any]]) -> dict[str, Any]:
    prebuild_entries = [variant.get("prebuild_selective_query") for variant in variants]
    prebuild_selective_query = None
    if any(entry is not None for entry in prebuild_entries):
        if any(entry is None for entry in prebuild_entries):
            raise ValueError(f"Prebuild selective-query coverage differs for backend {backend}")
        entries = [entry for entry in prebuild_entries if entry is not None]
        reference = entries[0]
        metadata = {
            "name": reference.get("name"),
            "filter": reference.get("filter"),
            "distribution": reference.get("distribution"),
            "target_selectivity": reference.get("target_selectivity"),
        }
        if any(
            {
                "name": entry.get("name"),
                "filter": entry.get("filter"),
                "distribution": entry.get("distribution"),
                "target_selectivity": entry.get("target_selectivity"),
            }
            != metadata
            for entry in entries[1:]
        ):
            raise ValueError(f"Prebuild selective-query metadata differs for backend {backend}")
        prebuild_selective_query = {
            **metadata,
            "metrics": summarize_metrics(entries, PREBUILD_SELECTIVE_METRICS),
        }

    search_maps = [{item["name"]: item for item in variant["searches"]} for variant in variants]
    search_names = list(search_maps[0])
    for search_map in search_maps[1:]:
        if list(search_map) != search_names:
            raise ValueError(f"Search scenarios differ for backend {backend}")

    searches = []
    for name in search_names:
        entries = [search_map[name] for search_map in search_maps]
        reference = entries[0]
        metadata = {
            "filter": reference.get("filter"),
            "distribution": reference.get("distribution"),
            "target_selectivity": reference.get("target_selectivity"),
        }
        if any(
            {
                "filter": entry.get("filter"),
                "distribution": entry.get("distribution"),
                "target_selectivity": entry.get("target_selectivity"),
            }
            != metadata
            for entry in entries[1:]
        ):
            raise ValueError(f"Search scenario metadata differs for {backend}/{name}")
        searches.append(
            {"name": name, **metadata, "metrics": summarize_metrics(entries, SEARCH_METRICS)}
        )

    update_maps = [
        {int(item["count"]): item for item in variant["lifecycle"]["updates"]}
        for variant in variants
    ]
    update_counts = list(update_maps[0])
    if any(list(update_map) != update_counts for update_map in update_maps[1:]):
        raise ValueError(f"Mutation sizes differ for backend {backend}")
    updates = [
        {
            "count": count,
            "metrics": summarize_metrics(
                [update_map[count] for update_map in update_maps], UPDATE_METRICS
            ),
        }
        for count in update_counts
    ]
    return {
        "backend": backend,
        "metrics": summarize_metrics(variants, BACKEND_METRICS),
        "prebuild_selective_query": prebuild_selective_query,
        "searches": searches,
        "lifecycle": {
            "updates": updates,
            "delete": {
                "metrics": summarize_metrics(
                    [variant["lifecycle"]["delete"] for variant in variants], DELETE_METRICS
                )
            },
            "restart": {
                "metrics": summarize_metrics(
                    [variant["lifecycle"]["restart"] for variant in variants],
                    RESTART_METRICS,
                )
            },
        },
    }


def summarize_files(paths: Sequence[Path]) -> dict[str, Any]:
    if len(paths) < 2:
        raise ValueError("At least two independent result files are required")
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("Independent result files cannot be repeated")

    loaded = [(path, load_result(path)) for path in paths]
    reference_dataset = dataset_signature(loaded[0][1])
    reference_parameters = loaded[0][1]["parameters"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reference_backends: set[str] | None = None
    runs = []
    timestamps: set[str] = set()

    for path, document in loaded:
        if dataset_signature(document) != reference_dataset:
            raise ValueError(f"Dataset metadata differs in {path}")
        if document["parameters"] != reference_parameters:
            raise ValueError(f"Benchmark parameters differ in {path}")
        results = keyed_results(document, path)
        if reference_backends is None:
            reference_backends = set(results)
        elif set(results) != reference_backends:
            raise ValueError(f"Backends differ in {path}")
        for backend, result in results.items():
            grouped[backend].append(result)

        runtime = document.get("runtime", {})
        timestamp = runtime.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError(f"Result document {path} is missing a runtime timestamp")
        if timestamp in timestamps:
            raise ValueError(f"Duplicate process timestamp in {path}")
        timestamps.add(timestamp)
        runs.append(
            {
                "source": path.name,
                "timestamp": timestamp,
                "git_revision": runtime.get("git_revision"),
                "git_dirty": runtime.get("git_dirty"),
                "gpu_name": (runtime.get("gpu") or {}).get("name"),
                "cpu_model": runtime.get("cpu_model"),
                "cuvs": runtime.get("cuvs"),
                "cupy": runtime.get("cupy"),
            }
        )

    results = [
        summarize_backend(backend, variants)
        for backend, variants in sorted(
            grouped.items(), key=lambda item: (BACKEND_ORDER.get(item[0], 99), item[0])
        )
    ]
    runtime_fields = ("git_revision", "git_dirty", "gpu_name", "cpu_model", "cuvs", "cupy")
    return {
        "format_version": 1,
        "run_count": len(loaded),
        "dataset": reference_dataset,
        "parameters": reference_parameters,
        "runtime_consistency": {
            field: [json.loads(value) for value in sorted({canonical(run[field]) for run in runs})]
            for field in runtime_fields
        },
        "runs": runs,
        "results": results,
    }


def format_median_mad(metric: dict[str, Any], precision: int) -> str:
    if metric["median"] is None:
        return "n/a"
    return f"{metric['median']:.{precision}f} +/- {metric['mad']:.{precision}f}"


def print_summary(summary: dict[str, Any]) -> None:
    print(f"Independent runs: {summary['run_count']}")
    for result in summary["results"]:
        prebuild = result.get("prebuild_selective_query")
        if prebuild is not None:
            metrics = prebuild["metrics"]
            print(
                f"\n{result['backend']} prebuild selective query: "
                f"latency {format_median_mad(metrics['latency_ms'], 3)} ms, "
                f"GPU delta {format_median_mad(metrics['gpu_delta_bytes'], 0)} bytes"
            )
    print("\nbackend                 scenario              p50_ms med+/-MAD       qps med+/-MAD")
    print("----------------------  --------------------  ------------------  -----------------")
    for result in summary["results"]:
        for search in result["searches"]:
            metrics = search["metrics"]
            print(
                f"{result['backend']:<22}  {search['name']:<20}  "
                f"{format_median_mad(metrics['warm_p50_ms'], 3):>18}  "
                f"{format_median_mad(metrics['qps'], 1):>17}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Independent result JSON files")
    parser.add_argument("--output", type=Path, help="Write the aggregate JSON document")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        summary = summarize_files(args.inputs)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print_summary(summary)


if __name__ == "__main__":
    main()
