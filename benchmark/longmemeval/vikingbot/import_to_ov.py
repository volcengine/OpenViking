"""
OpenViking data import tool for LongMemEval.

Import haystack sessions from LongMemEval JSON into OpenViking memory.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openviking as ov


LONGMEMEVAL_TIME_FORMAT = "%Y/%m/%d (%a) %H:%M"


def build_sample_agent_id(sample_id: str | int, mode: str) -> str:
    """Return the agent_id used for one sample import."""
    if mode == "shared":
        return "default"
    digest = hashlib.md5(str(sample_id).encode("utf-8")).hexdigest()[:12]
    return f"lm_{digest}"


def build_sample_user_id(sample_id: str | int, mode: str) -> str:
    """Return the user_id used for one sample import."""
    if mode == "shared":
        return "default"
    digest = hashlib.md5(f"user:{sample_id}".encode("utf-8")).hexdigest()[:12]
    return f"lm_user_{digest}"


def load_longmemeval_data(
    path: str,
    sample_index: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Load LongMemEval JSON and optionally filter to one sample."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            raise ValueError(f"Sample index {sample_index} out of range (0-{len(data) - 1})")
        return [data[sample_index]]
    return data


def build_session_messages(
    item: Dict[str, Any],
    session_range: Optional[Tuple[int, int]] = None,
) -> List[Dict[str, Any]]:
    """Build importable session messages for one LongMemEval sample."""
    sessions = []
    haystack_sessions = item.get("haystack_sessions", [])
    haystack_dates = item.get("haystack_dates", [])
    haystack_session_ids = item.get("haystack_session_ids", [])

    for index, raw_session in enumerate(haystack_sessions, start=1):
        if session_range:
            lo, hi = session_range
            if index < lo or index > hi:
                continue

        session_key = (
            haystack_session_ids[index - 1]
            if index - 1 < len(haystack_session_ids)
            else f"session_{index}"
        )
        date_time = haystack_dates[index - 1] if index - 1 < len(haystack_dates) else ""
        messages = []
        for msg_index, msg in enumerate(raw_session):
            messages.append(
                {
                    "role": msg.get("role", "user"),
                    "text": msg.get("content", ""),
                    "index": msg_index,
                }
            )

        sessions.append(
            {
                "messages": messages,
                "meta": {
                    "sample_id": item["question_id"],
                    "session_key": session_key,
                    "session_index": index,
                    "date_time": date_time,
                    "question_type": item.get("question_type", ""),
                },
            }
        )

    return sessions


def load_success_csv(csv_path: str = "./result/longmemeval_import_success.csv") -> set:
    success_keys = set()
    if Path(csv_path).exists():
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = f"viking:{row['sample_id']}:{row['session']}"
                success_keys.add(key)
    return success_keys


def write_success_record(
    record: Dict[str, Any], csv_path: str = "./result/longmemeval_import_success.csv"
) -> None:
    file_exists = Path(csv_path).exists()
    fieldnames = [
        "timestamp",
        "sample_id",
        "session",
        "date_time",
        "question_type",
        "embedding_tokens",
        "vlm_tokens",
        "llm_input_tokens",
        "llm_output_tokens",
        "total_tokens",
    ]

    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                "timestamp": record["timestamp"],
                "sample_id": record["sample_id"],
                "session": record["session"],
                "date_time": record.get("meta", {}).get("date_time", ""),
                "question_type": record.get("meta", {}).get("question_type", ""),
                "embedding_tokens": record["token_usage"].get("embedding", 0),
                "vlm_tokens": record["token_usage"].get("vlm", 0),
                "llm_input_tokens": record["token_usage"].get("llm_input", 0),
                "llm_output_tokens": record["token_usage"].get("llm_output", 0),
                "total_tokens": record["token_usage"].get("total", 0),
            }
        )


def write_error_record(
    record: Dict[str, Any], error_path: str = "./result/longmemeval_import_errors.log"
) -> None:
    with open(error_path, "a", encoding="utf-8") as f:
        timestamp = record["timestamp"]
        sample_id = record["sample_id"]
        session = record["session"]
        error = record["error"]
        f.write(f"[{timestamp}] ERROR [{sample_id}/{session}]: {error}\n")


def load_ingest_record(record_path: str = "./result/.longmemeval_ingest_record.json") -> Dict[str, Any]:
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ingest_record(
    record: Dict[str, Any], record_path: str = "./result/.longmemeval_ingest_record.json"
) -> None:
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)


def is_already_ingested(
    sample_id: str | int,
    session_key: str,
    record: Dict[str, Any],
    success_keys: Optional[set] = None,
) -> bool:
    key = f"viking:{sample_id}:{session_key}"
    if success_keys is not None and key in success_keys:
        return True
    return key in record and record[key].get("success", False)


def mark_ingested(
    sample_id: str | int,
    session_key: str,
    record: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    key = f"viking:{sample_id}:{session_key}"
    record[key] = {
        "success": True,
        "timestamp": int(time.time()),
        "meta": meta or {},
    }


def _parse_token_usage(commit_result: Dict[str, Any]) -> Dict[str, int]:
    if "result" in commit_result:
        result = commit_result["result"]
        if "token_usage" in result:
            tu = result["token_usage"]
            embedding = tu.get("embedding", {})
            llm = tu.get("llm", {})
            embed_total = embedding.get("total", embedding.get("total_tokens", 0))
            llm_total = llm.get("total", llm.get("total_tokens", 0))
            return {
                "embedding": embed_total,
                "vlm": llm_total,
                "llm_input": llm.get("input", 0),
                "llm_output": llm.get("output", 0),
                "total": tu.get("total", {}).get("total_tokens", embed_total + llm_total),
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


def _resolve_parallel(value: Optional[int], fallback: int) -> int:
    resolved = fallback if value is None else value
    if resolved < 1:
        raise ValueError("Parallelism must be >= 1")
    return resolved


async def submit_viking_ingest(
    messages: List[Dict[str, Any]],
    openviking_url: str,
    submit_semaphore: asyncio.Semaphore,
    session_time: Optional[str] = None,
    agent_id: str = "default",
    user_id: str = "default",
    sample_id: Optional[str | int] = None,
    session_key: Optional[str] = None,
) -> Dict[str, Any]:
    base_datetime = None
    if session_time:
        try:
            base_datetime = datetime.strptime(session_time, LONGMEMEVAL_TIME_FORMAT)
        except ValueError:
            print(f"Warning: Failed to parse session_time: {session_time}", file=sys.stderr)

    async with submit_semaphore:
        client = ov.AsyncHTTPClient(url=openviking_url, agent_id=agent_id, user=user_id)
        await client.initialize()
        try:
            create_res = await client.create_session()
            session_id = create_res["session_id"]

            for idx, msg in enumerate(messages):
                msg_created_at = None
                if base_datetime:
                    msg_dt = base_datetime + timedelta(seconds=idx)
                    msg_created_at = msg_dt.isoformat()

                await client.add_message(
                    session_id=session_id,
                    role=msg["role"],
                    parts=[{"type": "text", "text": msg["text"]}],
                    created_at=msg_created_at,
                )

            result = await client.commit_session(session_id, telemetry=True)
            if result.get("status") not in ("committed", "accepted"):
                raise RuntimeError(f"Commit failed: {result}")

            trace_id = result.get("trace_id", "")
            return {
                "task_id": result.get("task_id"),
                "trace_id": trace_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "session_id": session_id,
            }
        finally:
            await client.close()


async def wait_for_viking_task(
    openviking_url: str,
    task_id: Optional[str],
    wait_semaphore: asyncio.Semaphore,
    agent_id: str = "default",
    user_id: str = "default",
    sample_id: Optional[str | int] = None,
    session_key: Optional[str] = None,
) -> Dict[str, int]:
    if not task_id:
        return {"embedding": 0, "vlm": 0, "llm_input": 0, "llm_output": 0, "total": 0}

    async with wait_semaphore:
        client = ov.AsyncHTTPClient(url=openviking_url, agent_id=agent_id, user=user_id)
        await client.initialize()
        try:
            while True:
                task = await client.get_task(task_id)
                status = task.get("status") if task else "unknown"
                if status == "completed":
                    return _parse_token_usage(task)
                if status in ("failed", "unknown"):
                    raise RuntimeError(f"Task {task_id} failed: {task}")
                await asyncio.sleep(1)
        finally:
            await client.close()


async def viking_ingest(
    messages: List[Dict[str, Any]],
    openviking_url: str,
    submit_semaphore: asyncio.Semaphore,
    wait_semaphore: asyncio.Semaphore,
    session_time: Optional[str] = None,
    agent_id: str = "default",
    user_id: str = "default",
    sample_id: Optional[str | int] = None,
    session_key: Optional[str] = None,
) -> Dict[str, Any]:
    submit_result = await submit_viking_ingest(
        messages=messages,
        openviking_url=openviking_url,
        submit_semaphore=submit_semaphore,
        session_time=session_time,
        agent_id=agent_id,
        user_id=user_id,
        sample_id=sample_id,
        session_key=session_key,
    )
    token_usage = await wait_for_viking_task(
        openviking_url=openviking_url,
        task_id=submit_result.get("task_id"),
        wait_semaphore=wait_semaphore,
        agent_id=agent_id,
        user_id=user_id,
        sample_id=sample_id,
        session_key=session_key,
    )
    submit_result["token_usage"] = token_usage
    return submit_result


def parse_session_range(s: str) -> Tuple[int, int]:
    if "-" in s:
        lo, hi = s.split("-", 1)
        return int(lo), int(hi)
    n = int(s)
    return n, n


async def process_single_session(
    messages: List[Dict[str, Any]],
    sample_id: str | int,
    session_key: str,
    meta: Dict[str, Any],
    run_time: str,
    ingest_record: Dict[str, Any],
    args: argparse.Namespace,
    submit_semaphore: asyncio.Semaphore,
    wait_semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    try:
        agent_id = build_sample_agent_id(sample_id, args.agent_id_mode)
        user_id = build_sample_user_id(sample_id, args.user_id_mode)
        result = await viking_ingest(
            messages,
            args.openviking_url,
            submit_semaphore,
            wait_semaphore,
            meta.get("date_time"),
            agent_id=agent_id,
            user_id=user_id,
            sample_id=sample_id,
            session_key=session_key,
        )
        token_usage = result["token_usage"]
        task_id = result.get("task_id")
        trace_id = result.get("trace_id", "")
        embedding_tokens = token_usage.get("embedding", 0)
        vlm_tokens = token_usage.get("vlm", 0)
        print(
            f"    -> [COMPLETED] [{sample_id}/{session_key}] embed={embedding_tokens}, vlm={vlm_tokens}, task_id={task_id}, trace_id={trace_id}, user_id={user_id}, agent_id={agent_id}",
            file=sys.stderr,
        )

        record = {
            "timestamp": run_time,
            "sample_id": sample_id,
            "session": session_key,
            "status": "success",
            "meta": meta,
            "token_usage": token_usage,
            "embedding_tokens": embedding_tokens,
            "vlm_tokens": vlm_tokens,
            "task_id": task_id,
            "trace_id": trace_id,
            "user_id": user_id,
            "agent_id": agent_id,
        }
        write_success_record(record, args.success_csv)
        mark_ingested(sample_id, session_key, ingest_record, meta)
        save_ingest_record(ingest_record)
        return record
    except Exception as e:
        print(f"    -> [ERROR] [{sample_id}/{session_key}] {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        record = {
            "timestamp": run_time,
            "sample_id": sample_id,
            "session": session_key,
            "status": "error",
            "error": str(e),
        }
        write_error_record(record, args.error_log)
        return record


async def finalize_deferred_session(
    pending: Dict[str, Any],
    run_time: str,
    ingest_record: Dict[str, Any],
    args: argparse.Namespace,
    wait_semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    sample_id = pending["sample_id"]
    session_key = pending["session_key"]
    meta = pending["meta"]
    task_id = pending.get("task_id")
    trace_id = pending.get("trace_id", "")
    agent_id = pending.get("agent_id", "default")
    user_id = pending.get("user_id", "default")

    try:
        token_usage = await wait_for_viking_task(
            openviking_url=args.openviking_url,
            task_id=task_id,
            wait_semaphore=wait_semaphore,
            agent_id=agent_id,
            user_id=user_id,
            sample_id=sample_id,
            session_key=session_key,
        )
        embedding_tokens = token_usage.get("embedding", 0)
        vlm_tokens = token_usage.get("vlm", 0)
        print(
            f"    -> [COMPLETED] [{sample_id}/{session_key}] embed={embedding_tokens}, "
            f"vlm={vlm_tokens}, task_id={task_id}, trace_id={trace_id}, user_id={user_id}, agent_id={agent_id}",
            file=sys.stderr,
        )
        record = {
            "timestamp": run_time,
            "sample_id": sample_id,
            "session": session_key,
            "status": "success",
            "meta": meta,
            "token_usage": token_usage,
            "embedding_tokens": embedding_tokens,
            "vlm_tokens": vlm_tokens,
            "task_id": task_id,
            "trace_id": trace_id,
            "user_id": user_id,
            "agent_id": agent_id,
        }
        write_success_record(record, args.success_csv)
        mark_ingested(sample_id, session_key, ingest_record, meta)
        save_ingest_record(ingest_record)
        return record
    except Exception as e:
        print(f"    -> [ERROR] [{sample_id}/{session_key}] {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        record = {
            "timestamp": run_time,
            "sample_id": sample_id,
            "session": session_key,
            "status": "error",
            "error": str(e),
            "task_id": task_id,
            "user_id": user_id,
            "agent_id": agent_id,
        }
        write_error_record(record, args.error_log)
        return record


async def run_import(args: argparse.Namespace) -> None:
    submit_parallel = _resolve_parallel(getattr(args, "submit_parallel", None), args.parallel)
    wait_parallel = _resolve_parallel(getattr(args, "wait_parallel", None), args.parallel)
    submit_semaphore = asyncio.Semaphore(submit_parallel)
    wait_semaphore = asyncio.Semaphore(wait_parallel)
    session_range = parse_session_range(args.sessions) if args.sessions else None
    deferred_tasks: List[Dict[str, Any]] = []

    if args.clear_ingest_record:
        ingest_record = {}
        save_ingest_record(ingest_record)
        print("[INFO] All existing ingest records cleared", file=sys.stderr)
    else:
        ingest_record = load_ingest_record()

    success_keys = set()
    if not args.force_ingest:
        success_keys = load_success_csv(args.success_csv)
        print(
            f"[INFO] Loaded {len(success_keys)} existing success records from {args.success_csv}",
            file=sys.stderr,
        )

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    samples = load_longmemeval_data(args.input, args.sample)

    async def process_sample(item):
        sample_id = item["question_id"]
        agent_id = build_sample_agent_id(sample_id, args.agent_id_mode)
        user_id = build_sample_user_id(sample_id, args.user_id_mode)
        sessions = build_session_messages(item, session_range)

        print(f"\n=== Sample {sample_id} ===", file=sys.stderr)
        print(
            f"    {len(sessions)} session(s) to import with user_id={user_id}, agent_id={agent_id}",
            file=sys.stderr,
        )

        for sess in sessions:
            meta = sess["meta"]
            messages = sess["messages"]
            session_key = meta["session_key"]
            label = f"{session_key} ({meta['date_time']})"

            if not args.force_ingest and is_already_ingested(
                sample_id, session_key, ingest_record, success_keys
            ):
                print(f"  [{label}] [SKIP] already imported", file=sys.stderr)
                continue

            preview = " | ".join([f"{m['role']}: {m['text'][:30]}..." for m in messages[:3]])
            print(f"  [{label}] {preview}", file=sys.stderr)

            if args.wait_mode == "immediate":
                await process_single_session(
                    messages=messages,
                    sample_id=sample_id,
                    session_key=session_key,
                    meta=meta,
                    run_time=run_time,
                    ingest_record=ingest_record,
                    args=args,
                    submit_semaphore=submit_semaphore,
                    wait_semaphore=wait_semaphore,
                )
                continue

            try:
                submit_result = await submit_viking_ingest(
                    messages=messages,
                    openviking_url=args.openviking_url,
                    submit_semaphore=submit_semaphore,
                    session_time=meta.get("date_time"),
                    agent_id=agent_id,
                    user_id=user_id,
                    sample_id=sample_id,
                    session_key=session_key,
                )
                deferred_tasks.append(
                    {
                        "sample_id": sample_id,
                        "session_key": session_key,
                        "meta": meta,
                        "task_id": submit_result.get("task_id"),
                        "trace_id": submit_result.get("trace_id", ""),
                        "user_id": user_id,
                        "agent_id": agent_id,
                    }
                )
                print(
                    f"    -> [SUBMITTED] [{sample_id}/{session_key}] "
                    f"task_id={submit_result.get('task_id')}, user_id={user_id}, agent_id={agent_id}",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"    -> [ERROR] [{sample_id}/{session_key}] {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                record = {
                    "timestamp": run_time,
                    "sample_id": sample_id,
                    "session": session_key,
                    "status": "error",
                    "error": str(e),
                }
                write_error_record(record, args.error_log)

    tasks = [asyncio.create_task(process_sample(item)) for item in samples]
    print(
        f"\n[INFO] Starting import with submit_parallel={submit_parallel}, "
        f"wait_parallel={wait_parallel}, {len(tasks)} sample task(s) to process",
        file=sys.stderr,
    )
    await asyncio.gather(*tasks, return_exceptions=True)

    if args.wait_mode == "deferred" and deferred_tasks:
        print(
            f"\n[INFO] Waiting for {len(deferred_tasks)} deferred task(s) to complete",
            file=sys.stderr,
        )
        await asyncio.gather(
            *[
                asyncio.create_task(
                    finalize_deferred_session(
                        pending=pending,
                        run_time=run_time,
                        ingest_record=ingest_record,
                        args=args,
                        wait_semaphore=wait_semaphore,
                    )
                )
                for pending in deferred_tasks
            ]
        )


def main():
    parser = argparse.ArgumentParser(description="Import LongMemEval conversations into OpenViking")
    parser.add_argument(
        "--input",
        default="/Users/bytedance/mempalace/data/longmemeval-data/longmemeval_s_cleaned.json",
        help="Path to LongMemEval JSON file",
    )
    parser.add_argument(
        "--success-csv",
        default="./result/longmemeval_import_success.csv",
        help="Path to success records CSV file",
    )
    parser.add_argument(
        "--error-log",
        default="./result/longmemeval_import_errors.log",
        help="Path to error log file",
    )
    parser.add_argument(
        "--openviking-url",
        default="http://localhost:1933",
        help="OpenViking service URL",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        help="Shared default for submit/wait concurrency when dedicated flags are not set.",
    )
    parser.add_argument(
        "--submit-parallel",
        type=int,
        default=None,
        help="Max concurrent session submissions across samples. Defaults to --parallel.",
    )
    parser.add_argument(
        "--wait-parallel",
        type=int,
        default=None,
        help="Max concurrent background task waiters. Defaults to --parallel.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="LongMemEval sample index (0-based). Default all samples.",
    )
    parser.add_argument(
        "--sessions",
        default=None,
        help="Session range by haystack order, e.g. '1-4' or '3'. Default all sessions.",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        default=False,
        help="Force re-import even if already recorded as completed",
    )
    parser.add_argument(
        "--clear-ingest-record",
        action="store_true",
        default=False,
        help="Clear all existing ingest records before running",
    )
    parser.add_argument(
        "--wait-mode",
        choices=("immediate", "deferred"),
        default="deferred",
        help="When to wait for background commit tasks: immediate or deferred.",
    )
    parser.add_argument(
        "--agent-id-mode",
        choices=("shared", "per-sample"),
        default="per-sample",
        help="Whether all samples share one agent_id or each sample gets its own.",
    )
    parser.add_argument(
        "--user-id-mode",
        choices=("shared", "per-sample"),
        default="per-sample",
        help="Whether all samples share one user_id or each sample gets its own.",
    )
    args = parser.parse_args()

    Path(args.success_csv).parent.mkdir(parents=True, exist_ok=True)
    Path(args.error_log).parent.mkdir(parents=True, exist_ok=True)

    try:
        asyncio.run(run_import(args))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
