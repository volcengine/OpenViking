from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

TOKEN_GROUPS = {
    "QA": [
        "qa_input_tokens",
        "qa_output_tokens",
        "qa_cache_read_tokens",
        "qa_cache_write_tokens",
        "qa_total_tokens",
    ],
}
HERMES_USAGE_KEYS = [
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "api_call_count",
    "tool_call_count",
]


def require_suite(value: str) -> str:
    if value not in {"baseline", "e2e", "preingest"}:
        raise argparse.ArgumentTypeError("suite must be one of: baseline, e2e, preingest")
    return value


def result_dir_name(suite: str) -> str:
    if suite == "baseline":
        return "result_baseline"
    if suite == "e2e":
        return "result_e2e"
    return "result_preingest"


def summary_title(suite: str) -> str:
    if suite == "baseline":
        return "Hermes Baseline"
    if suite == "e2e":
        return "Hermes OpenViking E2E"
    return "Hermes + OpenViking (pre-ingest)"


def read_int(row: dict, key: str) -> int:
    try:
        return int(float(row.get(key, 0) or 0))
    except (ValueError, TypeError):
        return 0


def read_float(row: dict, key: str) -> float:
    try:
        return float(row.get(key, 0) or 0)
    except (ValueError, TypeError):
        return 0.0


def format_token_block(rows: list[dict], label: str, keys: list[str]) -> list[str]:
    totals = dict.fromkeys(keys, 0)
    for row in rows:
        for key in keys:
            totals[key] += read_int(row, key)

    count = len(rows) or 1
    return [
        f"{label} tokens:",
        *(f"  {key}: {totals[key]:,}" for key in keys),
        *(f"  avg_{key}: {totals[key] / count:,.2f}" for key in keys),
    ]


def read_true_token_csv(csv_path: Path) -> tuple[int, int, int, int]:
    if not csv_path.exists():
        return 0, 0, 0, 0
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0, 0, 0, 0
    last_row = rows[-1]
    return (
        read_int(last_row, "embedding_input_tokens"),
        read_int(last_row, "embedding_output_tokens"),
        read_int(last_row, "vlm_llm_input_tokens"),
        read_int(last_row, "vlm_llm_output_tokens"),
    )


def resolve_hermes_state_db(value: str | None) -> Path | None:
    if value:
        path = Path(value).expanduser()
        return path if path.exists() else None
    hermes_home = os.getenv("HERMES_HOME", "").strip()
    if hermes_home:
        path = Path(hermes_home).expanduser() / "state.db"
        return path if path.exists() else None
    return None


def read_hermes_sessions(state_db: Path | None) -> dict[str, dict[str, int]]:
    if state_db is None:
        return {}
    try:
        conn = sqlite3.connect(state_db)
    except sqlite3.Error:
        return {}
    try:
        available = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "id" not in available:
            return {}
        columns = ["id"] + [key for key in HERMES_USAGE_KEYS if key in available]
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM sessions").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

    sessions: dict[str, dict[str, int]] = {}
    for row in rows:
        session_id = str(row[0])
        usage = dict.fromkeys(HERMES_USAGE_KEYS, 0)
        for idx, key in enumerate(columns[1:], start=1):
            try:
                usage[key] = int(row[idx] or 0)
            except (TypeError, ValueError):
                usage[key] = 0
        sessions[session_id] = usage
    return sessions


def sum_hermes_usage(
    state_sessions: dict[str, dict[str, int]], session_ids: list[str]
) -> dict[str, int]:
    totals = dict.fromkeys(HERMES_USAGE_KEYS, 0)
    seen = set()
    for session_id in session_ids:
        if session_id in seen:
            continue
        seen.add(session_id)
        usage = state_sessions.get(session_id)
        if not usage:
            continue
        for key in HERMES_USAGE_KEYS:
            totals[key] += int(usage.get(key, 0) or 0)
    return totals


