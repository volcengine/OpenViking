"""
OpenViking data import tool for LoCoMo benchmark.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

import openviking as ov

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(*_args, **_kwargs):
        return False


env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)

DEFAULT_OPENVIKING_URL = os.getenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
DEFAULT_IMPORT_ERROR_RETRIES = int(os.getenv("IMPORT_ERROR_RETRIES", "2"))
DEFAULT_SESSION_PREFIX = "locomo-ovpreingest"
DEFAULT_OPENVIKING_ACCOUNT = os.getenv("OPENVIKING_ACCOUNT", "default")
DEFAULT_OPENVIKING_USER = os.getenv("OPENVIKING_USER", "default")
DEFAULT_OPENVIKING_API_KEY = os.getenv("OPENVIKING_API_KEY", "")
DEFAULT_DATASET_LOCATION = "benchmark/locomo/data/locomo10.json"


def default_locomo_input(script_dir: Path) -> str:
    return os.getenv("LOCOMO_JSON") or str(script_dir.parent / "data" / "locomo10.json")


def dataset_missing_message(path: str) -> str:
    return (
        f"LoCoMo dataset not found: {path}\n"
        f"Set LOCOMO_JSON=/path/to/locomo10.json, pass a script input path, "
        f"or place it at {DEFAULT_DATASET_LOCATION}."
    )


def _get_session_number(session_key: str) -> int:
    return int(session_key.split("_")[1])


def _build_session_id(sample_id: str, session_key: str) -> str:
    return f"{DEFAULT_SESSION_PREFIX}-{sample_id}-{session_key}"


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _parse_locomo_session_datetime(date_time: str) -> Optional[datetime]:
    value = _clean_text(date_time)
    if not value:
        return None

    normalized = (
        value.replace(" a.m.", " AM")
        .replace(" p.m.", " PM")
        .replace(" am ", " AM ")
        .replace(" pm ", " PM ")
    )
    formats = (
        "%I:%M %p on %d %B, %Y",
        "%I %p on %d %B, %Y",
        "%I:%M %p on %B %d, %Y",
        "%I %p on %B %d, %Y",
        "%d %B, %Y",
        "%B %d, %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _normalize_locomo_session_time(date_time: str) -> str:
    parsed = _parse_locomo_session_datetime(date_time)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M")


def _visual_metadata_line(speaker: str, msg: dict) -> str:
    caption = _clean_text(msg.get("blip_caption"))
    query = _clean_text(msg.get("query"))
    details = []
    if caption:
        details.append(f"caption: {caption}")
    if query:
        details.append(f"query: {query}")
    if not details:
        return ""
    return f"[{speaker} visual]: " + "; ".join(details)


def _build_session_transcript(
    conv: Dict[str, Any],
    session_key: str,
    date_time: str,
) -> str:
    speaker_a = conv.get("speaker_a", "A")
    speaker_b = conv.get("speaker_b", "B")
    lines = [
        f"The following is a conversation transcript between {speaker_a} and {speaker_b}.",
        "---",
    ]
    if date_time:
        lines.append(f"[Session: {date_time}]")
        normalized_time = _normalize_locomo_session_time(date_time)
        if normalized_time:
            lines.append(f"[Session date: {normalized_time}]")
    lines.append(f"[LoCoMo session: {session_key}]")

    for msg in conv[session_key]:
        speaker = msg.get("speaker", "unknown")
        text = _clean_text(msg.get("text"))
        lines.append(f"[{speaker}]: {text}")
        visual_line = _visual_metadata_line(speaker, msg)
        if visual_line:
            lines.append(visual_line)

    lines.append("---")
    return "\n".join(lines)


def load_locomo_data(path: str, sample_index: Optional[int] = None) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: {dataset_missing_message(path)}", file=sys.stderr)
        sys.exit(1)

    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            raise ValueError(f"Sample index {sample_index} out of range (0-{len(data) - 1})")
        return [data[sample_index]]
    return data


def build_session_messages(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    conv = item["conversation"]
    speakers = f"{conv['speaker_a']} & {conv['speaker_b']}"

    session_keys = sorted(
        [key for key in conv if key.startswith("session_") and not key.endswith("_date_time")],
        key=_get_session_number,
    )

    sessions = []
    for session_key in session_keys:
        date_time = conv.get(f"{session_key}_date_time", "")
        utterance_count = len(conv[session_key])
        transcript = _build_session_transcript(conv, session_key, date_time)
        messages = [
            {
                "role": "user",
                "text": transcript,
                "index": 0,
                "utterance_count": utterance_count,
            }
        ]

        sessions.append(
            {
                "messages": messages,
                "meta": {
                    "sample_id": item["sample_id"],
                    "session_key": session_key,
                    "date_time": date_time,
                    "speakers": speakers,
                },
            }
        )

    return sessions


def load_success_csv(csv_path: str) -> set:
    success_keys = set()
    if Path(csv_path).exists():
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                success_keys.add(f"viking:{row['sample_id']}:{row['session']}")
    return success_keys


def write_success_record(record: Dict[str, Any], csv_path: str) -> None:
    file_exists = Path(csv_path).exists()
    fieldnames = [
        "timestamp",
        "sample_id",
        "session",
        "date_time",
        "speakers",
        "embedding_tokens",
        "llm_total_tokens",
        "llm_input_tokens",
        "llm_output_tokens",
        "llm_cache_read",
        "llm_cache_write",
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
                "speakers": record.get("meta", {}).get("speakers", ""),
                "embedding_tokens": record["token_usage"].get("embedding", 0),
                "llm_total_tokens": record["token_usage"].get("llm_total", 0),
                "llm_input_tokens": record["token_usage"].get("llm_input", 0),
                "llm_output_tokens": record["token_usage"].get("llm_output", 0),
                "llm_cache_read": record["token_usage"].get("llm_cache_read", 0),
                "llm_cache_write": record["token_usage"].get("llm_cache_write", 0),
                "total_tokens": record["token_usage"].get("total", 0),
            }
        )


def write_error_record(record: Dict[str, Any], error_path: str) -> None:
    with open(error_path, "a", encoding="utf-8") as f:
        f.write(
            f"[{record['timestamp']}] ERROR [{record['sample_id']}/{record['session']}]: {record['error']}\n"
        )


def is_already_ingested(
    sample_id: str, session_key: str, success_keys: Optional[set] = None
) -> bool:
    return success_keys is not None and f"viking:{sample_id}:{session_key}" in success_keys


def _parse_token_usage(commit_result: Dict[str, Any]) -> Dict[str, int]:
    if "result" in commit_result:
        result = commit_result["result"]
        if "token_usage" in result:
            token_usage = result["token_usage"]
            embedding = token_usage.get("embedding", {})
            llm = token_usage.get("llm", {})
            embed_total = embedding.get("total_tokens", embedding.get("total", 0))
            llm_total = llm.get("total_tokens", llm.get("total", 0))
            llm_input = llm.get("prompt_tokens", llm.get("input", 0))
            llm_output = llm.get("completion_tokens", llm.get("output", 0))
            return {
                "embedding": embed_total,
                "llm_total": llm_total,
                "llm_input": llm_input,
                "llm_output": llm_output,
                "llm_cache_read": llm.get("cache_read", 0),
                "llm_cache_write": llm.get("cache_write", 0),
                "total": token_usage.get("total", {}).get("total_tokens", embed_total + llm_total),
            }

    telemetry = commit_result.get("telemetry", {}).get("summary", {})
    tokens = telemetry.get("tokens", {})
    return {
        "embedding": tokens.get("embedding", {}).get("total", 0),
        "llm_total": tokens.get("llm", {}).get("total", 0),
        "llm_input": tokens.get("llm", {}).get("input", 0),
        "llm_output": tokens.get("llm", {}).get("output", 0),
        "llm_cache_read": tokens.get("llm", {}).get("cache_read", 0),
        "llm_cache_write": tokens.get("llm", {}).get("cache_write", 0),
        "total": tokens.get("total", 0),
    }


async def viking_ingest(
    messages: List[Dict[str, Any]],
    openviking_url: str,
    session_time: Optional[str] = None,
    session_id: Optional[str] = None,
    replace_session: bool = False,
    account: str = DEFAULT_OPENVIKING_ACCOUNT,
    user: str = DEFAULT_OPENVIKING_USER,
    api_key: str = DEFAULT_OPENVIKING_API_KEY,
) -> Dict[str, Any]:
    base_datetime = _parse_locomo_session_datetime(session_time or "")
    if session_time and base_datetime is None:
        print(f"Warning: Failed to parse session_time: {session_time}", file=sys.stderr)

    client = ov.AsyncHTTPClient(
        url=openviking_url,
        api_key=api_key or None,
        account=account,
        user=user,
    )
    await client.initialize()

    try:
        if session_id and replace_session:
            try:
                await client.delete_session(session_id)
            except Exception as e:
                message = str(e).lower()
                if "not found" not in message and "not_found" not in message:
                    raise

        create_res = await client.create_session(session_id)
        resolved_session_id = create_res["session_id"]

        for idx, msg in enumerate(messages):
            msg_created_at = None
            if base_datetime:
                msg_created_at = (base_datetime + timedelta(seconds=idx)).isoformat()
            await client.add_message(
                session_id=resolved_session_id,
                role=msg["role"],
                parts=[{"type": "text", "text": msg["text"]}],
                created_at=msg_created_at,
                peer_id=msg.get("peer_id"),
            )

        result = await client.commit_session(resolved_session_id, telemetry=True)
        if result.get("status") not in ("committed", "accepted"):
            raise RuntimeError(f"Commit failed: {result}")

        task_id = result.get("task_id")
        if task_id:
            max_attempts = 3600
            for _ in range(max_attempts):
                task = await client.get_task(task_id)
                status = task.get("status") if task else "unknown"
                if status == "completed":
                    token_usage = _parse_token_usage(task)
                    break
                if status in ("failed", "cancelled", "unknown"):
                    raise RuntimeError(f"Task {task_id} {status}: {task}")
                await asyncio.sleep(1)
            else:
                raise RuntimeError(f"Task {task_id} timed out after {max_attempts} attempts")
        else:
            token_usage = {
                "embedding": 0,
                "llm_total": 0,
                "llm_input": 0,
                "llm_output": 0,
                "llm_cache_read": 0,
                "llm_cache_write": 0,
                "total": 0,
            }

        return {
            "token_usage": token_usage,
            "task_id": task_id,
            "trace_id": result.get("trace_id", ""),
            "session_id": resolved_session_id,
        }
    finally:
        await client.close()


def parse_observer_model_totals(status_text: str) -> tuple[int, int, int, int]:
    embedding_prompt = 0
    embedding_completion = 0
    vlm_prompt = 0
    vlm_completion = 0
    current_section = ""

    for raw_line in status_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith("Models:"):
            current_section = line.split(" ", 1)[0].lower()
            continue
        if not line.startswith("|"):
            continue
        cols = [col.strip() for col in line.split("|") if col.strip()]
        if len(cols) < 7 or cols[0] == "Model":
            continue

        prompt = int(cols[3]) if cols[3].isdigit() else 0
        completion = int(cols[4]) if cols[4].isdigit() else 0
        if current_section == "embedding":
            embedding_prompt += prompt
            embedding_completion += completion
        elif current_section == "vlm":
            vlm_prompt += prompt
            vlm_completion += completion

    return embedding_prompt, embedding_completion, vlm_prompt, vlm_completion


async def _read_model_totals(
    client: httpx.AsyncClient, openviking_url: str
) -> tuple[int, int, int, int] | None:
    try:
        resp = await client.get(f"{openviking_url}/api/v1/observer/models")
        if resp.status_code != 200:
            return None
        status_text = resp.json().get("result", {}).get("status", "")
    except Exception:
        return None

    if "No model usage data available" in status_text:
        return 0, 0, 0, 0
    return parse_observer_model_totals(status_text)


async def _read_queue_totals(
    client: httpx.AsyncClient, openviking_url: str
) -> tuple[int, int, int] | None:
    try:
        resp = await client.get(f"{openviking_url}/api/v1/observer/queue")
        if resp.status_code != 200:
            return None
        status_text = resp.json().get("result", {}).get("status", "")
    except Exception:
        return None

    for raw_line in status_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "TOTAL" not in line:
            continue
        cols = [col.strip() for col in line.split("|") if col.strip()]
        if len(cols) < 4 or cols[0] != "TOTAL":
            continue
        try:
            return int(cols[1]), int(cols[2]), int(cols[3])
        except ValueError:
            return None
    return None


def append_true_token_record(output_file: str, record: Dict[str, Any]) -> None:
    fieldnames = [
        "timestamp",
        "embedding_input_tokens",
        "embedding_output_tokens",
        "vlm_llm_input_tokens",
        "vlm_llm_output_tokens",
    ]
    expected_header = ",".join(fieldnames)
    mode = "a"
    should_write_header = not Path(output_file).exists()

    if not should_write_header:
        with open(output_file, "r", encoding="utf-8", newline="") as f:
            current_header = f.readline().strip()
        if current_header != expected_header:
            mode = "w"
            should_write_header = True

    with open(output_file, mode, encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if should_write_header:
            writer.writeheader()
        writer.writerow(record)


async def process_single_session(
    messages: List[Dict[str, Any]],
    sample_id: str,
    session_key: str,
    meta: Dict[str, Any],
    run_time: str,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    session_id = _build_session_id(sample_id, session_key)
    max_attempts = max(0, getattr(args, "error_retries", DEFAULT_IMPORT_ERROR_RETRIES)) + 1
    retry_delay = float(getattr(args, "retry_delay_sec", 2.0))
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            replace_session = args.force_ingest
            if attempt > 1:
                replace_session = True
                print(
                    f"    -> [RETRY] [{sample_id}/{session_key}] "
                    f"attempt {attempt}/{max_attempts}; replacing partial session {session_id}",
                    file=sys.stderr,
                )

            result = await viking_ingest(
                messages,
                args.openviking_url,
                meta.get("date_time"),
                session_id=session_id,
                replace_session=replace_session,
                account=args.account,
                user=args.user,
                api_key=args.api_key,
            )
            token_usage = result["token_usage"]
            task_id = result.get("task_id")
            trace_id = result.get("trace_id", "")
            resolved_session_id = result.get("session_id", session_id)
            print(
                f"    -> [COMPLETED] [{sample_id}/{session_key}] "
                f"embed={token_usage.get('embedding', 0)}, llm={token_usage.get('llm_total', 0)}, "
                f"session_id={resolved_session_id}, task_id={task_id}, trace_id={trace_id}",
                file=sys.stderr,
            )

            record = {
                "timestamp": run_time,
                "sample_id": sample_id,
                "session": session_key,
                "status": "success",
                "meta": meta,
                "token_usage": token_usage,
            }
            write_success_record(record, args.success_csv)
            return record
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                print(
                    f"    -> [ERROR] [{sample_id}/{session_key}] "
                    f"attempt {attempt}/{max_attempts}: {e}; retrying",
                    file=sys.stderr,
                )
                await asyncio.sleep(retry_delay)
                continue
            print(f"    -> [ERROR] [{sample_id}/{session_key}] {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    record = {
        "timestamp": run_time,
        "sample_id": sample_id,
        "session": session_key,
        "status": "error",
        "error": str(last_error) if last_error else "unknown import error",
    }
    write_error_record(record, args.error_log)
    return record


async def wait_for_queues_and_record_totals(
    openviking_url: str,
    output_file: str,
    baseline_processed: int = 0,
    baseline_model_totals: tuple[int, int, int, int] | None = None,
    expected_processed_delta: int = 0,
    max_wait_sec: int = 1800,
    settle_checks: int = 3,
    poll_interval: float = 2.0,
) -> None:
    has_processed_target = expected_processed_delta > 0
    target_processed = baseline_processed + max(0, expected_processed_delta)
    target_note = f", target_processed={target_processed}" if has_processed_target else ""
    print(
        f"\n[INFO] Waiting for OpenViking background queues to drain "
        f"(baseline_processed={baseline_processed}{target_note}, settle={settle_checks})...",
        file=sys.stderr,
    )

    async with httpx.AsyncClient() as client:
        started = asyncio.get_running_loop().time()
        idle_streak = 0
        last_totals: tuple[int, int, int] | None = None
        timed_out = False

        while True:
            elapsed = asyncio.get_running_loop().time() - started
            if elapsed > max_wait_sec:
                print(
                    f"[ERROR] Queue drain timeout after {elapsed:.0f}s "
                    f"(last totals={last_totals}, idle_streak={idle_streak})",
                    file=sys.stderr,
                )
                timed_out = True
                break

            totals = await _read_queue_totals(client, openviking_url)
            if totals is None:
                await asyncio.sleep(poll_interval)
                continue

            pending, in_progress, processed = totals
            last_totals = totals
            expected_work_done = True if not has_processed_target else processed >= target_processed
            queues_idle = pending == 0 and in_progress == 0

            if expected_work_done and queues_idle:
                idle_streak += 1
                if idle_streak >= settle_checks:
                    print(
                        f"[INFO] Background queues drained. Processed "
                        f"{processed - baseline_processed} item(s) since baseline.",
                        file=sys.stderr,
                    )
                    break
            else:
                idle_streak = 0
                if not expected_work_done and queues_idle:
                    print(
                        f"  .. waiting for expected import work "
                        f"(processed={processed}, target={target_processed})",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  .. queue state pending={pending} in_progress={in_progress} "
                        f"processed={processed}",
                        file=sys.stderr,
                    )
            await asyncio.sleep(poll_interval)

        if timed_out:
            raise TimeoutError(
                "OpenViking background queues did not finish before checkpoint "
                f"(target_processed={target_processed if has_processed_target else 'none'}, "
                f"last_totals={last_totals})"
            )

        print("[INFO] Fetching final true token usage from observer...", file=sys.stderr)
        try:
            final_totals = await _read_model_totals(client, openviking_url)
            if final_totals is None:
                print("[INFO] No model usage recorded by OpenViking server.", file=sys.stderr)
                return

            (
                baseline_embedding_prompt,
                baseline_embedding_completion,
                baseline_vlm_prompt,
                baseline_vlm_completion,
            ) = baseline_model_totals or (0, 0, 0, 0)
            (
                final_embedding_prompt,
                final_embedding_completion,
                final_vlm_prompt,
                final_vlm_completion,
            ) = final_totals

            delta_embedding_prompt = max(final_embedding_prompt - baseline_embedding_prompt, 0)
            delta_embedding_completion = max(
                final_embedding_completion - baseline_embedding_completion, 0
            )
            delta_vlm_prompt = max(final_vlm_prompt - baseline_vlm_prompt, 0)
            delta_vlm_completion = max(final_vlm_completion - baseline_vlm_completion, 0)

            print("\n=== TRUE OpenViking Token Delta For This Run ===", file=sys.stderr)
            print(f"Embedding Input (Prompt) Delta: {delta_embedding_prompt}", file=sys.stderr)
            print(
                f"Embedding Output (Completion) Delta: {delta_embedding_completion}",
                file=sys.stderr,
            )
            print(f"VLM/LLM Input (Prompt) Delta: {delta_vlm_prompt}", file=sys.stderr)
            print(f"VLM/LLM Output (Completion) Delta: {delta_vlm_completion}", file=sys.stderr)
            print(
                f"[INFO] Final cumulative totals: embedding_in={final_embedding_prompt}, "
                f"embedding_out={final_embedding_completion}, vlm_in={final_vlm_prompt}, "
                f"vlm_out={final_vlm_completion}",
                file=sys.stderr,
            )

            append_true_token_record(
                output_file,
                {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "embedding_input_tokens": delta_embedding_prompt,
                    "embedding_output_tokens": delta_embedding_completion,
                    "vlm_llm_input_tokens": delta_vlm_prompt,
                    "vlm_llm_output_tokens": delta_vlm_completion,
                },
            )
            print(f"True token totals saved to: {output_file}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] Failed to fetch final token usage: {e}", file=sys.stderr)


async def run_import(args: argparse.Namespace) -> None:
    success_keys = set()
    if not args.force_ingest:
        success_keys = load_success_csv(args.success_csv)
        print(
            f"[INFO] Loaded {len(success_keys)} existing success records from {args.success_csv}",
            file=sys.stderr,
        )

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    skipped_count = 0
    samples = load_locomo_data(args.input, args.sample)
    tasks = []
    semaphore = asyncio.Semaphore(max(1, args.parallel))

    async def run_limited(coro):
        async with semaphore:
            return await coro

    async with httpx.AsyncClient() as snap_client:
        baseline_totals = await _read_queue_totals(snap_client, args.openviking_url)
        baseline_model_totals = await _read_model_totals(snap_client, args.openviking_url)
    baseline_processed = baseline_totals[2] if baseline_totals else 0
    print(f"[INFO] Observer baseline processed={baseline_processed}", file=sys.stderr)

    for item in samples:
        sample_id = item["sample_id"]
        sessions = build_session_messages(item)

        for sess in sessions:
            meta = sess["meta"]
            messages = sess["messages"]
            session_key = meta["session_key"]
            label = f"{session_key} ({meta['date_time']})"

            if not args.force_ingest and is_already_ingested(sample_id, session_key, success_keys):
                print(
                    f"  [{label}] [SKIP] already imported (use --force-ingest to reprocess)",
                    file=sys.stderr,
                )
                skipped_count += 1
                continue

            tasks.append(
                run_limited(
                    process_single_session(
                        messages=messages,
                        sample_id=sample_id,
                        session_key=session_key,
                        meta=meta,
                        run_time=run_time,
                        args=args,
                    )
                )
            )

    if tasks:
        print(
            f"\n[INFO] Starting import with {len(tasks)} tasks to process (parallel={max(1, args.parallel)})",
            file=sys.stderr,
        )
        results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        results = []

    failed_sessions = []
    for result in results:
        if isinstance(result, Exception):
            failed_sessions.append(f"exception:{result}")
        elif isinstance(result, dict) and result.get("status") == "error":
            failed_sessions.append(f"{result.get('sample_id')}/{result.get('session')}")

    if failed_sessions:
        print(
            f"Import failed for {len(failed_sessions)} session(s) after retries: "
            + ", ".join(failed_sessions[:20]),
            file=sys.stderr,
        )
        if len(failed_sessions) > 20:
            print(f"... and {len(failed_sessions) - 20} more", file=sys.stderr)
        sys.exit(1)

    true_token_file = Path(args.success_csv).parent / "import_true_tokens.csv"
    await wait_for_queues_and_record_totals(
        args.openviking_url,
        str(true_token_file),
        baseline_processed=baseline_processed,
        baseline_model_totals=baseline_model_totals,
        max_wait_sec=args.queue_max_wait_sec,
    )


def reset_generated_file(path: str | Path, label: str) -> None:
    target = Path(path)
    if target.exists():
        target.unlink()
        print(f"Removed existing {label}: {target}", file=sys.stderr)


def main():
    script_dir = Path(__file__).parent.resolve()
    default_input = default_locomo_input(script_dir)
    default_success_csv = str(script_dir / "result_preingest" / "import_success.csv")
    default_error_log = str(script_dir / "result_preingest" / "import_errors.log")

    parser = argparse.ArgumentParser(description="Import LoCoMo conversations into OpenViking")
    parser.add_argument("--input", default=default_input, help="Path to LoCoMo .json")
    parser.add_argument(
        "--success-csv", default=default_success_csv, help="Success records CSV path"
    )
    parser.add_argument("--error-log", default=default_error_log, help="Error log path")
    parser.add_argument(
        "--openviking-url", default=DEFAULT_OPENVIKING_URL, help="OpenViking service URL"
    )
    parser.add_argument(
        "--account", default=DEFAULT_OPENVIKING_ACCOUNT, help="OpenViking account namespace"
    )
    parser.add_argument("--user", default=DEFAULT_OPENVIKING_USER, help="OpenViking user namespace")
    parser.add_argument("--api-key", default=DEFAULT_OPENVIKING_API_KEY, help="OpenViking API key")
    parser.add_argument("--sample", type=int, default=None, help="Sample index (0-based)")
    parser.add_argument(
        "--force-ingest", action="store_true", default=False, help="Force re-import"
    )
    parser.add_argument("--parallel", type=int, default=4, help="Max concurrent session imports")
    parser.add_argument(
        "--queue-max-wait-sec",
        type=int,
        default=1800,
        help="Maximum seconds to wait for OpenViking observer queue deltas",
    )
    parser.add_argument(
        "--error-retries",
        type=int,
        default=DEFAULT_IMPORT_ERROR_RETRIES,
        help="Retry failed imports this many times",
    )
    parser.add_argument(
        "--retry-delay-sec",
        type=float,
        default=2.0,
        help="Seconds to wait between import retry attempts",
    )
    args = parser.parse_args()

    Path(args.success_csv).parent.mkdir(parents=True, exist_ok=True)
    Path(args.error_log).parent.mkdir(parents=True, exist_ok=True)
    if args.force_ingest:
        reset_generated_file(args.success_csv, "import success CSV")
        reset_generated_file(args.error_log, "import error log")
        reset_generated_file(
            Path(args.success_csv).parent / "import_true_tokens.csv", "import true-token CSV"
        )

    try:
        asyncio.run(run_import(args))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
