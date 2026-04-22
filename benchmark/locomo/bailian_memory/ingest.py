"""
Ingest LoCoMo conversations into Alibaba Cloud Bailian (ModelStudio) Memory.

Each sample gets an isolated memory namespace keyed by sample_id.
Speaker messages are formatted with speaker names for better memory extraction.

Config via ~/.openviking_benchmark_env:
    DASHSCOPE_API_KEY=sk-xxx
    BAILIAN_MEMORY_LIBRARY_ID=xxx
    BAILIAN_PROFILE_SCHEMA_ID=xxx   # optional, omit to skip profile extraction

Usage:
    python ingest.py
    python ingest.py --sample conv-26
    python ingest.py --sample conv-26 --sessions 1-4
    python ingest.py --force-ingest
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / ".openviking_benchmark_env")

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "data" / "locomo10.json")
DEFAULT_RECORD_PATH = str(SCRIPT_DIR / "result" / ".ingest_record.json")
DEFAULT_LOG_PATH = str(SCRIPT_DIR / "result" / "ingest_errors.log")

BAILIAN_MEMORY_BASE_URL = "https://dashscope.aliyuncs.com/api/v2/apps/memory"


# ---------------------------------------------------------------------------
# Bailian Memory API
# ---------------------------------------------------------------------------

def _auth_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def add_memory(
    api_key: str,
    memory_library_id: str,
    messages: list[dict],
    user_id: str,
    profile_schema_id: Optional[str] = None,
    meta_data: Optional[dict] = None,
) -> dict:
    """Call Bailian Memory AddMemory API."""
    payload: dict = {
        "messages": messages,
        "user_id": user_id,
        "memory_library_id": memory_library_id,
    }
    if profile_schema_id:
        payload["profile_schema"] = profile_schema_id
    if meta_data:
        payload["meta_data"] = meta_data

    resp = requests.post(
        f"{BAILIAN_MEMORY_BASE_URL}/add",
        headers=_auth_headers(api_key),
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# LoCoMo data loading
# ---------------------------------------------------------------------------

def load_locomo_data(path: str, sample_id: Optional[str] = None) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if sample_id is not None:
        try:
            idx = int(sample_id)
            if idx < 0 or idx >= len(data):
                raise ValueError(f"Sample index {idx} out of range (0-{len(data) - 1})")
            return [data[idx]]
        except ValueError:
            pass
        matched = [s for s in data if s.get("sample_id") == sample_id]
        if not matched:
            raise ValueError(f"sample_id '{sample_id}' not found")
        return matched

    return data


def parse_session_range(s: str) -> tuple[int, int]:
    if "-" in s:
        lo, hi = s.split("-", 1)
        return int(lo), int(hi)
    n = int(s)
    return n, n


def build_session_messages(
    item: dict,
    session_range: Optional[tuple[int, int]] = None,
) -> list[dict]:
    conv = item["conversation"]

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
            messages.append({
                "role": "user",
                "content": f"[System]: This conversation took place on {date_time}.",
            })
        for msg in raw_messages:
            speaker = msg.get("speaker", "")
            text = msg.get("text", "")
            blip = msg.get("blip_caption", "")
            content = f"[{speaker}]: {text}"
            if blip:
                content += f" (image: {blip})"
            messages.append({"role": "user", "content": content})

        sessions.append({
            "messages": messages,
            "meta": {
                "sample_id": item["sample_id"],
                "session_key": sk,
                "date_time": date_time,
                "speaker_a": conv.get("speaker_a", ""),
                "speaker_b": conv.get("speaker_b", ""),
            },
        })

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
    key = f"bailian:{sample_id}:{session_key}"
    return key in record and record[key].get("success", False)


def mark_ingested(
    sample_id: str,
    session_key: str,
    record: dict,
    memory_node_ids: list[str],
    meta: Optional[dict] = None,
) -> None:
    key = f"bailian:{sample_id}:{session_key}"
    record[key] = {
        "success": True,
        "timestamp": int(time.time()),
        "memory_node_ids": memory_node_ids,
        "meta": meta or {},
    }


def write_error_log(path: str, sample_id: str, session_key: str, error: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] ERROR [{sample_id}/{session_key}]: {error}\n")


# ---------------------------------------------------------------------------
# Core ingest logic
# ---------------------------------------------------------------------------

def run_ingest(args: argparse.Namespace) -> None:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("Error: DASHSCOPE_API_KEY not set in ~/.openviking_benchmark_env or environment", file=sys.stderr)
        sys.exit(1)

    memory_library_id = os.environ.get("BAILIAN_MEMORY_LIBRARY_ID", "")
    if not memory_library_id:
        print("Error: BAILIAN_MEMORY_LIBRARY_ID not set in ~/.openviking_benchmark_env or environment", file=sys.stderr)
        sys.exit(1)

    # Optional: only used if set in env
    profile_schema_id = os.environ.get("BAILIAN_PROFILE_SCHEMA_ID", "") or None
    if profile_schema_id:
        print(f"[INFO] profile_schema_id={profile_schema_id} (user portrait extraction enabled)", file=sys.stderr)
    else:
        print("[INFO] BAILIAN_PROFILE_SCHEMA_ID not set, skipping profile extraction", file=sys.stderr)

    session_range = parse_session_range(args.sessions) if args.sessions else None

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
                result = add_memory(
                    api_key=api_key,
                    memory_library_id=memory_library_id,
                    messages=sess["messages"],
                    user_id=sample_id,
                    profile_schema_id=profile_schema_id,
                    meta_data={
                        "session_key": session_key,
                        "date_time": meta["date_time"],
                        "speaker_a": meta["speaker_a"],
                        "speaker_b": meta["speaker_b"],
                    },
                )
                elapsed = time.time() - t0

                memory_node_ids = [
                    n.get("memory_node_id", "")
                    for n in result.get("memory_nodes", [])
                    if n.get("memory_node_id")
                ]
                mark_ingested(sample_id, session_key, ingest_record, memory_node_ids, meta)
                save_ingest_record(ingest_record, args.record)
                print(f"  [{label}] OK  nodes={len(memory_node_ids)}  {elapsed:.1f}s", file=sys.stderr)
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
    parser = argparse.ArgumentParser(description="Ingest LoCoMo conversations into Bailian Memory")
    parser.add_argument(
        "--input",
        default=DEFAULT_DATA_PATH,
        help="Path to locomo10.json (default: ../data/locomo10.json)",
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

    args = parser.parse_args()
    run_ingest(args)


if __name__ == "__main__":
    main()