def hermes_total_tokens(usage: dict[str, int]) -> int:
    return (
        usage.get("input_tokens", 0)
        + usage.get("output_tokens", 0)
        + usage.get("cache_read_tokens", 0)
        + usage.get("cache_write_tokens", 0)
    )


def format_hermes_usage_block(
    label: str,
    usage: dict[str, int],
    matched_count: int,
    expected_count: int,
) -> list[str]:
    count = matched_count or 1
    lines = [
        "",
        f"{label}:",
        f"  matched_sessions: {matched_count:,}/{expected_count:,}",
        f"  total_tokens: {hermes_total_tokens(usage):,}",
    ]
    for key in HERMES_USAGE_KEYS:
        lines.append(f"  {key}: {usage.get(key, 0):,}")
    for key in HERMES_USAGE_KEYS:
        lines.append(f"  avg_{key}: {usage.get(key, 0) / count:,.2f}")
    return lines


def qa_state_session_ids(rows: list[dict], state_sessions: dict[str, dict[str, int]]) -> list[str]:
    matched = []
    for row in rows:
        for key in ("hermes_session_id", "conversation"):
            session_id = row.get(key, "")
            if session_id in state_sessions:
                matched.append(session_id)
                break
    return matched


def process_qa_results(
    input_path: str, suite: str, state_sessions: dict[str, dict[str, int]]
) -> tuple[list[str], int]:
    with open(input_path, "r", encoding="utf-8", newline="") as f:
        rows = [row for row in csv.DictReader(f) if str(row.get("category", "")) != "5"]

    correct = 0
    wrong = 0
    category_totals = defaultdict(int)
    category_correct = defaultdict(int)
    total_qa_latency = 0.0

    for row in rows:
        category = str(row.get("category", ""))
        category_totals[category] += 1
        if str(row.get("result", "")).upper() == "CORRECT":
            correct += 1
            category_correct[category] += 1
        elif str(row.get("result", "")).upper() == "WRONG":
            wrong += 1
        total_qa_latency += read_float(row, "qa_latency_sec")

    graded = correct + wrong
    accuracy = correct / graded if graded else 0.0
    csv_total_qa_tokens = sum(read_int(row, "qa_total_tokens") for row in rows)
    matched_state_ids = qa_state_session_ids(rows, state_sessions)
    state_usage = sum_hermes_usage(state_sessions, matched_state_ids) if matched_state_ids else {}
    state_total_qa_tokens = hermes_total_tokens(state_usage) if matched_state_ids else 0
    total_qa_tokens = (
        state_total_qa_tokens
        if matched_state_ids and len(matched_state_ids) == len(rows)
        else csv_total_qa_tokens
    )
    accuracy_per_1k = (correct / total_qa_tokens * 1000) if total_qa_tokens else 0.0
    avg_qa_latency = total_qa_latency / len(rows) if rows else 0.0

    output = [
        f"=== {summary_title(suite)} Summary ===",
        f"Total rows: {len(rows):,}",
        f"Graded rows: {graded:,}",
        f"Correct: {correct:,}",
        f"Wrong: {wrong:,}",
        f"Accuracy: {accuracy:.2%}",
        f"Accuracy per 1K QA tokens: {accuracy_per_1k:.4f}",
        f"Avg QA latency (sec): {avg_qa_latency:.4f}",
        "",
        "Category accuracy:",
    ]

    for category in sorted(
        category_totals.keys(), key=lambda value: int(value) if value.isdigit() else value
    ):
        total = category_totals[category]
        cat_correct = category_correct[category]
        cat_accuracy = cat_correct / total if total else 0.0
        output.append(f"  category {category}: {cat_correct}/{total} ({cat_accuracy:.2%})")

    if matched_state_ids:
        output.extend(
            format_hermes_usage_block(
                "Hermes state.db QA usage (authoritative when fully matched)",
                state_usage,
                len(set(matched_state_ids)),
                len(rows),
            )
        )

    for label, keys in TOKEN_GROUPS.items():
        output.append("")
        output.extend(format_token_block(rows, f"{label} CSV/gateway", keys))

    return output, total_qa_tokens


