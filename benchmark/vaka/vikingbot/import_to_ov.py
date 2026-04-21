from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from vaka_utils import (
    DEFAULT_CASE_SIZE,
    DEFAULT_INPUT,
    DEFAULT_MEMORY_SESSIONS,
    choose_response,
    choose_response_without_refs,
    load_vaka_cases,
    max_global_session_id,
    parse_session_selector,
    select_cases,
)

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_RESULT_DIR = SCRIPT_DIR / "result"
DEFAULT_SUCCESS_CSV = str(DEFAULT_RESULT_DIR / "import_success.csv")
DEFAULT_ERROR_LOG = str(DEFAULT_RESULT_DIR / "import_errors.log")
DEFAULT_RECORD_PATH = str(DEFAULT_RESULT_DIR / ".ingest_record.json")
DEFAULT_USER_ID = "default"
DEFAULT_AGENT_ID = "default"

SUCCESS_FIELDNAMES = [
    "timestamp",
    "account",
    "user_id",
    "agent_id",
    "case_id",
    "case_session_range",
    "global_session_id",
    "local_session_id",
    "row_count",
    "used_docs",
    "embedding_tokens",
    "vlm_tokens",
    "llm_input_tokens",
    "llm_output_tokens",
    "total_tokens",
    "task_id",
    "trace_id",
]


def load_ingest_record(record_path: str) -> dict[str, Any]:
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ingest_record(record: dict[str, Any], record_path: str) -> None:
    Path(record_path).parent.mkdir(parents=True, exist_ok=True)
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)


def _identity_part(value: str | None) -> str:
    return value or ""


def ingest_key(
    *,
    account: str | None,
    user_id: str | None,
    agent_id: str | None,
    global_session_id: int | str,
) -> str:
    return (
        f"vaka:account={_identity_part(account)}:"
        f"user={_identity_part(user_id)}:"
        f"agent={_identity_part(agent_id)}:"
        f"session={global_session_id}"
    )


def ensure_success_csv_schema(success_csv: str) -> None:
    path = Path(success_csv)
    if not path.exists() or path.stat().st_size == 0:
        return

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        existing_fieldnames = list(reader.fieldnames or [])
        if all(field in existing_fieldnames for field in SUCCESS_FIELDNAMES):
            return
        rows = list(reader)

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUCCESS_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in SUCCESS_FIELDNAMES})
    temp_path.replace(path)


def load_success_keys(
    success_csv: str,
    *,
    account: str | None,
    user_id: str | None,
    agent_id: str | None,
) -> set[str]:
    keys: set[str] = set()
    if not Path(success_csv).exists():
        return keys
    ensure_success_csv_schema(success_csv)
    with open(success_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            global_session_id = row.get("global_session_id", "")
            if (
                global_session_id
                and row.get("account", "") == _identity_part(account)
                and row.get("user_id", "") == _identity_part(user_id)
                and row.get("agent_id", "") == _identity_part(agent_id)
            ):
                keys.add(
                    ingest_key(
                        account=account,
                        user_id=user_id,
                        agent_id=agent_id,
                        global_session_id=global_session_id,
                    )
                )
    return keys


def write_success_record(record: dict[str, Any], success_csv: str) -> None:
    Path(success_csv).parent.mkdir(parents=True, exist_ok=True)
    ensure_success_csv_schema(success_csv)
    file_exists = Path(success_csv).exists()
    with open(success_csv, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUCCESS_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": record["timestamp"],
                "account": record["account"],
                "user_id": record["user_id"],
                "agent_id": record["agent_id"],
                "case_id": record["case_id"],
                "case_session_range": record["case_session_range"],
                "global_session_id": record["global_session_id"],
                "local_session_id": record["local_session_id"],
                "row_count": record["row_count"],
                "used_docs": json.dumps(record.get("used_docs", []), ensure_ascii=False),
                "embedding_tokens": record["token_usage"].get("embedding", 0),
                "vlm_tokens": record["token_usage"].get("vlm", 0),
                "llm_input_tokens": record["token_usage"].get("llm_input", 0),
                "llm_output_tokens": record["token_usage"].get("llm_output", 0),
                "total_tokens": record["token_usage"].get("total", 0),
                "task_id": record.get("task_id", ""),
                "trace_id": record.get("trace_id", ""),
            }
        )


