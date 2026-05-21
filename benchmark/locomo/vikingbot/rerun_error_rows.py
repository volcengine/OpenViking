import argparse
import csv
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


csv.field_size_limit(sys.maxsize)

EVAL_ERROR_PATTERNS = [
    "[TIMEOUT]",
    "[CMD ERROR]",
    "[PARSE ERROR]",
    "[SINGLE SEARCH ERROR]",
    "Error calling LLM:",
    "RateLimitError",
    "qpm limit",
    "quota",
    "Quota",
]

JUDGE_ERROR_PATTERNS = [
    "[API ERROR]",
    "[PARSE ERROR]",
    "Request timed out",
    "Connection error",
    "Extra data",
    "Invalid \\escape",
]

ANY_ERROR_PATTERNS = EVAL_ERROR_PATTERNS + JUDGE_ERROR_PATTERNS


def _read_rows(path: str) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def _row_text(row: dict, fields: list[str] | None = None) -> str:
    fields = fields or ["response", "result", "reasoning"]
    return "\n".join(str(row.get(field, "") or "") for field in fields)


def _is_eval_error(row: dict) -> bool:
    response = str(row.get("response", "") or "")
    return not response.strip() or any(pattern in response for pattern in EVAL_ERROR_PATTERNS)


def _is_judge_error(row: dict) -> bool:
    if not str(row.get("result", "") or "").strip():
        return True
    text = _row_text(row, ["result", "reasoning"])
    return any(pattern in text for pattern in JUDGE_ERROR_PATTERNS)


def _is_bad_temp_row(row: dict) -> bool:
    text = _row_text(row)
    return not str(row.get("response", "") or "").strip() or any(
        pattern in text for pattern in ANY_ERROR_PATTERNS
    )


def _sample_number(sample_id: str) -> int:
    if not sample_id.startswith("sample_"):
        raise ValueError(f"Expected sample_{{idx}} sample_id, got {sample_id!r}")
    return int(sample_id.split("_", 1)[1])


def _temp_path(tmp_dir: Path, sample: int, question_index: int) -> Path:
    return tmp_dir / f"sample_{sample}_q{question_index}.csv"


def _temp_result_is_good(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        rows, _ = _read_rows(str(path))
    except Exception:
        return False
    return bool(rows) and not _is_bad_temp_row(rows[0])


def collect_eval_errors(input_path: str) -> list[tuple[int, int]]:
    rows, _ = _read_rows(input_path)
    items = []
    for row in rows:
        if _is_eval_error(row):
            items.append((_sample_number(row["sample_id"]), int(row["question_index"])))
    return items


def rerun_eval_errors(args: argparse.Namespace) -> None:
    tmp_dir = Path(args.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    needed = collect_eval_errors(args.input)
    remaining = [
        item
        for item in needed
        if not _temp_result_is_good(_temp_path(tmp_dir, item[0], item[1]))
    ]
    print(f"eval_error_rows={len(needed)} remaining={len(remaining)}")

    def run_one(item: tuple[int, int]) -> tuple[tuple[int, int], int, str]:
        sample, question_index = item
        output = _temp_path(tmp_dir, sample, question_index)
        env = os.environ.copy()
        if args.locomo_answer_prompt:
            env["VIKINGBOT_EVAL_LOCOMO_ANSWER_PROMPT"] = "1"
        else:
            env.pop("VIKINGBOT_EVAL_LOCOMO_ANSWER_PROMPT", None)
        if args.debug:
            env["VIKINGBOT_EVAL_DEBUG"] = "1"

        cmd = [
            sys.executable,
            "benchmark/locomo/vikingbot/run_eval.py",
            args.data,
            "--engine",
            args.engine,
            "--sample",
            str(sample),
            "--question-index",
            str(question_index),
            "--output",
            str(output),
            "--threads",
            "1",
        ]
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=args.timeout,
            env=env,
        )
        stderr_tail = (proc.stderr or "")[-1200:]
        return item, proc.returncode, stderr_tail

    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_one, item) for item in remaining]
        for index, future in enumerate(as_completed(futures), start=1):
            item, returncode, stderr_tail = future.result()
            status = "DONE" if returncode == 0 else "FAIL"
            print(f"{status} {index}/{len(remaining)} sample_{item[0]} q{item[1]}", flush=True)
            if returncode != 0:
                failures.append((item, returncode, stderr_tail))

    if failures:
        print("Failures:")
        for item, returncode, stderr_tail in failures[:20]:
            print(f"sample_{item[0]} q{item[1]} returncode={returncode}\n{stderr_tail}")
        raise SystemExit(1)