def import_state_session_ids(
    rows: list[dict],
    suite: str,
    state_sessions: dict[str, dict[str, int]],
) -> list[str]:
    if suite == "e2e":
        ids = [
            session_id
            for session_id in state_sessions
            if session_id.startswith("locomo-e2e-") and not session_id.startswith("locomo-e2e-qa-")
        ]
        if ids:
            return sorted(ids)
    if suite == "baseline":
        ids = [
            session_id
            for session_id in state_sessions
            if session_id.startswith("locomo-native-")
            and not session_id.startswith("locomo-native-qa-")
        ]
        if ids:
            return sorted(ids)
    return [
        row.get("conversation", "") for row in rows if row.get("conversation", "") in state_sessions
    ]


def summarize_import_success(
    import_csv: Path, suite: str, state_sessions: dict[str, dict[str, int]]
) -> tuple[list[str], int]:
    if not import_csv.exists():
        return [], 0

    with open(import_csv, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if suite == "baseline":
        total_tokens = sum(read_int(row, "total_tokens") for row in rows)
        output = [
            "",
            "Import CSV/gateway tokens:",
            f"  total_tokens: {total_tokens:,}",
        ]
        matched_ids = import_state_session_ids(rows, suite, state_sessions)
        if matched_ids:
            usage = sum_hermes_usage(state_sessions, matched_ids)
            output.extend(
                format_hermes_usage_block(
                    "Hermes state.db import usage (authoritative)",
                    usage,
                    len(set(matched_ids)),
                    len(set(matched_ids)),
                )
            )
            total_tokens = hermes_total_tokens(usage)
        return output, total_tokens

    if suite == "e2e":
        input_tokens = sum(read_int(row, "input_tokens") for row in rows)
        output_tokens = sum(read_int(row, "output_tokens") for row in rows)
        cache_read = sum(read_int(row, "cache_read") for row in rows)
        cache_write = sum(read_int(row, "cache_write") for row in rows)
        total_tokens = sum(read_int(row, "total_tokens") for row in rows)
        output = [
            "",
            "Import CSV/gateway tokens:",
            f"  input_tokens: {input_tokens:,}",
            f"  output_tokens: {output_tokens:,}",
            f"  cache_read_tokens: {cache_read:,}",
            f"  cache_write_tokens: {cache_write:,}",
            f"  total_tokens: {total_tokens:,}",
        ]
        matched_ids = import_state_session_ids(rows, suite, state_sessions)
        if matched_ids:
            usage = sum_hermes_usage(state_sessions, matched_ids)
            output.extend(
                format_hermes_usage_block(
                    "Hermes state.db import usage (authoritative)",
                    usage,
                    len(set(matched_ids)),
                    len(set(matched_ids)),
                )
            )
            total_tokens = hermes_total_tokens(usage)
        return output, total_tokens

    total_embedding = sum(read_int(row, "embedding_tokens") for row in rows)
    total_llm = sum(
        read_int(row, "llm_total_tokens") or read_int(row, "vlm_tokens") for row in rows
    )
    total_tokens = sum(read_int(row, "total_tokens") for row in rows)
    valid_rows = len(rows)
    avg_embedding = total_embedding / valid_rows if valid_rows else 0.0
    avg_llm = total_llm / valid_rows if valid_rows else 0.0
    avg_total = total_tokens / valid_rows if valid_rows else 0.0
    return [
        "",
        "OpenViking import token statistics:",
        f"  total_sessions: {valid_rows:,}",
        f"  total_embedding_tokens: {total_embedding:,}",
        f"  total_llm_tokens: {total_llm:,}",
        f"  total_tokens: {total_tokens:,}",
        f"  avg_embedding_tokens: {avg_embedding:,.2f}",
        f"  avg_llm_tokens: {avg_llm:,.2f}",
        f"  avg_total_tokens: {avg_total:,.2f}",
    ], total_tokens


def summarize_true_tokens(result_dir: Path) -> list[str]:
    import_emb_in, import_emb_out, import_vlm_in, import_vlm_out = read_true_token_csv(
        result_dir / "import_true_tokens.csv"
    )
    eval_emb_in, eval_emb_out, eval_vlm_in, eval_vlm_out = read_true_token_csv(
        result_dir / "eval_true_tokens.csv"
    )

    if not any(
        [
            import_emb_in,
            import_emb_out,
            import_vlm_in,
            import_vlm_out,
            eval_emb_in,
            eval_emb_out,
            eval_vlm_in,
            eval_vlm_out,
        ]
    ):
        return []

    return [
        "",
        "OpenViking true tokens:",
        f"  import_embedding_input_tokens: {import_emb_in:,}",
        f"  import_embedding_output_tokens: {import_emb_out:,}",
        f"  import_vlm_llm_input_tokens: {import_vlm_in:,}",
        f"  import_vlm_llm_output_tokens: {import_vlm_out:,}",
        f"  eval_embedding_input_tokens: {eval_emb_in:,}",
        f"  eval_embedding_output_tokens: {eval_emb_out:,}",
        f"  eval_vlm_llm_input_tokens: {eval_vlm_in:,}",
        f"  eval_vlm_llm_output_tokens: {eval_vlm_out:,}",
        f"  total_embedding_input_tokens: {import_emb_in + eval_emb_in:,}",
        f"  total_embedding_output_tokens: {import_emb_out + eval_emb_out:,}",
        f"  total_vlm_llm_input_tokens: {import_vlm_in + eval_vlm_in:,}",
        f"  total_vlm_llm_output_tokens: {import_vlm_out + eval_vlm_out:,}",
    ]


def main() -> None:
    script_dir = Path(__file__).parent.resolve()
    parser = argparse.ArgumentParser(description="Summarize shared Hermes LoCoMo judge results")
    parser.add_argument("--suite", type=require_suite, default="baseline", help="Benchmark suite")
    parser.add_argument("--input", default=None, help="Path to graded CSV")
    parser.add_argument("--import-csv", default=None, help="Path to import_success.csv")
    parser.add_argument(
        "--hermes-state-db",
        default=None,
        help="Path to Hermes state.db for authoritative token/cache accounting",
    )
    args = parser.parse_args()

    default_result_dir = script_dir / result_dir_name(args.suite)
    if args.input is None:
        args.input = str(default_result_dir / "qa_results.csv")
    result_dir = Path(args.input).expanduser().resolve().parent
    if args.import_csv is None:
        args.import_csv = str(result_dir / "import_success.csv")

    state_db = resolve_hermes_state_db(args.hermes_state_db)
    state_sessions = read_hermes_sessions(state_db)

    output_lines = []
    qa_total_tokens = 0

    if os.path.exists(args.input):
        qa_lines, qa_total_tokens = process_qa_results(args.input, args.suite, state_sessions)
        output_lines.extend(qa_lines)
    else:
        output_lines.append(f"Warning: QA result file not found: {args.input}")

    true_token_lines = summarize_true_tokens(result_dir)
    if true_token_lines:
        output_lines.extend(true_token_lines)

    import_lines, import_total_tokens = summarize_import_success(
        Path(args.import_csv), args.suite, state_sessions
    )
    if import_lines:
        output_lines.extend(import_lines)
        if args.suite in {"baseline", "e2e"}:
            output_lines.extend(
                [
                    "",
                    f"Grand Total Agent Tokens (Import + QA): {import_total_tokens + qa_total_tokens:,}",
                ]
            )

    for line in output_lines:
        print(line)


if __name__ == "__main__":
    main()