def write_error_record(record: dict[str, Any], error_log: str) -> None:
    Path(error_log).parent.mkdir(parents=True, exist_ok=True)
    with open(error_log, "a", encoding="utf-8") as f:
        f.write(
            f"[{record['timestamp']}] ERROR "
            f"[session_id={record.get('global_session_id', '')} "
            f"case={record['case_id']} local_session={record['local_session_id']}]: "
            f"{record['error']}\n"
        )


def is_already_ingested(
    account: str | None,
    user_id: str | None,
    agent_id: str | None,
    global_session_id: int,
    record: dict[str, Any],
    success_keys: set[str],
) -> bool:
    key = ingest_key(
        account=account,
        user_id=user_id,
        agent_id=agent_id,
        global_session_id=global_session_id,
    )
    return key in success_keys or bool(record.get(key, {}).get("success"))


def mark_ingested(
    account: str | None,
    user_id: str | None,
    agent_id: str | None,
    global_session_id: int,
    record: dict[str, Any],
    meta: dict[str, Any],
) -> None:
    key = ingest_key(
        account=account,
        user_id=user_id,
        agent_id=agent_id,
        global_session_id=global_session_id,
    )
    record[key] = {"success": True, "timestamp": int(time.time()), "meta": meta}


def _parse_token_usage(commit_result: dict[str, Any]) -> dict[str, int]:
    if "result" in commit_result:
        result = commit_result["result"]
        if "token_usage" in result:
            token_usage = result["token_usage"]
            embedding = token_usage.get("embedding", {})
            llm = token_usage.get("llm", {})
            embed_total = embedding.get("total", embedding.get("total_tokens", 0))
            llm_total = llm.get("total", llm.get("total_tokens", 0))
            return {
                "embedding": embed_total,
                "vlm": llm_total,
                "llm_input": llm.get("input", 0),
                "llm_output": llm.get("output", 0),
                "total": token_usage.get("total", {}).get("total_tokens", embed_total + llm_total),
            }

    telemetry = commit_result.get("telemetry", {}).get("summary", {})
    tokens = telemetry.get("tokens", {})
    return {
        "embedding": tokens.get("embedding", {}).get("total", 0),
        "vlm": tokens.get("llm", {}).get("total", 0),
        "llm_input": tokens.get("llm", {}).get("input", 0),
        "llm_output": tokens.get("llm", {}).get("output", 0),
        "total": tokens.get("total", 0),
    }


