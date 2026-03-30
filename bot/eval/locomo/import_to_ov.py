"""
OpenViking data import tool.

Import conversations from LoCoMo JSON or plain text files into OpenViking memory.

Usage:
    # Import LoCoMo JSON conversations
    uv run python import_to_ov.py locomo10.json --sample 0 --sessions 1-4

    # Import plain text conversations
    uv run python import_to_ov.py example.txt
"""

import argparse
import json
import sys
import time
from datetime import datetime

import openviking as ov


def parse_test_file(path: str) -> list[dict]:
    """Parse txt test file into sessions.

    Each session is a dict with:
        - messages: list of user message strings
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    raw_sessions = content.split("---\n")
    sessions = []

    for raw in raw_sessions:
        lines = [line for line in raw.strip().splitlines() if line.strip()]
        if not lines:
            continue

        messages = []
        for line in lines:
            if not line.startswith("eval:"):  # Skip eval lines
                messages.append(line)

        if messages:
            sessions.append({"messages": messages})

    return sessions


def format_locomo_message(msg: dict, index: int | None = None) -> str:
    """Format a single LoCoMo message into chat-style string.

    Output format:
        [index][Speaker]: text here
    """
    speaker = msg.get("speaker", "unknown")
    text = msg.get("text", "")
    if index is not None:
        return f"[{index}][{speaker}]: {text}"
    return f"[{speaker}]: {text}"


def load_locomo_data(
    path: str,
    sample_index: int | None = None,
) -> list[dict]:
    """Load LoCoMo JSON and optionally filter to one sample."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            print(f"Error: sample index {sample_index} out of range (0-{len(data)-1})", file=sys.stderr)
            sys.exit(1)
        return [data[sample_index]]
    return data


def build_session_messages(
    item: dict,
    session_range: tuple[int, int] | None = None,
) -> list[dict]:
    """Build session messages for one LoCoMo sample.

    Returns list of dicts with keys: messages, meta.
    Each dict represents a session with multiple messages (user/assistant role).
    """
    conv = item["conversation"]
    speakers = f"{conv['speaker_a']} & {conv['speaker_b']}"

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

        dt_key = f"{sk}_date_time"
        date_time = conv.get(dt_key, "")

        # Extract messages with all as user role, including speaker in content
        messages = []
        for idx, msg in enumerate(conv[sk]):
            speaker = msg.get("speaker", "unknown")
            text = msg.get("text", "")
            messages.append({
                "role": "user",
                "text": f"[{speaker}]: {text}",
                "speaker": speaker,
                "index": idx
            })

        sessions.append({
            "messages": messages,
            "meta": {
                "sample_id": item["sample_id"],
                "session_key": sk,
                "date_time": date_time,
                "speakers": speakers,
            },
        })

    return sessions


# ---------------------------------------------------------------------------
# Ingest record helpers (avoid duplicate ingestion)
# ---------------------------------------------------------------------------


def load_ingest_record(record_path: str = "result/ingest_record.json") -> dict:
    """Load existing ingest record file, return empty dict if not exists."""
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ingest_record(record: dict, record_path: str = "result/ingest_record.json") -> None:
    """Save ingest record to file."""
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)


def is_already_ingested(
    sample_id: str | int,
    session_key: str,
    record: dict,
) -> bool:
    """Check if a specific session has already been successfully ingested."""
    key = f"viking:{sample_id}:{session_key}"
    return key in record and record[key].get("success", False)


def mark_ingested(
    sample_id: str | int,
    session_key: str,
    record: dict,
    meta: dict | None = None,
) -> None:
    """Mark a session as successfully ingested."""
    key = f"viking:{sample_id}:{session_key}"
    record[key] = {
        "success": True,
        "timestamp": int(time.time()),
        "meta": meta or {},
    }


# ---------------------------------------------------------------------------
# OpenViking import
# ---------------------------------------------------------------------------


