"""
Ingest LoCoMo conversations into mem0.

Each sample gets an isolated mem0 namespace keyed by sample_id (e.g. "conv-26").
speaker_a → "user" role, speaker_b → "assistant" role (following memorybench convention).

Usage:
    # Ingest all samples
    python ingest.py

    # Ingest a specific sample
    python ingest.py --sample conv-26

    # Ingest specific sessions
    python ingest.py --sample conv-26 --sessions 1-4

    # Force re-ingest even if already done
    python ingest.py --force-ingest

    # Set mem0 API key via env or flag
    MEM0_API_KEY=xxx python ingest.py
    python ingest.py --api-key xxx
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv
load_dotenv(Path.home() / ".openviking_benchmark_env")

try:
    from mem0 import MemoryClient
except ImportError:
    print("Error: mem0 package not installed. Run: pip install mem0ai", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "data" / "locomo10.json")
DEFAULT_RECORD_PATH = str(SCRIPT_DIR / "result" / ".ingest_record.json")
DEFAULT_LOG_PATH = str(SCRIPT_DIR / "result" / "ingest_errors.log")

MEM0_API_URL = "https://api.mem0.ai"

# Must match the userId format used by openclaw-mem0 plugin:
# effectiveUserId(sample_id, "agent:locomo-mem0:eval") = "{sample_id}:agent:locomo-mem0"

# Same custom instructions as memorybench mem0 provider
CUSTOM_INSTRUCTIONS = """Extract memories from group chat conversations between two people. Each message is prefixed with the speaker's name in brackets (e.g. [Alice]: text).

Guidelines:
1. Always include the speaker's name in the memory, never use generic terms like "user"
2. Extract memories for both speakers equally
3. Each memory should be self-contained with full context: who, what, when
4. Include specific details: dates, places, names of activities, emotional states
5. Cover all meaningful topics: life events, plans, hobbies, relationships, opinions"""


# ---------------------------------------------------------------------------
# LoCoMo data loading
# ---------------------------------------------------------------------------

def load_locomo_data(path: str, sample_id: Optional[str] = None) -> list[dict]:
    """Load LoCoMo JSON and optionally filter to one sample by sample_id or numeric index."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if sample_id is not None:
        # Try numeric index first
        try:
            idx = int(sample_id)
            if idx < 0 or idx >= len(data):
                raise ValueError(f"Sample index {idx} out of range (0-{len(data) - 1})")
            return [data[idx]]
        except ValueError:
            pass
        # Try matching sample_id string
        matched = [s for s in data if s.get("sample_id") == sample_id]
        if not matched:
            raise ValueError(f"sample_id '{sample_id}' not found")
        return matched

    return data


def parse_session_range(s: str) -> tuple[int, int]:
    """Parse '1-4' or '3' into (lo, hi) inclusive tuple."""
    if "-" in s:
        lo, hi = s.split("-", 1)
        return int(lo), int(hi)
    n = int(s)
    return n, n


def build_session_messages(
    item: dict,
    session_range: Optional[tuple[int, int]] = None,
) -> list[dict]:
    """
    Extract sessions from a LoCoMo sample.

    Returns list of dicts with keys:
        - messages: list of {role, content} for mem0
        - meta: session metadata
    """
    conv = item["conversation"]
    speaker_a = conv["speaker_a"]
    speaker_b = conv["speaker_b"]

    session_keys = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("_date_time")],
        key=lambda k: int(k.split("_")[1]),
    )

    sessions = []
    for sk in session_keys:
        sess_num = int(sk.split("_")[1])
        if session_range:
            lo, hi = session_range
            if sess_num < lo or sess_num > hi:
                continue

        raw_messages = conv[sk]
        if not isinstance(raw_messages, list) or not raw_messages:
            continue

        dt_key = f"{sk}_date_time"
        date_time = conv.get(dt_key, "")

        messages = []
        if date_time:
            messages.append({"role": "user", "content": f"[System]: This conversation took place on {date_time}."})
        for msg in raw_messages:
            speaker = msg.get("speaker", "")
            text = msg.get("text", "")
            messages.append({"role": "user", "content": f"[{speaker}]: {text}"})

        sessions.append(
            {
                "messages": messages,
                "meta": {
                    "sample_id": item["sample_id"],
                    "session_key": sk,
                    "date_time": date_time,
                    "speaker_a": speaker_a,
                    "speaker_b": speaker_b,
                },
            }
        )

    return sessions


