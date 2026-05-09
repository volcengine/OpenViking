#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rerun failed/missing LongMemEval rows and backfill results in place"
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="One or more judged run CSV files to patch in place",
    )
    parser.add_argument(
        "--dataset",
        default="data/longmemeval_s_cleaned.json",
        help="Original LongMemEval dataset path",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Worker count for rerun run_eval.py, default: 4",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-question timeout in seconds for rerun run_eval.py, default: 600",
    )
    parser.add_argument(
        "--judge-parallel",
        type=int,
        default=5,
        help="Parallel request count for judge.py, default: 5",
    )
    parser.add_argument(
        "--include-missing",
        action="store_true",
        help="Also run dataset samples that are missing from the CSV",
    )
    parser.add_argument(
        "--bad-response-prefixes",
        nargs="*",
        default=["[TIMEOUT]", "[CMD ERROR]", "[PARSE ERROR]", "[SINGLE SEARCH ERROR]"],
        help=(
            "Response prefixes considered failed rows, default: "
            "[TIMEOUT] [CMD ERROR] [PARSE ERROR] [SINGLE SEARCH ERROR]"
        ),
    )
    parser.add_argument(
        "--bad-reasoning-prefixes",
        nargs="*",
        default=["[API ERROR]", "[JUDGE ERROR]"],
        help=(
            "Judge reasoning prefixes considered failed rows, default: "
            "[API ERROR] [JUDGE ERROR]"
        ),
    )
    parser.add_argument(
        "--single-search-context-limit",
        type=int,
        default=10,
        help="Number of retrieved memory files to read before rerank, default: 10",
    )
    return parser.parse_args()


def load_dataset_by_question_id(dataset_path: Path) -> dict[str, dict]:
    with dataset_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {sample.get("question_id", ""): sample for sample in data}


