from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from vaka_utils import (
    DEFAULT_CASE_SIZE,
    DEFAULT_EVAL_SESSIONS,
    DEFAULT_INPUT,
    DEFAULT_MEMORY_SESSIONS,
    build_context_for_row,
    case_has_all_sessions,
    choose_response,
    choose_response_without_refs,
    flatten_case_rows,
    load_vaka_cases,
    max_global_session_id,
    parse_session_selector,
    present_session_ids,
    select_cases,
)


SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_OUTPUT = str(SCRIPT_DIR / "result" / "vaka_qa_result.csv")

FIELDNAMES = [
    "case_id",
    "case_index",
    "case_session_range",
    "item_id",
    "global_session_id",
    "local_session_id",
    "round",
    "question_index",
    "question",
    "standard_answer",
    "judge_standard",
    "answer",
    "answer_source",
    "category",
    "doc_base",
    "used_doc",
    "case_type",
    "model_ability",
    "command_abbility",
    "source_result_cmdfollow_check",
    "source_reason",
    "source_confidence",
    "source_request_id",
    "latency_time",
    "memory_context",
    "eval_history",
    "response",
    "response_without_ref",
    "result",
    "reasoning",
]


def _choose_expected(row: dict) -> tuple[str, str]:
    standard_answer = (row.get("standard_answer") or "").strip()
    if standard_answer:
        return standard_answer, "standard_answer"
    judge_standard = (row.get("judge_standard") or "").strip()
    if judge_standard:
        return judge_standard, "judge_standard"
    return "", ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare Vaka long-memory QA results from a Vaka multi-turn CSV"
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=DEFAULT_INPUT,
        help=f"Path to Vaka CSV file, default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Path to output result CSV, default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--case",
        default=None,
        help="Case selector: case_id or 1-based case number. Comma-separated is supported.",
    )
    parser.add_argument(
        "--case-size",
        type=int,
        default=DEFAULT_CASE_SIZE,
        help="Number of global session IDs per case, default: 10",
    )
    parser.add_argument(
        "--memory-sessions",
        default=DEFAULT_MEMORY_SESSIONS,
        help=f"Global session IDs used as memory context, default: {DEFAULT_MEMORY_SESSIONS}",
    )
    parser.add_argument(
        "--eval-sessions",
        default=DEFAULT_EVAL_SESSIONS,
        help=f"Global session IDs used as evaluation turns, default: {DEFAULT_EVAL_SESSIONS}",
    )
    parser.add_argument(
        "--answer-column",
        default="deepsearch_answer",
        help="CSV column containing the Vaka answer, default: deepsearch_answer",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Maximum number of eval rows to prepare, default: all",
    )
    parser.add_argument(
        "--no-include-eval-history",
        action="store_true",
        help="Do not include prior eval turns when judging a later eval row",
    )
    parser.add_argument(
        "--strict-complete-case",
        action="store_true",
        help="Fail if a selected case does not contain every local session 1-10",
    )
    args = parser.parse_args()

    if args.case_size <= 0:
        raise ValueError("--case-size must be positive")

    all_cases = load_vaka_cases(args.input, args.case_size)
    max_session = max_global_session_id(all_cases)
    memory_sessions = parse_session_selector(args.memory_sessions, max_session_id=max_session)
    eval_sessions = parse_session_selector(args.eval_sessions, max_session_id=max_session)
    all_rows = flatten_case_rows(all_cases)

    cases = select_cases(all_cases, args.case)

    output_rows: list[dict] = []
    for case in cases:
        if args.strict_complete_case and not case_has_all_sessions(case, args.case_size):
            present = ",".join(str(sid) for sid in sorted(present_session_ids(case)))
            raise ValueError(
                f"{case['case_id']} is incomplete. Present local sessions: {present}"
            )

        eval_rows = [
            row for row in case["rows"] if row["_global_session_id"] in eval_sessions
        ]
        eval_rows.sort(key=lambda row: row["_row_index"])
        for question_index, row in enumerate(eval_rows):
            if args.count is not None and len(output_rows) >= args.count:
                break

            response = choose_response(row, args.answer_column)
            response_without_ref = choose_response_without_refs(row, response)
            answer, answer_source = _choose_expected(row)
            memory_context, eval_history = build_context_for_row(
                all_rows,
                row,
                memory_sessions=memory_sessions,
                eval_sessions=eval_sessions,
                answer_column=args.answer_column,
                include_eval_history=not args.no_include_eval_history,
            )

            output_rows.append(
                {
                    "case_id": case["case_id"],
                    "case_index": case["case_index"],
                    "case_session_range": case["session_range"],
                    "item_id": row.get("item_id", ""),
                    "global_session_id": row["_global_session_id"],
                    "local_session_id": row["_local_session_id"],
                    "round": row.get("round", ""),
                    "question_index": question_index,
                    "question": row.get("query", ""),
                    "standard_answer": row.get("standard_answer", ""),
                    "judge_standard": row.get("judge_standard", ""),
                    "answer": answer,
                    "answer_source": answer_source,
                    "category": row.get("type", ""),
                    "doc_base": row.get("doc_base", ""),
                    "used_doc": row.get("used_doc", ""),
                    "case_type": row.get("case_type", ""),
                    "model_ability": row.get("model_ability", ""),
                    "command_abbility": row.get("command_abbility", ""),
                    "source_result_cmdfollow_check": row.get("result_cmdfollow_check", ""),
                    "source_reason": row.get("reason", ""),
                    "source_confidence": row.get("confidence", ""),
                    "source_request_id": row.get("deepsearch_request_id", ""),
                    "latency_time": row.get("latency_time", ""),
                    "memory_context": memory_context,
                    "eval_history": eval_history,
                    "response": response,
                    "response_without_ref": response_without_ref,
                    "result": "",
                    "reasoning": "",
                }
            )

        if args.count is not None and len(output_rows) >= args.count:
            break

    output_path = Path(args.output).expanduser()
    output_dir = output_path.parent
    if str(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Loaded {len(cases)} case(s) from {args.input}")
    print(f"Prepared {len(output_rows)} eval row(s)")
    print(f"Result CSV saved to {output_path}")


if __name__ == "__main__":
    main()
