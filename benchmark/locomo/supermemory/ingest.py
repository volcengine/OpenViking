"""
Ingest LoCoMo conversations into Supermemory.

Each sample gets an isolated Supermemory namespace keyed by containerTag = sample_id
(e.g. "conv-26"). Sessions are formatted as date-prefixed JSON content strings,
matching the memorybench supermemory provider convention.

Usage:
    # Ingest all samples
    python ingest.py

    # Ingest a specific sample
    python ingest.py --sample conv-26

    # Ingest specific sessions
    python ingest.py --sample conv-26 --sessions 1-4

    # Force re-ingest even if already done
    python ingest.py --force-ingest

    # Set Supermemory API key via env or flag
    SUPERMEMORY_API_KEY=xxx python ingest.py
    python ingest.py --api-key xxx
"""

import argparse
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv(Path.home() / ".openviking_benchmark_env")

try:
    from supermemory import Supermemory
except ImportError:
    print("Error: supermemory package not installed. Run: pip install supermemory", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "data" / "locomo10.json")
DEFAULT_RECORD_PATH = str(SCRIPT_DIR / "result" / ".ingest_record.json")
DEFAULT_LOG_PATH = str(SCRIPT_DIR / "result" / "ingest_errors.log")


# ---------------------------------------------------------------------------
# Tag sanitization (must match openclaw-supermemory's sanitizeTag logic)
# ---------------------------------------------------------------------------

def sanitize_tag(raw: str) -> str:
    """Sanitize a tag string to match openclaw-supermemory convention.
    Replaces non-alphanumeric/underscore chars with '_', collapses runs, strips edges.
    e.g. 'conv-26' -> 'conv_26'
    """
    tag = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    tag = re.sub(r"_+", "_", tag)
    tag = tag.strip("_")
    return tag


# ---------------------------------------------------------------------------
# LoCoMo data loading
# ---------------------------------------------------------------------------

def load_locomo_data(path: str, sample_id: Optional[str] = None) -> list[dict]:
    """Load LoCoMo JSON and optionally filter to one sample by sample_id or numeric index."""
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
    """Parse '1-4' or '3' into (lo, hi) inclusive tuple."""
    if "-" in s:
        lo, hi = s.split("-", 1)
        return int(lo), int(hi)
    n = int(s)
    return n, n