# ---------------------------------------------------------------------------
# Ingest record (progress tracking)
# ---------------------------------------------------------------------------

def load_ingest_record(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ingest_record(record: dict, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)


def is_already_ingested(sample_id: str, session_key: str, record: dict) -> bool:
    key = f"mem0:{sample_id}:{session_key}"
    return key in record and record[key].get("success", False)


def mark_ingested(
    sample_id: str,
    session_key: str,
    record: dict,
    event_ids: list[str],
    meta: Optional[dict] = None,
) -> None:
    key = f"mem0:{sample_id}:{session_key}"
    record[key] = {
        "success": True,
        "timestamp": int(time.time()),
        "event_ids": event_ids,
        "meta": meta or {},
    }


def write_error_log(path: str, sample_id: str, session_key: str, error: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] ERROR [{sample_id}/{session_key}]: {error}\n")


# ---------------------------------------------------------------------------
# mem0 event polling
# ---------------------------------------------------------------------------

def poll_events(api_key: str, event_ids: list[str], timeout_sec: int = 600) -> dict[str, str]:
    """
    Poll mem0 event statuses until all complete or timeout.
    Returns {event_id: final_status}.
    """
    pending = set(event_ids)
    statuses: dict[str, str] = {}
    backoff = 0.5
    start = time.time()

    while pending:
        if time.time() - start > timeout_sec:
            for eid in pending:
                statuses[eid] = "TIMEOUT"
            break

        done_this_round = set()
        for event_id in list(pending):
            try:
                resp = requests.get(
                    f"{MEM0_API_URL}/v1/event/{event_id}/",
                    headers={"Authorization": f"Token {api_key}"},
                    timeout=30,
                )
                if resp.ok:
                    status = resp.json().get("status", "UNKNOWN")
                    if status in ("SUCCEEDED", "FAILED"):
                        statuses[event_id] = status
                        done_this_round.add(event_id)
            except Exception as e:
                print(f"    [poll] Error checking event {event_id}: {e}", file=sys.stderr)

        pending -= done_this_round
        if pending:
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)

    return statuses


# ---------------------------------------------------------------------------
# Core ingest logic
# ---------------------------------------------------------------------------

def ingest_session(
    client: MemoryClient,
    api_key: str,
    messages: list[dict],
    user_id: str,
    meta: dict,
    wait_for_indexing: bool = True,
) -> list[str]:
    """
    Add one session's messages to mem0.
    Returns list of event_ids (may be empty if async_mode=False or API returns none).
    """
    add_kwargs: dict[str, Any] = {
        "user_id": user_id,
        "version": "v2",
        "enable_graph": False,
        "async_mode": False,
        "metadata": {
            "session_key": meta.get("session_key", ""),
            "date_time": meta.get("date_time", ""),
            "speaker_a": meta.get("speaker_a", ""),
            "speaker_b": meta.get("speaker_b", ""),
        },
    }

    result = client.add(messages, **add_kwargs)

    event_ids: list[str] = []
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and item.get("event_id"):
                event_ids.append(item["event_id"])
    elif isinstance(result, dict) and result.get("event_id"):
        event_ids.append(result["event_id"])

    if wait_for_indexing and event_ids:
        statuses = poll_events(api_key, event_ids)
        failed = [eid for eid, s in statuses.items() if s != "SUCCEEDED"]
        if failed:
            raise RuntimeError(f"Events failed/timed-out: {failed}")

    return event_ids