def viking_ingest(messages: list[dict], session_time: str = None) -> None:
    """Save messages to OpenViking via SyncHTTPClient (add messages + commit session).

    Args:
        messages: List of message dicts with role and text
        session_time: Session time string (e.g., "9:36 am on 2 April, 2023")
    """
    from datetime import datetime

    # 解析 session_time
    created_at = None
    if session_time:
        try:
            dt = datetime.strptime(session_time, "%I:%M %p on %d %B, %Y")
            created_at = dt.isoformat()
        except ValueError:
            print(f"Warning: Failed to parse session_time: {session_time}", file=sys.stderr)

    client = ov.SyncHTTPClient()
    client.initialize()

    # Create new session
    session_result = client.create_session()
    session_id = session_result.get('session_id')

    # Add messages one by one
    for msg in messages:
        client.add_message(session_id, role=msg["role"], content=msg["text"], created_at=created_at)

    # Commit session to trigger memory extraction
    commit_result = client.commit_session(session_id)
    task_id = commit_result.get("task_id")

    # Wait for commit task to complete
    if task_id:
        now = time.time()
        while True:
            task = client.get_task(task_id)
            if not task or task.get("status") in ("completed", "failed"):
                break
            time.sleep(1)
        elapsed = time.time() - now
        status = task.get("status", "unknown") if task else "not found"

    client.close()


# ---------------------------------------------------------------------------
# Main import logic
# ---------------------------------------------------------------------------


def parse_session_range(s: str) -> tuple[int, int]:
    """Parse '1-4' or '3' into (lo, hi) inclusive tuple."""
    if "-" in s:
        lo, hi = s.split("-", 1)
        return int(lo), int(hi)
    n = int(s)
    return n, n