def parse_locomo_date(date_time: str) -> Optional[str]:
    """Parse a LoCoMo date_time string to ISO 8601 format.
    e.g. '1:56 pm on 8 May, 2023' -> '2023-05-08T13:56:00.000Z'
    """
    import re
    from datetime import datetime, timezone
    match = re.search(r"(\d+):(\d+)\s*(am|pm)\s*on\s*(\d+)\s*(\w+),?\s*(\d+)", date_time, re.IGNORECASE)
    if not match:
        return None
    hour_str, minute, ampm, day, month_name, year = match.groups()
    hour = int(hour_str)
    if ampm.lower() == "pm" and hour != 12:
        hour += 12
    if ampm.lower() == "am" and hour == 12:
        hour = 0
    month_names = ["january","february","march","april","may","june",
                   "july","august","september","october","november","december"]
    try:
        month = next(i + 1 for i, n in enumerate(month_names) if n.startswith(month_name.lower()))
        dt = datetime(int(year), month, int(day), hour, int(minute), tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except (StopIteration, ValueError):
        return None


def build_session_content(
    item: dict,
    session_key: str,
    date_time: str,
) -> str:
    conv = item["conversation"]
    raw_messages = conv[session_key]
    lines = [f"[{msg.get('speaker', '').capitalize()}]: {msg.get('text', '')}" for msg in raw_messages]
    session_str = "\n".join(lines)

    if date_time:
        return (
            f"Here is the date the following session took place: {date_time}\n\n"
            f"Here is the session as a stringified JSON:\n{session_str}"
        )
    else:
        return f"Here is the session as a stringified JSON:\n{session_str}"


def build_sessions(
    item: dict,
    session_range: Optional[tuple[int, int]] = None,
) -> list[dict]:
    """
    Extract sessions from a LoCoMo sample.

    Returns list of dicts with keys:
        - content: formatted string for supermemory
        - meta: session metadata
    """
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
        date_iso = parse_locomo_date(date_time) if date_time else None

        content = build_session_content(item, sk, date_time)

        sessions.append(
            {
                "content": content,
                "meta": {
                    "sample_id": item["sample_id"],
                    "session_key": sk,
                    "date_time": date_time,
                    "date_iso": date_iso or "",
                    "speaker_a": conv["speaker_a"],
                    "speaker_b": conv["speaker_b"],
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
    key = f"supermemory:{sample_id}:{session_key}"
    return key in record and record[key].get("success", False)


def mark_ingested(
    sample_id: str,
    session_key: str,
    record: dict,
    doc_id: str,
    meta: Optional[dict] = None,
) -> None:
    key = f"supermemory:{sample_id}:{session_key}"
    record[key] = {
        "success": True,
        "timestamp": int(time.time()),
        "doc_id": doc_id,
        "meta": meta or {},
    }


def write_error_log(path: str, sample_id: str, session_key: str, error: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] ERROR [{sample_id}/{session_key}]: {error}\n")


# ---------------------------------------------------------------------------
# Supermemory indexing poll
# ---------------------------------------------------------------------------

def poll_document(client: Supermemory, doc_id: str, timeout_sec: int = 600) -> str:
    """
    Poll a single document until done/failed/TIMEOUT.
    Returns final status string.
    """
    backoff = 1.0
    start = time.time()

    while True:
        if time.time() - start > timeout_sec:
            return "TIMEOUT"

        try:
            doc = client.documents.get(doc_id)
            doc_status = getattr(doc, "status", None) or (doc.get("status") if isinstance(doc, dict) else None)

            if doc_status == "failed":
                return "failed"
            if doc_status == "done":
                return "done"

        except Exception as e:
            print(f"    [poll] Error checking doc {doc_id}: {e}", file=sys.stderr)

        time.sleep(backoff)
        backoff = min(backoff * 1.2, 5.0)


def poll_all_documents(
    client: Supermemory,
    pending: dict[str, dict],  # doc_id -> {"session_key", "label", ...}
    timeout_sec: int = 600,
    threads: int = 8,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Poll all pending doc_ids in parallel.
    Returns (done_map, failed_map): doc_id -> label.
    """
    done_map: dict[str, str] = {}
    failed_map: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(poll_document, client, doc_id, timeout_sec): doc_id
            for doc_id in pending
        }
        for fut in as_completed(futures):
            doc_id = futures[fut]
            label = pending[doc_id]["label"]
            try:
                status = fut.result()
            except Exception as e:
                status = f"ERROR: {e}"
            if status == "done":
                done_map[doc_id] = label
                print(f"  [{label}] indexed OK  doc_id={doc_id}", file=sys.stderr)
            else:
                failed_map[doc_id] = label
                print(f"  [{label}] indexing {status}  doc_id={doc_id}", file=sys.stderr)

    return done_map, failed_map


# ---------------------------------------------------------------------------
# Core ingest logic
# ---------------------------------------------------------------------------

def upload_session(
    client: Supermemory,
    content: str,
    container_tag: str,
    meta: dict,
) -> str:
    """Upload one session to Supermemory (no indexing wait). Returns doc_id."""
    response = client.add(
        content=content,
        container_tag=container_tag,
        metadata={
            "session_key": meta.get("session_key", ""),
            "date_time": meta.get("date_time", ""),
            "date": meta.get("date_iso", ""),
            "speaker_a": meta.get("speaker_a", ""),
            "speaker_b": meta.get("speaker_b", ""),
        },
    )
    doc_id = getattr(response, "id", None) or (response.get("id") if isinstance(response, dict) else None)
    if not doc_id:
        raise RuntimeError(f"Supermemory add() returned no id: {response}")
    return doc_id


def ingest_sample(
    item: dict,
    client: Supermemory,
    ingest_record: dict,
    record_lock: threading.Lock,
    args: argparse.Namespace,
    session_range: Optional[tuple[int, int]],
) -> tuple[int, int, int, int]:
    """Ingest one sample. Returns (total, success, skip, error) counts."""
    sample_id: str = item["sample_id"]
    container_tag = sanitize_tag(sample_id)
    sessions = build_sessions(item, session_range)
    print(f"\n=== Sample {sample_id} ({len(sessions)} sessions) [containerTag={container_tag}] ===", file=sys.stderr)

    total = len(sessions)
    success = skip = error = 0

    # Phase 1: upload all sessions serially (within this sample)
    to_poll: dict[str, dict] = {}

    for sess in sessions:
        meta = sess["meta"]
        session_key = meta["session_key"]
        label = f"{session_key} ({meta['date_time']})"

        with record_lock:
            already = not args.force_ingest and is_already_ingested(sample_id, session_key, ingest_record)
        if already:
            print(f"  [{label}] SKIP (already ingested)", file=sys.stderr)
            skip += 1
            continue

        try:
            doc_id = upload_session(client, sess["content"], container_tag, meta)
            print(f"  [{label}] uploaded  doc_id={doc_id}", file=sys.stderr)
            to_poll[doc_id] = {"session_key": session_key, "label": label, "meta": meta}
        except Exception as e:
            print(f"  [{label}] UPLOAD ERROR: {e}", file=sys.stderr)
            write_error_log(args.error_log, sample_id, session_key, str(e))
            error += 1

    if not to_poll:
        return total, success, skip, error

    # Phase 2: poll all uploaded docs in parallel until all done
    print(f"  [INFO] Polling {len(to_poll)} docs for indexing completion...", file=sys.stderr)
    t0 = time.time()
    done_map, failed_map = poll_all_documents(client, to_poll, threads=args.threads)
    elapsed = time.time() - t0
    print(f"  [INFO] Indexing done in {elapsed:.1f}s: {len(done_map)} OK, {len(failed_map)} failed", file=sys.stderr)

    # Phase 3: save ingest records (thread-safe)
    with record_lock:
        for doc_id in done_map:
            info = to_poll[doc_id]
            mark_ingested(sample_id, info["session_key"], ingest_record, doc_id, info["meta"])
            success += 1
        save_ingest_record(ingest_record, args.record)

    for doc_id in failed_map:
        info = to_poll[doc_id]
        write_error_log(args.error_log, sample_id, info["session_key"], f"indexing failed/timeout for doc {doc_id}")
        error += 1

    return total, success, skip, error


def run_ingest(args: argparse.Namespace) -> None:
    api_key = args.api_key or os.environ.get("SUPERMEMORY_API_KEY", "")
    if not api_key:
        print("Error: Supermemory API key required (--api-key or SUPERMEMORY_API_KEY env var)", file=sys.stderr)
        sys.exit(1)

    client = Supermemory(api_key=api_key)

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
    print(f"[INFO] Loaded {len(samples)} sample(s) (sample_concurrency={args.sample_concurrency})", file=sys.stderr)

    total_sessions = success_count = skip_count = error_count = 0
    record_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.sample_concurrency) as executor:
        futures = {
            executor.submit(
                ingest_sample, item, client, ingest_record, record_lock, args, session_range
            ): item["sample_id"]
            for item in samples
        }
        for fut in as_completed(futures):
            sample_id = futures[fut]
            try:
                t, s, sk, e = fut.result()
                total_sessions += t
                success_count += s
                skip_count += sk
                error_count += e
            except Exception as exc:
                print(f"[ERROR] Sample {sample_id} failed: {exc}", file=sys.stderr)
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
    parser = argparse.ArgumentParser(description="Ingest LoCoMo conversations into Supermemory")
    parser.add_argument(
        "--input",
        default=DEFAULT_DATA_PATH,
        help="Path to locomo10.json (default: ../data/locomo10.json)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Supermemory API key (or set SUPERMEMORY_API_KEY env var)",
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
        "--threads",
        type=int,
        default=8,
        help="Concurrent threads for indexing poll within a sample (default: 8)",
    )
    parser.add_argument(
        "--sample-concurrency",
        type=int,
        default=3,
        help="Number of samples to ingest concurrently (default: 3)",
    )

    args = parser.parse_args()
    run_ingest(args)


if __name__ == "__main__":
    main()