def build_session_messages(
    rows: list[dict[str, Any]],
    *,
    answer_column: str,
    keep_references: bool,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for row in rows:
        query = (row.get("query") or "").strip()
        if query:
            messages.append({"role": "user", "text": query})

        response = choose_response(row, answer_column)
        if not keep_references:
            response = choose_response_without_refs(row, response)
        if response:
            messages.append({"role": "assistant", "text": response})
    return messages


def build_case_sessions(
    case: dict[str, Any],
    *,
    memory_sessions: set[int],
    answer_column: str,
    keep_references: bool,
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    global_session_ids = {
        row["_global_session_id"]
        for row in case["rows"]
        if row["_global_session_id"] in memory_sessions
    }
    for global_session_id in sorted(global_session_ids):
        rows = [row for row in case["rows"] if row["_global_session_id"] == global_session_id]
        if not rows:
            continue
        local_session_id = rows[0]["_local_session_id"]
        messages = build_session_messages(
            rows,
            answer_column=answer_column,
            keep_references=keep_references,
        )
        if not messages:
            continue
        used_doc_values = {row.get("used_doc") or row.get("doc_base") or "" for row in rows}
        used_docs = sorted(doc for doc in used_doc_values if doc)
        sessions.append(
            {
                "messages": messages,
                "meta": {
                    "case_id": case["case_id"],
                    "case_session_range": case["session_range"],
                    "global_session_id": global_session_id,
                    "local_session_id": local_session_id,
                    "row_count": len(rows),
                    "used_docs": used_docs,
                },
            }
        )
    return sessions


async def viking_ingest(
    messages: list[dict[str, str]],
    *,
    openviking_url: str,
    account: str | None,
    user_id: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    try:
        import openviking as ov
    except ImportError as exc:
        raise RuntimeError(
            "openviking package is required. Run from the project environment, "
            "for example: uv run python benchmark/vaka/vikingbot/import_to_ov.py"
        ) from exc

    client = ov.AsyncHTTPClient(
        url=openviking_url, account=account, user=user_id, agent_id=agent_id
    )
    await client.initialize()
    try:
        create_res = await client.create_session()
        session_id = create_res["session_id"]
        for msg in messages:
            await client.add_message(
                session_id=session_id,
                role=msg["role"],
                parts=[{"type": "text", "text": msg["text"]}],
            )

        result = await client.commit_session(session_id, telemetry=True)
        if result.get("status") not in ("committed", "accepted"):
            raise RuntimeError(f"Commit failed: {result}")

        task_id = result.get("task_id")
        token_usage = {"embedding": 0, "vlm": 0, "llm_input": 0, "llm_output": 0, "total": 0}
        if task_id:
            for _ in range(1200):
                task = await client.get_task(task_id)
                status = task.get("status") if task else "unknown"
                if status == "completed":
                    token_usage = _parse_token_usage(task)
                    break
                if status in ("failed", "cancelled", "unknown"):
                    raise RuntimeError(f"Task {task_id} {status}: {task}")
                await asyncio.sleep(1)
            else:
                raise RuntimeError(f"Task {task_id} timed out")

        return {
            "token_usage": token_usage,
            "task_id": task_id,
            "trace_id": result.get("trace_id", ""),
        }
    finally:
        await client.close()


async def process_session(
    session: dict[str, Any],
    *,
    run_time: str,
    ingest_record: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    meta = session["meta"]
    case_id = meta["case_id"]
    global_session_id = meta["global_session_id"]
    local_session_id = meta["local_session_id"]
    try:
        user_id = None if args.no_user_agent_id else args.user_id
        agent_id = None if args.no_user_agent_id else args.agent_id
        result = await viking_ingest(
            session["messages"],
            openviking_url=args.openviking_url,
            account=args.account,
            user_id=user_id,
            agent_id=agent_id,
        )
        token_usage = result["token_usage"]
        record = {
            "timestamp": run_time,
            "account": _identity_part(args.account),
            "user_id": _identity_part(user_id),
            "agent_id": _identity_part(agent_id),
            "case_id": case_id,
            "case_session_range": meta["case_session_range"],
            "global_session_id": global_session_id,
            "local_session_id": local_session_id,
            "row_count": meta["row_count"],
            "used_docs": meta["used_docs"],
            "token_usage": token_usage,
            "task_id": result.get("task_id", ""),
            "trace_id": result.get("trace_id", ""),
        }
        write_success_record(record, args.success_csv)
        mark_ingested(args.account, user_id, agent_id, global_session_id, ingest_record, meta)
        save_ingest_record(ingest_record, args.record_path)
        print(
            f"    -> [COMPLETED] [session_id={global_session_id}] "
            f"user={_identity_part(user_id)} agent={_identity_part(agent_id)} "
            f"rows={meta['row_count']} total_tokens={token_usage.get('total', 0)}",
            file=sys.stderr,
        )
        return {"status": "success", **record}
    except Exception as exc:
        print(f"    -> [ERROR] [{case_id}/session_{local_session_id}] {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        record = {
            "timestamp": run_time,
            "case_id": case_id,
            "global_session_id": global_session_id,
            "local_session_id": local_session_id,
            "status": "error",
            "error": str(exc),
        }
        write_error_record(record, args.error_log)
        return record


async def run_import(args: argparse.Namespace) -> None:
    all_cases = load_vaka_cases(args.input, args.case_size)
    max_session = max_global_session_id(all_cases)
    memory_sessions = parse_session_selector(args.memory_sessions, max_session_id=max_session)
    cases = select_cases(all_cases, args.case)

    if args.clear_ingest_record:
        ingest_record: dict[str, Any] = {}
        save_ingest_record(ingest_record, args.record_path)
    else:
        ingest_record = load_ingest_record(args.record_path)

    user_id = None if args.no_user_agent_id else args.user_id
    agent_id = None if args.no_user_agent_id else args.agent_id
    success_keys = (
        set()
        if args.force_ingest
        else load_success_keys(
            args.success_csv,
            account=args.account,
            user_id=user_id,
            agent_id=agent_id,
        )
    )
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async def process_case(case: dict[str, Any]) -> list[dict[str, Any]]:
        print(
            f"\n=== {case['case_id']} (global sessions {case['session_range']}) ===",
            file=sys.stderr,
        )
        sessions = build_case_sessions(
            case,
            memory_sessions=memory_sessions,
            answer_column=args.answer_column,
            keep_references=args.keep_references,
        )
        print(f"    {len(sessions)} memory session(s) to import", file=sys.stderr)
        print(
            f"    target user={_identity_part(user_id)} agent={_identity_part(agent_id)}",
            file=sys.stderr,
        )

        results = []
        for session in sessions:
            meta = session["meta"]
            global_session_id = meta["global_session_id"]
            if not args.force_ingest and is_already_ingested(
                args.account,
                user_id,
                agent_id,
                global_session_id,
                ingest_record,
                success_keys,
            ):
                print(
                    f"    -> [SKIP] [session_id={global_session_id}] already imported",
                    file=sys.stderr,
                )
                results.append({"status": "skipped"})
                continue
            results.append(
                await process_session(
                    session,
                    run_time=run_time,
                    ingest_record=ingest_record,
                    args=args,
                )
            )
        return results

    case_results = []
    for case in cases:
        case_results.append(await process_case(case))
    flat_results = [item for group in case_results for item in group]
    success_count = sum(1 for item in flat_results if item.get("status") == "success")
    skipped_count = sum(1 for item in flat_results if item.get("status") == "skipped")
    error_count = sum(1 for item in flat_results if item.get("status") == "error")
    total_tokens = sum(
        int(item.get("token_usage", {}).get("total", 0))
        for item in flat_results
        if item.get("status") == "success"
    )

    print("\n=== Import summary ===", file=sys.stderr)
    print(f"Cases: {len(cases)}", file=sys.stderr)
    print(f"Successfully imported: {success_count}", file=sys.stderr)
    print(f"Skipped: {skipped_count}", file=sys.stderr)
    print(f"Failed: {error_count}", file=sys.stderr)
    print(f"Total tokens: {total_tokens}", file=sys.stderr)
    print(f"Success records: {args.success_csv}", file=sys.stderr)
    print(f"Error logs: {args.error_log}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Vaka memory sessions into OpenViking")
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"Path to Vaka CSV file, default: {DEFAULT_INPUT}",
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
        help=f"Global session IDs to import as memory, default: {DEFAULT_MEMORY_SESSIONS}",
    )
    parser.add_argument(
        "--answer-column",
        default="deepsearch_answer",
        help="CSV column containing assistant answers, default: deepsearch_answer",
    )
    parser.add_argument(
        "--keep-references",
        action="store_true",
        help="Keep Vaka <reference> tags in imported assistant answers",
    )
    parser.add_argument(
        "--openviking-url",
        default="http://localhost:1933",
        help="OpenViking service URL, default: http://localhost:1933",
    )
    parser.add_argument(
        "--account",
        default="default",
        help="OpenViking trusted-mode account header, default: default",
    )
    parser.add_argument(
        "--user-id",
        default=DEFAULT_USER_ID,
        help=f"OpenViking user_id for all imported Vaka memory, default: {DEFAULT_USER_ID}",
    )
    parser.add_argument(
        "--agent-id",
        default=DEFAULT_AGENT_ID,
        help=f"OpenViking agent_id for all imported Vaka memory, default: {DEFAULT_AGENT_ID}",
    )
    parser.add_argument(
        "--success-csv",
        default=DEFAULT_SUCCESS_CSV,
        help=f"Path to success CSV, default: {DEFAULT_SUCCESS_CSV}",
    )
    parser.add_argument(
        "--error-log",
        default=DEFAULT_ERROR_LOG,
        help=f"Path to error log, default: {DEFAULT_ERROR_LOG}",
    )
    parser.add_argument(
        "--record-path",
        default=DEFAULT_RECORD_PATH,
        help=f"Path to ingest record JSON, default: {DEFAULT_RECORD_PATH}",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        help="Force re-import even if the session is recorded as imported",
    )
    parser.add_argument(
        "--clear-ingest-record",
        action="store_true",
        help="Clear the ingest record before importing",
    )
    parser.add_argument(
        "--no-user-agent-id",
        action="store_true",
        help="Do not set OpenViking user_id or agent_id on the client",
    )
    args = parser.parse_args()

    if args.case_size <= 0:
        raise ValueError("--case-size must be positive")
    Path(args.success_csv).parent.mkdir(parents=True, exist_ok=True)
    Path(args.error_log).parent.mkdir(parents=True, exist_ok=True)
    Path(args.record_path).parent.mkdir(parents=True, exist_ok=True)

    asyncio.run(run_import(args))


if __name__ == "__main__":
    main()
