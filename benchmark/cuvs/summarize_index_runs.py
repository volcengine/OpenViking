#!/usr/bin/env python3
"""Aggregate independent cuVS index benchmark processes.

The index harness reports within-process latency distributions. This helper
combines several result files without treating their raw batches as one run,
so process-level medians and median absolute deviations remain visible.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

DATASET_FIELDS = (
    "kind",
    "name",
    "source_sha256",
    "vector_count",
    "dimension",
    "query_count",
    "metric",
    "seed",
)

METRIC_PATHS = {
    "build_seconds": ("build_seconds",),
    "first_search_per_query_ms": ("first_search_per_query_ms",),
    "warm_p50_ms": ("search", "per_query_latency_ms", "p50"),
    "warm_p95_ms": ("search", "per_query_latency_ms", "p95"),
    "warm_p99_ms": ("search", "per_query_latency_ms", "p99"),
    "qps": ("search", "qps"),
    "recall_at_k": ("recall_at_k",),
    "rss_delta_bytes": ("rss_delta_bytes",),
    "gpu_used_delta_bytes": ("gpu_used_delta_bytes",),
}

BACKEND_ORDER = {
    "native": 0,
    "cuvs_brute_force": 1,
    "cuvs_brute_force_fp16": 2,
    "cuvs_cagra": 3,
    "cuvs_cagra_fp16": 4,
}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def nested_value(document: dict[str, Any], path: Sequence[str]) -> float | None:
    value: Any = document
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Metric {'.'.join(path)} is not numeric")
    return float(value)


def metric_summary(values: Iterable[float | None]) -> dict[str, float | int | None]:
    present = [value for value in values if value is not None]
    if not present:
        return {"count": 0, "median": None, "mad": None, "min": None, "max": None}
    median = statistics.median(present)
    absolute_deviations = [abs(value - median) for value in present]
    return {
        "count": len(present),
        "median": median,
        "mad": statistics.median(absolute_deviations),
        "min": min(present),
        "max": max(present),
    }


def variant_key(result: dict[str, Any]) -> tuple[str, str]:
    backend = result.get("backend")
    if not isinstance(backend, str) or not backend:
        raise ValueError("Every result requires a non-empty backend")
    search_params = result.get("cagra_search_params")
    return backend, canonical(search_params)


def variant_sort_key(key: tuple[str, str]) -> tuple[Any, ...]:
    backend, encoded_search_params = key
    search_params = json.loads(encoded_search_params) or {}
    return (
        BACKEND_ORDER.get(backend, len(BACKEND_ORDER)),
        backend,
        search_params.get("itopk_size", 0),
        search_params.get("search_width", 0),
        encoded_search_params,
    )


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
    if document.get("format_version") != 2:
        raise ValueError(f"Unsupported result format in {path}")
    if not isinstance(document.get("parameters"), dict):
        raise ValueError(f"Result document {path} is missing parameters")
    if not isinstance(document.get("results"), list) or not document["results"]:
        raise ValueError(f"Result document {path} has no backend results")
    return document


def summarize_files(paths: Sequence[Path]) -> dict[str, Any]:
    if len(paths) < 2:
        raise ValueError("At least two independent result files are required")
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("Independent result files cannot be repeated")

    documents = [(path, load_result(path)) for path in paths]
    reference_dataset = dataset_signature(documents[0][1])
    reference_parameters = documents[0][1]["parameters"]
    reference_variants: set[tuple[str, str]] | None = None
    grouped_results: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    runs: list[dict[str, Any]] = []
    run_timestamps: set[str] = set()

    for path, document in documents:
        if dataset_signature(document) != reference_dataset:
            raise ValueError(f"Dataset metadata differs in {path}")
        if document["parameters"] != reference_parameters:
            raise ValueError(f"Benchmark parameters differ in {path}")

        current_variants: set[tuple[str, str]] = set()
        for result in document["results"]:
            key = variant_key(result)
            if key in current_variants:
                raise ValueError(f"Duplicate backend/search variant in {path}: {key[0]}")
            current_variants.add(key)
            grouped_results[key].append(result)
        if reference_variants is None:
            reference_variants = current_variants
        elif current_variants != reference_variants:
            raise ValueError(f"Backend/search variants differ in {path}")

        runtime = document.get("runtime", {})
        timestamp = runtime.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            raise ValueError(f"Result document {path} is missing a runtime timestamp")
        if timestamp in run_timestamps:
            raise ValueError(f"Duplicate process timestamp in {path}")
        run_timestamps.add(timestamp)
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

    results = []
    for (backend, encoded_search_params), variants in sorted(
        grouped_results.items(), key=lambda item: variant_sort_key(item[0])
    ):
        results.append(
            {
                "backend": backend,
                "cagra_search_params": json.loads(encoded_search_params),
                "metrics": {
                    name: metric_summary(nested_value(result, path) for result in variants)
                    for name, path in METRIC_PATHS.items()
                },
            }
        )

    runtime_values = {
        field: sorted({canonical(run.get(field)) for run in runs})
        for field in ("git_revision", "git_dirty", "gpu_name", "cpu_model", "cuvs", "cupy")
    }
    return {
        "format_version": 1,
        "run_count": len(documents),
        "dataset": reference_dataset,
        "parameters": reference_parameters,
        "runtime_consistency": {
            field: [json.loads(value) for value in values]
            for field, values in runtime_values.items()
        },
        "runs": runs,
        "results": results,
    }


def result_label(result: dict[str, Any]) -> str:
    label = result["backend"]
    search_params = result.get("cagra_search_params")
    if search_params:
        itopk_size = search_params.get("itopk_size", "auto")
        search_width = search_params.get("search_width", "auto")
        label = f"{label}[i={itopk_size},w={search_width}]"
    return label


def format_median_mad(metric: dict[str, Any], precision: int) -> str:
    if metric["median"] is None:
        return "n/a"
    return f"{metric['median']:.{precision}f} +/- {metric['mad']:.{precision}f}"


def print_summary(summary: dict[str, Any]) -> None:
    print(f"Independent runs: {summary['run_count']}")
    print(
        "\nbackend                       build_s med+/-MAD  first_ms med+/-MAD  "
        "p50_ms med+/-MAD       qps med+/-MAD  recall med+/-MAD"
    )
    print(
        "----------------------------  -----------------  ------------------  "
        "------------------  ---------------  ----------------"
    )
    for result in summary["results"]:
        metrics = result["metrics"]
        print(
            f"{result_label(result):<28}  "
            f"{format_median_mad(metrics['build_seconds'], 3):>17}  "
            f"{format_median_mad(metrics['first_search_per_query_ms'], 3):>18}  "
            f"{format_median_mad(metrics['warm_p50_ms'], 3):>18}  "
            f"{format_median_mad(metrics['qps'], 1):>15}  "
            f"{format_median_mad(metrics['recall_at_k'], 4):>16}"
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
