#!/usr/bin/env python3
"""Aggregate independent async vector-service benchmark processes."""

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
}
RESULT_METRICS = {
    "wall_seconds": ("wall_seconds",),
    "qps": ("qps",),
    "error_count": ("error_count",),
    "p50_ms": ("latency_ms", "p50"),
    "p95_ms": ("latency_ms", "p95"),
    "p99_ms": ("latency_ms", "p99"),
    "max_ms": ("latency_ms", "max"),
    "write_ms": ("write_ms",),
}
BACKEND_ORDER = {"native": 0, "cuvs_brute_force": 1}


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
        raise ValueError(f"Unsupported service result format in {path}")
    if not isinstance(document.get("parameters"), dict):
        raise ValueError(f"Result document {path} is missing parameters")
    if not isinstance(document.get("results"), list) or not document["results"]:
        raise ValueError(f"Result document {path} has no backend results")
    return document


def summarize_metrics(
    documents: Sequence[dict[str, Any]], paths: dict[str, tuple[str, ...]]
) -> dict[str, dict[str, float | int | None]]:
    return {
        name: metric_summary(nested_value(document, path) for document in documents)
        for name, path in paths.items()
    }


def keyed_backends(document: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for result in document["results"]:
        backend = result.get("backend")
        if not isinstance(backend, str) or not backend:
            raise ValueError(f"Every result in {path} requires a non-empty backend")
        if backend in keyed:
            raise ValueError(f"Duplicate backend in {path}: {backend}")
        keyed[backend] = result
    return keyed


def scenario_metadata(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        key: scenario.get(key)
        for key in ("filter_mode", "filter", "candidate_window")
        if key in scenario
    }


def summarize_backend(backend: str, variants: Sequence[dict[str, Any]]) -> dict[str, Any]:
    scenario_maps = [
        {scenario["name"]: scenario for scenario in variant["scenarios"]} for variant in variants
    ]
    scenario_names = list(scenario_maps[0])
    if any(list(scenario_map) != scenario_names for scenario_map in scenario_maps[1:]):
        raise ValueError(f"Scenarios differ for backend {backend}")

    scenarios = []
    for name in scenario_names:
        entries = [scenario_map[name] for scenario_map in scenario_maps]
        metadata = scenario_metadata(entries[0])
        if any(scenario_metadata(entry) != metadata for entry in entries[1:]):
            raise ValueError(f"Scenario metadata differs for {backend}/{name}")
        result_maps = [
            {int(result["concurrency"]): result for result in entry["results"]} for entry in entries
        ]
        concurrency_levels = list(result_maps[0])
        if any(list(result_map) != concurrency_levels for result_map in result_maps[1:]):
            raise ValueError(f"Concurrency levels differ for {backend}/{name}")
        scenario_summary = {
            "name": name,
            **metadata,
            "results": [
                {
                    "concurrency": concurrency,
                    "metrics": summarize_metrics(
                        [result_map[concurrency] for result_map in result_maps],
                        RESULT_METRICS,
                    ),
                }
                for concurrency in concurrency_levels
            ],
        }
        initial_values = [entry.get("initial_filter_query_ms") for entry in entries]
        if any(value is not None for value in initial_values):
            scenario_summary["initial_filter_query_ms"] = metric_summary(initial_values)
        scenarios.append(scenario_summary)
    return {
        "backend": backend,
        "metrics": summarize_metrics(variants, BACKEND_METRICS),
        "scenarios": scenarios,
    }


def summarize_files(paths: Sequence[Path]) -> dict[str, Any]:
    if len(paths) < 2:
        raise ValueError("At least two independent result files are required")
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("Independent result files cannot be repeated")

    loaded = [(path, load_result(path)) for path in paths]
    reference_dataset = dataset_signature(loaded[0][1])
    reference_parameters = loaded[0][1]["parameters"]
    reference_backends: set[str] | None = None
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    runs = []
    timestamps: set[str] = set()

    for path, document in loaded:
        if dataset_signature(document) != reference_dataset:
            raise ValueError(f"Dataset metadata differs in {path}")
        if document["parameters"] != reference_parameters:
            raise ValueError(f"Benchmark parameters differ in {path}")
        backends = keyed_backends(document, path)
        if reference_backends is None:
            reference_backends = set(backends)
        elif set(backends) != reference_backends:
            raise ValueError(f"Backends differ in {path}")
        for backend, result in backends.items():
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
    print(
        "\nbackend                 scenario                         conc   p50_ms med+/-MAD       p99_ms med+/-MAD       qps med+/-MAD"
    )
    print(
        "----------------------  -------------------------------  ----  ------------------  ------------------  -----------------"
    )
    for backend in summary["results"]:
        for scenario in backend["scenarios"]:
            for result in scenario["results"]:
                metrics = result["metrics"]
                print(
                    f"{backend['backend']:<22}  {scenario['name']:<31}  "
                    f"{result['concurrency']:>4}  "
                    f"{format_median_mad(metrics['p50_ms'], 3):>18}  "
                    f"{format_median_mad(metrics['p99_ms'], 3):>18}  "
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