def run_import(args: argparse.Namespace) -> None:
    session_range = parse_session_range(args.sessions) if args.sessions else None

    # Handle ingest record operations
    if args.clear_ingest_record:
        ingest_record = {}
        save_ingest_record(ingest_record)
        print(f"[INFO] All existing ingest records cleared", file=sys.stderr)
    else:
        ingest_record = load_ingest_record()

    # Open output files for incremental writing
    txt_output = open(args.output, "a", encoding="utf-8")
    jsonl_output = open(f"{args.output}.jsonl", "a", encoding="utf-8")

    # Write run header
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    txt_output.write(f"\n=== Import run at {run_time} ===\n")
    txt_output.flush()

    skipped_count = 0
    success_count = 0
    error_count = 0

    if args.input.endswith(".json"):
        # LoCoMo JSON format
        samples = load_locomo_data(args.input, args.sample)

        for item in samples:
            sample_id = item["sample_id"]
            sessions = build_session_messages(item, session_range)

            print(f"\n=== Sample {sample_id} ===", file=sys.stderr)
            print(f"    {len(sessions)} session(s) to import", file=sys.stderr)

            for sess in sessions:
                meta = sess["meta"]
                messages = sess["messages"]
                label = f"{meta['session_key']} ({meta['date_time']})"

                # Skip already ingested sessions unless force-ingest is enabled
                if not args.force_ingest and is_already_ingested(sample_id, meta['session_key'], ingest_record):
                    print(f"  [{label}] [SKIP] already imported (use --force-ingest to reprocess)", file=sys.stderr)
                    skipped_count += 1

                    # Write skip record
                    result = {
                        "timestamp": run_time,
                        "sample_id": sample_id,
                        "session": meta["session_key"],
                        "status": "skipped",
                        "reason": "already imported"
                    }
                    txt_output.write(f"[{sample_id}/{meta['session_key']}] SKIPPED: already imported\n")
                    jsonl_output.write(json.dumps(result, ensure_ascii=False) + "\n")
                    txt_output.flush()
                    jsonl_output.flush()
                    continue

                # Preview messages
                preview = " | ".join([f"{msg['role']}: {msg['text'][:30]}..." for msg in messages[:3]])
                print(f"  [{label}] {preview}", file=sys.stderr)

                try:
                    viking_ingest(messages, session_time=meta.get("date_time"))
                    print(f"    -> [SUCCESS] imported to OpenViking", file=sys.stderr)
                    success_count += 1

                    # Write success record
                    result = {
                        "timestamp": run_time,
                        "sample_id": sample_id,
                        "session": meta["session_key"],
                        "status": "success",
                        "meta": meta
                    }
                    txt_output.write(f"[{sample_id}/{meta['session_key']}] SUCCESS\n")
                    jsonl_output.write(json.dumps(result, ensure_ascii=False) + "\n")
                    txt_output.flush()
                    jsonl_output.flush()

                    # Mark as successfully ingested
                    mark_ingested(sample_id, meta['session_key'], ingest_record, {
                        "date_time": meta['date_time'],
                        "speakers": meta['speakers']
                    })
                    save_ingest_record(ingest_record)  # Save immediately after success

                except Exception as e:
                    print(f"    -> [ERROR] {e}", file=sys.stderr)
                    error_count += 1

                    # Write error record
                    result = {
                        "timestamp": run_time,
                        "sample_id": sample_id,
                        "session": meta["session_key"],
                        "status": "error",
                        "error": str(e)
                    }
                    txt_output.write(f"[{sample_id}/{meta['session_key']}] ERROR: {str(e)}\n")
                    jsonl_output.write(json.dumps(result, ensure_ascii=False) + "\n")
                    txt_output.flush()
                    jsonl_output.flush()

    else:
        # Plain text format
        sessions = parse_test_file(args.input)
        print(f"Found {len(sessions)} session(s) in text file", file=sys.stderr)

        for idx, session in enumerate(sessions, start=1):
            session_key = f"txt-session-{idx}"
            print(f"\n=== Text Session {idx} ===", file=sys.stderr)

            # Skip already ingested sessions unless force-ingest is enabled
            if not args.force_ingest and is_already_ingested("txt", session_key, ingest_record):
                print(f"  [SKIP] already imported (use --force-ingest to reprocess)", file=sys.stderr)
                skipped_count += 1

                # Write skip record
                result = {
                    "timestamp": run_time,
                    "sample_id": "txt",
                    "session": session_key,
                    "status": "skipped",
                    "reason": "already imported"
                }
                txt_output.write(f"[txt/{session_key}] SKIPPED: already imported\n")
                jsonl_output.write(json.dumps(result, ensure_ascii=False) + "\n")
                txt_output.flush()
                jsonl_output.flush()
                continue

            # For plain text, all messages as user role
            messages = []
            for i, text in enumerate(session["messages"]):
                messages.append({
                    "role": "user",
                    "text": text.strip(),
                    "speaker": "user",
                    "index": i
                })

            preview = " | ".join([f"{msg['role']}: {msg['text'][:30]}..." for msg in messages[:3]])
            print(f"  {preview}", file=sys.stderr)

            try:
                viking_ingest(messages)
                print(f"    -> [SUCCESS] imported to OpenViking", file=sys.stderr)
                success_count += 1

                # Write success record
                result = {
                    "timestamp": run_time,
                    "sample_id": "txt",
                    "session": session_key,
                    "status": "success",
                    "session_index": idx
                }
                txt_output.write(f"[txt/{session_key}] SUCCESS\n")
                jsonl_output.write(json.dumps(result, ensure_ascii=False) + "\n")
                txt_output.flush()
                jsonl_output.flush()

                mark_ingested("txt", session_key, ingest_record, {
                    "session_index": idx
                })
                save_ingest_record(ingest_record)  # Save immediately after success

            except Exception as e:
                print(f"    -> [ERROR] {e}", file=sys.stderr)
                error_count += 1

                # Write error record
                result = {
                    "timestamp": run_time,
                    "sample_id": "txt",
                    "session": session_key,
                    "status": "error",
                    "error": str(e)
                }
                txt_output.write(f"[txt/{session_key}] ERROR: {str(e)}\n")
                jsonl_output.write(json.dumps(result, ensure_ascii=False) + "\n")
                txt_output.flush()
                jsonl_output.flush()

    # Close output files
    txt_output.close()
    jsonl_output.close()

    # Final summary
    total_processed = success_count + error_count + skipped_count
    print(f"\n=== Import summary ===", file=sys.stderr)
    print(f"Total sessions: {total_processed}", file=sys.stderr)
    print(f"Successfully imported: {success_count}", file=sys.stderr)
    print(f"Failed: {error_count}", file=sys.stderr)
    print(f"Skipped (already imported): {skipped_count}", file=sys.stderr)
    print(f"Results saved to: {args.output} (text) and {args.output}.jsonl (JSON Lines)", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Import conversations into OpenViking")
    parser.add_argument(
        "--input",
        default="./test_data/locomo10.json",
        help="Path to input file (.txt or LoCoMo .json)"
    )
    parser.add_argument(
        "--output",
        default="./result/import_results",
        help="Path to output file (default: import_results)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="LoCoMo JSON: sample index (0-based). Default: all samples.",
    )
    parser.add_argument(
        "--sessions",
        default=None,
        help="LoCoMo JSON: session range, e.g. '1-4' or '3'. Default: all sessions.",
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
    args = parser.parse_args()

    run_import(args)


if __name__ == "__main__":
    main()