def run_ingest(args: argparse.Namespace) -> None:
    api_key = args.api_key or os.environ.get("MEM0_API_KEY", "")
    if not api_key:
        print("Error: mem0 API key required (--api-key or MEM0_API_KEY env var)", file=sys.stderr)
        sys.exit(1)

    client = MemoryClient(api_key=api_key)

    # Set project-level custom instructions once
    try:
        client.update_project(custom_instructions=CUSTOM_INSTRUCTIONS)
        print("[INFO] Updated mem0 project custom instructions", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] Could not set custom instructions: {e}", file=sys.stderr)

    session_range = parse_session_range(args.sessions) if args.sessions else None

    # Load / clear ingest record
    if args.clear_ingest_record:
        ingest_record: dict = {}
        save_ingest_record(ingest_record, args.record)
        print("[INFO] Cleared existing ingest records", file=sys.stderr)
    else:
        ingest_record = load_ingest_record(args.record)

    samples = load_locomo_data(args.input, args.sample)
    if args.limit:
        samples = samples[: args.limit]
    print(f"[INFO] Loaded {len(samples)} sample(s)", file=sys.stderr)

    total_sessions = 0
    success_count = 0
    skip_count = 0
    error_count = 0

    for item in samples:
        sample_id: str = item["sample_id"]
        sessions = build_session_messages(item, session_range)
        print(f"\n=== Sample {sample_id} ({len(sessions)} sessions) ===", file=sys.stderr)

        for sess in sessions:
            meta = sess["meta"]
            session_key = meta["session_key"]
            label = f"{session_key} ({meta['date_time']})"
            total_sessions += 1

            if not args.force_ingest and is_already_ingested(sample_id, session_key, ingest_record):
                print(f"  [{label}] SKIP (already ingested)", file=sys.stderr)
                skip_count += 1
                continue

            print(f"  [{label}] ingesting {len(sess['messages'])} messages ...", file=sys.stderr)
            t0 = time.time()

            try:
                event_ids = ingest_session(
                    client,
                    api_key,
                    sess["messages"],
                    user_id=sample_id,
                    meta=meta,
                    wait_for_indexing=args.wait_indexing,
                )
                elapsed = time.time() - t0
                mark_ingested(sample_id, session_key, ingest_record, event_ids, meta)
                save_ingest_record(ingest_record, args.record)
                print(
                    f"  [{label}] OK  events={len(event_ids)}  {elapsed:.1f}s",
                    file=sys.stderr,
                )
                success_count += 1
            except Exception as e:
                elapsed = time.time() - t0
                print(f"  [{label}] ERROR: {e}  {elapsed:.1f}s", file=sys.stderr)
                write_error_log(args.error_log, sample_id, session_key, str(e))
                error_count += 1

    print(f"\n=== Ingest summary ===", file=sys.stderr)
    print(f"  Total sessions:  {total_sessions}", file=sys.stderr)
    print(f"  Succeeded:       {success_count}", file=sys.stderr)
    print(f"  Skipped:         {skip_count}", file=sys.stderr)
    print(f"  Failed:          {error_count}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest LoCoMo conversations into mem0")
    parser.add_argument(
        "--input",
        default=DEFAULT_DATA_PATH,
        help="Path to locomo10.json (default: ../data/locomo10.json)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="mem0 API key (or set MEM0_API_KEY env var)",
    )
    parser.add_argument(
        "--sample",
        default=None,
        help="Sample index (0-based int) or sample_id string (e.g. conv-26). Default: all.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of samples to ingest. Default: all.",
    )
    parser.add_argument(
        "--sessions",
        default=None,
        help="Session range, e.g. '1-4' or '3'. Default: all.",
    )
    parser.add_argument(
        "--record",
        default=DEFAULT_RECORD_PATH,
        help=f"Path to ingest progress record (default: {DEFAULT_RECORD_PATH})",
    )
    parser.add_argument(
        "--error-log",
        default=DEFAULT_LOG_PATH,
        help=f"Path to error log (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        default=False,
        help="Re-ingest even if already recorded as done",
    )
    parser.add_argument(
        "--clear-ingest-record",
        action="store_true",
        default=False,
        help="Clear all existing ingest records before running",
    )
    parser.add_argument(
        "--no-wait-indexing",
        dest="wait_indexing",
        action="store_false",
        default=True,
        help="Don't wait for mem0 async indexing to complete (faster but no status check)",
    )

    args = parser.parse_args()
    run_ingest(args)


if __name__ == "__main__":
    main()
