"""Run category annotation in rolling catalog batches.

This intentionally keeps OpenViking memory writes out of the loop. Each batch:
1. Builds a catalog from all prior valid annotations.
2. Renders the next batch of category requests with that catalog.
3. Runs the LLM annotation executor for the batch.
4. Adds the batch annotations to the next catalog input.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)[:180]


def _run(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            "command failed"
            f"\nreturncode={result.returncode}"
            f"\ncommand={' '.join(command)}"
            f"\nstdout={result.stdout[-4000:]}"
            f"\nstderr={result.stderr[-4000:]}"
        )
    output = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "{}"
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = {"stdout": result.stdout}
    return payload


def _batch_ranges(
    *,
    start_offset: int,
    end_offset: int,
    batch_size: int,
    warmup_count: int = 0,
) -> list[tuple[int, int]]:
    if start_offset < 0:
        raise ValueError("start_offset must be >= 0")
    if end_offset <= start_offset:
        raise ValueError("end_offset must be greater than start_offset")
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    if warmup_count < 0:
        raise ValueError("warmup_count must be >= 0")
    ranges: list[tuple[int, int]] = []
    offset = start_offset
    warmup_end = min(end_offset, start_offset + warmup_count)
    while offset < warmup_end:
        ranges.append((offset, 1))
        offset += 1
    while offset < end_offset:
        limit = min(batch_size, end_offset - offset)
        ranges.append((offset, limit))
        offset += limit
    return ranges


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--memory-type", default="trajectories")
    parser.add_argument("--seed-annotations", type=Path, action="append", default=[])
    parser.add_argument("--start-offset", type=int, required=True)
    parser.add_argument("--end-offset", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument(
        "--warmup-count",
        type=int,
        default=0,
        help="Annotate the first N items one by one so later batches can reuse the early catalog.",
    )
    parser.add_argument("--run-id-prefix", required=True)
    parser.add_argument("--schema-path", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=ROOT / "benchmark" / "tau2" / "result")
    parser.add_argument("--max-catalog-categories", type=int, default=80)
    parser.add_argument("--subject-text-limit", type=int, default=3500)
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--retry-count", type=int, default=2)
    parser.add_argument("--api-retry-count", type=int, default=3)
    parser.add_argument("--max-category-ratio", type=float, default=0.85)
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--hint", action="append", default=[])
    args = parser.parse_args()

    if args.max_category_ratio <= 0:
        raise SystemExit("--max-category-ratio must be > 0")

    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    catalog_root = output_root / "category_catalogs" / _safe_key(args.run_id_prefix)
    request_root = output_root / "category_requests"
    annotation_root = output_root / "category_annotations"
    summary_root = output_root / "category_batch_runs" / _safe_key(args.run_id_prefix)
    catalog_root.mkdir(parents=True, exist_ok=True)
    summary_root.mkdir(parents=True, exist_ok=True)

    annotation_files = [path.resolve() for path in args.seed_annotations]
    batches: list[dict[str, Any]] = []
    ranges = _batch_ranges(
        start_offset=args.start_offset,
        end_offset=args.end_offset,
        batch_size=args.batch_size,
        warmup_count=args.warmup_count,
    )

    for batch_index, (offset, limit) in enumerate(ranges, start=1):
        catalog_path: Path | None = None
        if annotation_files:
            catalog_path = catalog_root / f"catalog_before_b{batch_index:02d}_offset_{offset}.json"
            command = [
                sys.executable,
                str(ROOT / "benchmark/tau2/scripts/build_category_catalog.py"),
                "--output",
                str(catalog_path),
                "--run-id",
                f"{args.run_id_prefix}_catalog_before_b{batch_index:02d}",
            ]
            for path in annotation_files:
                command.extend(["--annotations", str(path)])
            _run(command)

        request_run_id = f"{args.run_id_prefix}_requests_b{batch_index:02d}_offset_{offset}_limit_{limit}"
        request_command = [
            sys.executable,
            str(ROOT / "benchmark/tau2/scripts/build_category_requests.py"),
            "--manifest",
            str(args.manifest.resolve()),
            "--workspace",
            str(args.workspace.resolve()),
            "--domain",
            args.domain,
            "--memory-type",
            args.memory_type,
            "--offset",
            str(offset),
            "--limit",
            str(limit),
            "--max-catalog-categories",
            str(args.max_catalog_categories),
            "--subject-text-limit",
            str(args.subject_text_limit),
            "--output-root",
            str(request_root),
            "--run-id",
            request_run_id,
        ]
        if catalog_path:
            request_command.extend(["--category-catalog", str(catalog_path)])
        for hint in args.hint:
            request_command.extend(["--hint", hint])
        request_summary = _run(request_command)

        annotation_run_id = f"{args.run_id_prefix}_annotations_b{batch_index:02d}_offset_{offset}_limit_{limit}"
        annotation_command = [
            sys.executable,
            str(ROOT / "benchmark/tau2/scripts/generate_category_annotations.py"),
            "--requests",
            str(request_root / _safe_key(request_run_id) / "category_extraction_requests.jsonl"),
            "--output-root",
            str(annotation_root),
            "--run-id",
            annotation_run_id,
            "--max-tokens",
            str(args.max_tokens),
            "--timeout-seconds",
            str(args.timeout_seconds),
            "--retry-count",
            str(args.retry_count),
            "--api-retry-count",
            str(args.api_retry_count),
        ]
        if args.schema_path:
            annotation_command.extend(["--schema-path", str(args.schema_path.resolve())])
        if args.resume_existing:
            annotation_command.append("--resume-existing")
        annotation_summary = _run(annotation_command)

        annotations_path = annotation_root / _safe_key(annotation_run_id) / "annotations.jsonl"
        annotation_files.append(annotations_path.resolve())
        batches.append(
            {
                "annotation_run_id": annotation_run_id,
                "annotations_path": _relative(annotations_path.resolve()),
                "catalog_path": _relative(catalog_path.resolve()) if catalog_path else None,
                "limit": limit,
                "offset": offset,
                "request_run_id": request_run_id,
                "request_summary": request_summary,
                "annotation_summary": annotation_summary,
            }
        )

    final_catalog_path = catalog_root / "final_category_catalog.json"
    final_command = [
        sys.executable,
        str(ROOT / "benchmark/tau2/scripts/build_category_catalog.py"),
        "--output",
        str(final_catalog_path),
        "--run-id",
        f"{args.run_id_prefix}_final_catalog",
    ]
    for path in annotation_files:
        final_command.extend(["--annotations", str(path)])
    _run(final_command)
    final_catalog = _load_json(final_catalog_path)
    annotation_count = int(final_catalog.get("source_annotation_count") or 0)
    category_count = int(final_catalog.get("category_count") or 0)
    category_ratio = category_count / annotation_count if annotation_count else 0.0
    compaction_status = "passed" if category_ratio <= args.max_category_ratio else "warning_high_category_ratio"

    summary = {
        "annotation_count": annotation_count,
        "batch_count": len(batches),
        "batches": batches,
        "category_count": category_count,
        "category_count_ratio": round(category_ratio, 6),
        "category_ratio_threshold": args.max_category_ratio,
        "catalog_compaction_status": compaction_status,
        "claim_boundary": "rolling_batch_category_annotation_no_openviking_state_mutation",
        "domain": args.domain,
        "end_offset": args.end_offset,
        "final_catalog_path": _relative(final_catalog_path.resolve()),
        "memory_type": args.memory_type,
        "run_id_prefix": args.run_id_prefix,
        "schema_version": "openviking_tau2_category_batch_annotation_run.v0",
        "seed_annotation_files": [_relative(path) for path in args.seed_annotations],
        "start_offset": args.start_offset,
        "status": "passed" if compaction_status == "passed" else "warning",
        "warmup_count": args.warmup_count,
    }
    summary_path = summary_root / "run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