def load_rows(csv_path: Path) -> tuple[list[dict], list[str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    return rows, fieldnames


def failed_sample_ids(
    rows: list[dict],
    bad_response_prefixes: list[str],
    bad_reasoning_prefixes: list[str],
) -> list[str]:
    ids: list[str] = []
    for row in rows:
        response = row.get("response", "")
        reasoning = row.get("reasoning", "")
        result = row.get("result", "")
        is_bad_response = any(response.startswith(prefix) for prefix in bad_response_prefixes)
        is_bad_reasoning = any(
            reasoning.startswith(prefix) for prefix in bad_reasoning_prefixes
        )
        if (
            is_bad_response
            or is_bad_reasoning
            or response == ""
            or result.strip().upper() == "ERROR"
        ):
            sample_id = row.get("sample_id", "")
            if sample_id and sample_id not in ids:
                ids.append(sample_id)
    return ids


def write_subset_json(samples: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)


def run_cmd(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def backfill_rows(
    original_rows: list[dict],
    rerun_rows: list[dict],
    fieldnames: list[str],
    bad_response_prefixes: list[str],
    bad_reasoning_prefixes: list[str],
) -> list[dict]:
    rerun_by_sample_id = {row.get("sample_id", ""): row for row in rerun_rows}
    merged_rows: list[dict] = []
    for row in original_rows:
        sample_id = row.get("sample_id", "")
        response = row.get("response", "")
        reasoning = row.get("reasoning", "")
        result = row.get("result", "")
        is_bad_response = any(response.startswith(prefix) for prefix in bad_response_prefixes)
        is_bad_reasoning = any(
            reasoning.startswith(prefix) for prefix in bad_reasoning_prefixes
        )
        should_patch = (
            is_bad_response
            or is_bad_reasoning
            or response == ""
            or result.strip().upper() == "ERROR"
        )
        if should_patch and sample_id in rerun_by_sample_id:
            patched = dict(row)
            for key in fieldnames:
                if key in rerun_by_sample_id[sample_id]:
                    patched[key] = rerun_by_sample_id[sample_id][key]
            merged_rows.append(patched)
        else:
            merged_rows.append(row)
    return merged_rows


def write_rows(csv_path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def process_one_csv(
    csv_path: Path,
    dataset_by_qid: dict[str, dict],
    *,
    threads: int,
    timeout: int,
    judge_parallel: int,
    include_missing: bool,
    bad_response_prefixes: list[str],
    bad_reasoning_prefixes: list[str],
    single_search_context_limit: int,
) -> None:
    rows, fieldnames = load_rows(csv_path)
    sample_ids = failed_sample_ids(rows, bad_response_prefixes, bad_reasoning_prefixes)
    if include_missing:
        existing_ids = {row.get("sample_id", "") for row in rows}
        missing_ids = [sample_id for sample_id in dataset_by_qid if sample_id and sample_id not in existing_ids]
        sample_ids.extend(missing_ids)
    sample_ids = list(dict.fromkeys(sample_ids))
    if not sample_ids:
        print(f"{csv_path}: no failed or missing rows, skip")
        return

    print(f"{csv_path}: rerunning {len(sample_ids)} failed/missing rows")
    subset = [dataset_by_qid[sid] for sid in sample_ids if sid in dataset_by_qid]
    if len(subset) != len(sample_ids):
        missing = sorted(set(sample_ids) - {sample.get('question_id', '') for sample in subset})
        raise ValueError(f"Missing samples in dataset for {csv_path}: {missing}")

    with tempfile.TemporaryDirectory(prefix="longmemeval_timeout_rerun_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        subset_json = tmpdir_path / "timeout_subset.json"
        rerun_csv = tmpdir_path / "timeout_rerun.csv"

        write_subset_json(subset, subset_json)
        run_cmd(
            [
                sys.executable,
                "benchmark/longmemeval/openviking/run_eval.py",
                str(subset_json),
                "--output",
                str(rerun_csv),
                "--threads",
                str(threads),
                "--timeout",
                str(timeout),
                "--single-search-context-limit",
                str(single_search_context_limit),
            ]
        )
        run_cmd(
            [
                sys.executable,
                "benchmark/longmemeval/openviking/judge.py",
                "--input",
                str(rerun_csv),
                "--parallel",
                str(judge_parallel),
            ]
        )

        rerun_rows, rerun_fieldnames = load_rows(rerun_csv)
        missing_original_fields = [field for field in fieldnames if field not in rerun_fieldnames]
        if missing_original_fields:
            raise ValueError(
                f"Rerun CSV missing original fields for {csv_path}: "
                f"missing={missing_original_fields}, original={fieldnames}, rerun={rerun_fieldnames}"
            )

        merged_rows = backfill_rows(
            rows,
            rerun_rows,
            fieldnames,
            bad_response_prefixes,
            bad_reasoning_prefixes,
        )
        existing_after_patch = {row.get("sample_id", "") for row in merged_rows}
        for rerun_row in rerun_rows:
            sample_id = rerun_row.get("sample_id", "")
            if sample_id and sample_id not in existing_after_patch:
                merged_rows.append(rerun_row)
                existing_after_patch.add(sample_id)
        write_rows(csv_path, merged_rows, fieldnames)

    run_cmd(
        [
            sys.executable,
            "benchmark/longmemeval/openviking/stat_judge_result.py",
            "--input",
            str(csv_path),
        ]
    )


def main() -> int:
    args = parse_args()
    dataset_path = Path(args.dataset).expanduser()
    dataset_by_qid = load_dataset_by_question_id(dataset_path)
    for input_path in args.inputs:
        process_one_csv(
            Path(input_path),
            dataset_by_qid,
            threads=args.threads,
            timeout=args.timeout,
            judge_parallel=args.judge_parallel,
            include_missing=args.include_missing,
            bad_response_prefixes=args.bad_response_prefixes,
            bad_reasoning_prefixes=args.bad_reasoning_prefixes,
            single_search_context_limit=args.single_search_context_limit,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