def merge_results(args: argparse.Namespace) -> None:
    rows, fieldnames = _read_rows(args.input)
    by_key = {(row["sample_id"], row["question_index"]): row for row in rows}
    updated = []
    skipped_bad = []

    for path in Path(args.tmp_dir).glob("sample_*_q*.csv"):
        temp_rows, _ = _read_rows(str(path))
        if not temp_rows:
            continue
        temp_row = temp_rows[0]
        if _is_bad_temp_row(temp_row):
            skipped_bad.append(str(path))
            continue
        key = (temp_row["sample_id"], temp_row["question_index"])
        if key not in by_key:
            continue
        target = by_key[key]
        for field in fieldnames:
            if field in temp_row:
                target[field] = temp_row[field]
        target["result"] = ""
        target["reasoning"] = ""
        updated.append(key)

    judge_cleared = []
    for row in rows:
        if _is_judge_error(row):
            row["result"] = ""
            row["reasoning"] = ""
            judge_cleared.append((row.get("sample_id", ""), row.get("question_index", "")))

    with open(args.input, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"updated_eval_rows={len(updated)}")
    print(f"cleared_judge_rows={len(set(judge_cleared))}")
    if skipped_bad:
        print(f"skipped_bad_temp_rows={len(skipped_bad)}")
        for path in skipped_bad[:20]:
            print(path)


def run_judge(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        "benchmark/locomo/vikingbot/judge.py",
        "--input",
        args.input,
        "--parallel",
        str(args.judge_parallel),
        "--engine",
        args.engine,
    ]
    if args.locomo_judge_prompt:
        cmd.append("--locomo-judge-prompt")
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rerun LoCoMo vikingbot eval error rows, merge them back, and optionally judge."
    )
    parser.add_argument(
        "stage",
        choices=["rerun", "merge", "judge", "all"],
        help="Which stage to run.",
    )
    parser.add_argument(
        "--input",
        default="result/locomo_vikingbot_eval_no_loop_locomo_prompt_sparse0.8.csv",
        help="CSV to repair.",
    )
    parser.add_argument("--data", default="result/locomo.json", help="LoCoMo JSON dataset path.")
    parser.add_argument(
        "--tmp-dir",
        default="result/locomo_sparse08_rerun_tmp",
        help="Directory for one-row rerun CSV files.",
    )
    parser.add_argument("--engine", default="vikingbot", choices=["vikingbot", "openviking"])
    parser.add_argument("--workers", type=int, default=4, help="Parallel eval rerun workers.")
    parser.add_argument("--timeout", type=int, default=900, help="Per-question rerun timeout.")
    parser.add_argument("--judge-parallel", type=int, default=16)
    parser.add_argument(
        "--no-locomo-answer-prompt",
        dest="locomo_answer_prompt",
        action="store_false",
        help="Do not set VIKINGBOT_EVAL_LOCOMO_ANSWER_PROMPT=1 during rerun.",
    )
    parser.add_argument(
        "--no-locomo-judge-prompt",
        dest="locomo_judge_prompt",
        action="store_false",
        help="Do not pass --locomo-judge-prompt during judge.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Set VIKINGBOT_EVAL_DEBUG=1 for rerun rows.",
    )
    parser.set_defaults(locomo_answer_prompt=True, locomo_judge_prompt=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.stage in {"rerun", "all"}:
        rerun_eval_errors(args)
    if args.stage in {"merge", "all"}:
        merge_results(args)
    if args.stage in {"judge", "all"}:
        run_judge(args)


if __name__ == "__main__":
    main()
