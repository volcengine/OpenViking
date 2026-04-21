"""
OpenClaw response evaluator.

Two modes:
  ingest  - Load conversations into openclaw (builds memory)
  qa      - Run QA questions against openclaw and output response vs expected answer

Usage:
    # Ingest conversations
    uv run python eval.py ingest locomo10.json --sample 0 --sessions 1-4

    # Run QA evaluation (uses same user from ingest)
    uv run python eval.py qa locomo10.json --sample 0 --output qa_results.txt

    # Original txt mode (ingest only)
    uv run python eval.py ingest example.txt --output output.txt
"""

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock

import requests

# Configuration constants
DEFAULT_BASE_URL = "http://127.0.0.1:18789"
DEFAULT_AGENT_ID = "locomo-eval"
DEFAULT_INGEST_RECORD_PATH = ".ingest_record.json"

# CSV write lock for thread safety
csv_lock = Lock()


# ---------------------------------------------------------------------------
# Txt-based test file parsing (original format)
# ---------------------------------------------------------------------------

def parse_test_file(path: str) -> list[dict]:
    """Parse txt test file into sessions.

    Each session is a dict with:
        - messages: list of user message strings
        - evals: list of eval expectation strings
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: Test file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except IOError as e:
        print(f"Error reading test file: {e}", file=sys.stderr)
        sys.exit(1)

    raw_sessions = content.split("---\n")
    sessions = []

    for raw in raw_sessions:
        lines = [line for line in raw.strip().splitlines() if line.strip()]
        if not lines:
            continue

        messages = []
        evals = []
        for line in lines:
            if line.startswith("eval:"):
                evals.append(line[len("eval:"):].strip())
            else:
                messages.append(line)

        if messages or evals:
            sessions.append({"messages": messages, "evals": evals})

    return sessions


# ---------------------------------------------------------------------------
# LoCoMo JSON parsing
# ---------------------------------------------------------------------------

def format_locomo_message(msg: dict) -> str:
    """Format a single LoCoMo message into a natural chat-style string.

    Output format:
        Speaker: text here
        image_url: caption
    """
    speaker = msg.get("speaker", "unknown")
    text = msg.get("text", "")
    line = f"{speaker}: {text}"

    img_urls = msg.get("img_url", [])
    if isinstance(img_urls, str):
        img_urls = [img_urls]
    blip = msg.get("blip_caption", "")

    if img_urls:
        for url in img_urls:
            caption = f": {blip}" if blip else ""
            line += f"\n{url}{caption}"
    elif blip:
        line += f"\n({blip})"

    return line


def load_locomo_data(
    path: str,
    sample_index: int | None = None,
) -> list[dict]:
    """Load LoCoMo JSON and optionally filter to one sample."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: LoCoMo JSON file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing LoCoMo JSON file: {e}", file=sys.stderr)
        sys.exit(1)
    except IOError as e:
        print(f"Error reading LoCoMo JSON file: {e}", file=sys.stderr)
        sys.exit(1)

    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            print(f"Error: sample index {sample_index} out of range (0-{len(data)-1})", file=sys.stderr)
            sys.exit(1)
        return [data[sample_index]]
    return data


def build_session_messages(
    item: dict,
    session_range: tuple[int, int] | None = None,
    tail: str = "[]",
) -> list[dict]:
    """Build bundled session messages for one LoCoMo sample.

    Returns list of dicts with keys: message, meta.
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

        parts = [f"[group chat conversation: {date_time}]"]
        for msg in conv[sk]:
            parts.append(format_locomo_message(msg))
        if tail:
            parts.append(tail)
        combined = "\n\n".join(parts)

        sessions.append({
            "message": combined,
            "meta": {
                "sample_id": item["sample_id"],
                "session_key": sk,
                "date_time": date_time,
                "speakers": speakers,
            },
        })

    return sessions


# ---------------------------------------------------------------------------
# Question time helpers
# ---------------------------------------------------------------------------

def parse_locomo_datetime(date_str: str) -> datetime | None:
    """解析 LoCoMo 时间格式，如 '1:56 pm on 8 May, 2023'"""
    try:
        # 移除时间部分，只保留日期 "8 May, 2023"
        if " on " in date_str:
            date_part = date_str.split(" on ")[-1]
            return datetime.strptime(date_part.strip(), "%d %B, %Y")
    except ValueError:
        pass
    return None


def get_sample_question_time(sample: dict) -> str | None:
    """从 sample 的 conversation 中提取最后一个有内容 session 的时间，返回 ISO 格式日期"""
    conversation = sample.get("conversation", {})

    # 找所有 session_N 字段（非 date_time）
    session_keys = [
        k for k in conversation.keys() if k.startswith("session_") and "date_time" not in k
    ]
    if not session_keys:
        return None

    # 按 session 编号排序，找到最后一个有内容的
    def get_session_num(key):
        try:
            return int(key.replace("session_", ""))
        except ValueError:
            return 0

    session_keys.sort(key=get_session_num, reverse=True)

    for session_key in session_keys:
        if conversation.get(session_key):  # 有内容
            # 找到对应的 date_time
            session_num = get_session_num(session_key)
            dt_key = f"session_{session_num}_date_time"
            date_str = conversation.get(dt_key)
            if date_str:
                dt = parse_locomo_datetime(date_str)
                if dt:
                    return dt.strftime("%Y-%m-%d")

    return None


# ---------------------------------------------------------------------------
# Ingest record helpers (avoid duplicate ingestion)
# ---------------------------------------------------------------------------

def load_ingest_record(record_path: str = DEFAULT_INGEST_RECORD_PATH) -> dict:
    """Load existing ingest record file, return empty dict if not exists."""
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        print(f"Warning: Error parsing ingest record: {e}, starting fresh", file=sys.stderr)
        return {}
    except IOError as e:
        print(f"Warning: Error reading ingest record: {e}, starting fresh", file=sys.stderr)
        return {}


def save_ingest_record(record: dict, record_path: str = DEFAULT_INGEST_RECORD_PATH) -> None:
    """Save ingest record to file."""
    try:
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"Warning: Error saving ingest record: {e}", file=sys.stderr)


def is_already_ingested(
    agent_id: str,
    user_key: str,
    sample_id: str | int,
    session_key: str,
    record: dict,
) -> bool:
    """Check if a specific session has already been successfully ingested."""
    key = f"{agent_id}:{user_key}:{sample_id}:{session_key}"
    return key in record and record[key].get("success", False)


def mark_ingested(
    agent_id: str,
    user_key: str,
    sample_id: str | int,
    session_key: str,
    record: dict,
    meta: dict | None = None,
) -> None:
    """Mark a session as successfully ingested."""
    key = f"{agent_id}:{user_key}:{sample_id}:{session_key}"
    record[key] = {
        "success": True,
        "timestamp": int(time.time()),
        "meta": meta or {},
    }


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def extract_response_text(response_json: dict) -> str:
    """Extract assistant text from the /v1/responses API response."""
    try:
        for item in response_json.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        return content.get("text", "")
        for item in response_json.get("output", []):
            if "text" in item:
                return item["text"]
            for content in item.get("content", []):
                if "text" in content:
                    return content["text"]
    except (KeyError, TypeError, IndexError) as e:
        print(f"Warning: Error extracting response text: {e}", file=sys.stderr)
    return f"[ERROR: could not extract text from response: {response_json}]"


def get_session_id_from_key(session_key: str, user: str, agent_id: str = "main") -> str | None:
    """Search all agents' sessions.json files for the session_key and return sessionFile path.
    Returns the full path to the session JSONL file if found, None otherwise.
    """
    agents_base_dir = os.path.expanduser("~/.openclaw/agents")

    if not os.path.exists(agents_base_dir):
        print(f"    [session] Agents directory not found: {agents_base_dir}", file=sys.stderr)
        return None

    # Iterate through all agent directories
    for agent_name in os.listdir(agents_base_dir):
        agent_dir = os.path.join(agents_base_dir, agent_name)
        if not os.path.isdir(agent_dir):
            continue

        sessions_dir = os.path.join(agent_dir, "sessions")
        sessions_file = os.path.join(sessions_dir, "sessions.json")

        if not os.path.exists(sessions_file):
            continue

        try:
            with open(sessions_file, "r") as f:
                data = json.load(f)

            # Search for the session_key in this sessions.json
            for key, value in data.items():
                if session_key in key and isinstance(value, dict):
                    session_file = value.get("sessionFile")
                    if session_file:
                        print(f"    [session] Found sessionFile in agent '{agent_name}': {session_file}", file=sys.stderr)
                        return session_file

        except json.JSONDecodeError as e:
            print(f"    [session] Error parsing {sessions_file}: {e}", file=sys.stderr)
            continue
        except IOError as e:
            print(f"    [session] Error reading {sessions_file}: {e}", file=sys.stderr)
            continue

    print(f"    [session] session_key '{session_key}' not found in any agent's sessions.json", file=sys.stderr)
    return None


def get_session_id(user: str, agent_id: str = "main") -> str | None:
    """Read the current session ID for the given user from sessions.json."""
    sessions_file = os.path.expanduser(f"~/.openclaw/agents/{agent_id}/sessions/sessions.json")
    try:
        with open(sessions_file, "r") as f:
            data = json.load(f)
        key = f"agent:{agent_id}:openresponses-user:{user}"
        return data.get(key, {}).get("sessionId")
    except FileNotFoundError:
        print(f"    [reset] Session ID file not found: {sessions_file}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"    [reset] Error parsing session ID file: {e}", file=sys.stderr)
        return None
    except IOError as e:
        print(f"    [reset] Error reading session ID file: {e}", file=sys.stderr)
        return None


def reset_session(session_path: str, agent_id: str = "main") -> str | None:
    """Rename the session .jsonl file with a timestamp suffix.
    Accepts either a session_id or a full path to the session file.
    Returns the new filename if successful, None otherwise.
    """
    # Check if session_path is already a full path
    if os.path.isabs(session_path) and os.path.exists(session_path):
        src = session_path
    else:
        # Treat as session_id
        sessions_dir = os.path.expanduser(f"~/.openclaw/agents/{agent_id}/sessions")
        src = os.path.join(sessions_dir, f"{session_path}.jsonl")

    if not os.path.exists(src):
        print(f"    [backup] Session file not found: {src}", file=sys.stderr)
        return None

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    dst = f"{src}.{timestamp}"
    try:
        os.rename(src, dst)
        new_filename = os.path.basename(dst)
        print(f"    [backup] renamed {os.path.basename(src)} -> {new_filename}", file=sys.stderr)
        return new_filename
    except IOError as e:
        print(f"    [backup] could not rename session file: {e}", file=sys.stderr)
        return None


def calculate_session_metrics_from_jsonl(jsonl_filename: str, agent_id: str = "main") -> dict:
    """Calculate token usage and rounds from archived JSONL file."""
    # Check if jsonl_filename is already a full path
    if os.path.isabs(jsonl_filename) and os.path.exists(jsonl_filename):
        jsonl_full_path = jsonl_filename
    else:
        sessions_dir = os.path.expanduser(f"~/.openclaw/agents/{agent_id}/sessions")
        jsonl_full_path = os.path.join(sessions_dir, jsonl_filename)

    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "total_tokens": 0,
        "rounds": 0,
    }

    if not os.path.exists(jsonl_full_path):
        return usage

    try:
        with open(jsonl_full_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("type") == "message" and entry.get("message", {}).get("role") == "assistant":
                    usage["rounds"] += 1
                    entry_usage = entry.get("message", {}).get("usage", {})
                    usage["input_tokens"] += entry_usage.get("input", 0)
                    usage["output_tokens"] += entry_usage.get("output", 0)
                    usage["cacheRead"] += entry_usage.get("cacheRead", 0)
                    usage["cacheWrite"] += entry_usage.get("cacheWrite", 0)
                    usage["total_tokens"] += entry_usage.get("totalTokens", 0)
    except json.JSONDecodeError as e:
        print(f"    [usage] Error parsing JSONL file: {e}", file=sys.stderr)
    except IOError as e:
        print(f"    [usage] Error reading JSONL file: {e}", file=sys.stderr)

    return usage


def send_message_with_retry(
    base_url: str, token: str, user: str, message: str, retries: int = 2,
    agent_id: str = DEFAULT_AGENT_ID, session_key: str | None = None
) -> tuple[str, dict]:
    """Call send_message with up to `retries` retries on failure."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return send_message(base_url, token, user, message, agent_id, session_key)
        except Exception as e:
            last_exc = e
            if attempt < retries:
                print(f"    [retry {attempt + 1}/{retries}] {e}", file=sys.stderr)
    raise last_exc


def send_message(
    base_url: str, token: str, user: str, message: str,
    agent_id: str = DEFAULT_AGENT_ID, session_key: str | None = None
) -> tuple[str, dict]:
    """Send a single message to the OpenClaw responses API.

    Returns (reply_text, usage) where usage has input_tokens, output_tokens, total_tokens.
    """
    url = f"{base_url}/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-OpenClaw-Agent-ID": agent_id
    }
    if session_key:
        headers["X-OpenClaw-Session-Key"] = session_key
    payload = {
        "model": "openclaw",
        "input": message,
        "stream": False,
    }
    if user:
        payload["user"] = user

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=6000)
        resp.raise_for_status()
        body = resp.json()
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Connection error to {base_url}: {e}")
    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"Request timeout to {base_url}: {e}")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"HTTP error {e.response.status_code} from {base_url}: {e}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Error parsing response from {base_url}: {e}")

    print(body)
    usage = body.get("usage", {"input_tokens": 0, "output_tokens": 0, "cacheRead": 0, "total_tokens": 0})
    return extract_response_text(body), usage


# ---------------------------------------------------------------------------
# Ingest: load conversations into openclaw
# ---------------------------------------------------------------------------

def run_ingest(
    args: argparse.Namespace,
) -> None:
    session_range = parse_session_range(args.sessions) if args.sessions else None

    # Handle ingest record operations
    if args.clear_ingest_record:
        ingest_record = {}
        save_ingest_record(ingest_record)
        print(f"[INFO] All existing ingest records cleared", file=sys.stderr)
    else:
        ingest_record = load_ingest_record()

    if args.input.endswith(".json"):
        samples = load_locomo_data(args.input, args.sample)
        results = []
        skipped_count = 0

        for item in samples:
            sample_id = item["sample_id"]
            user_key = args.user or "eval-1"
            sessions = build_session_messages(item, session_range, tail=args.tail)

            print(f"\n=== Sample {sample_id} ===", file=sys.stderr)
            print(f"    user: {user_key}", file=sys.stderr)
            print(f"    agent: {args.agent_id}", file=sys.stderr)
            print(f"    {len(sessions)} session(s) to ingest", file=sys.stderr)

            session_id = None
            for sess in sessions:
                meta = sess["meta"]
                msg = sess["message"]
                label = f"{meta['session_key']} ({meta['date_time']})"

                # Skip already ingested sessions unless force-ingest is enabled
                if not args.force_ingest and is_already_ingested(args.agent_id, user_key, sample_id, meta['session_key'], ingest_record):
                    print(f"  [{label}] [SKIP] already ingested (use --force-ingest to reprocess)", file=sys.stderr)
                    skipped_count += 1
                    continue

                preview = msg.replace("\n", " | ")[:80]
                print(f"  [{label}] {preview}...", file=sys.stderr)

                try:
                    reply, usage = send_message(args.base_url, args.token, user_key, msg, args.agent_id)
                    print(f"    -> {reply[:80]}{'...' if len(reply) > 80 else ''}", file=sys.stderr)
                    results.append({
                        "sample_id": sample_id,
                        "session": meta["session_key"],
                        "user": user_key,
                        "reply": reply,
                        "usage": usage,
                    })
                    # Mark as successfully ingested
                    mark_ingested(args.agent_id, user_key, sample_id, meta['session_key'], ingest_record, {
                        "mode": "openclaw",
                        "date_time": meta['date_time'],
                        "usage": usage
                    })
                except Exception as e:
                    print(f"    -> [ERROR] {e}", file=sys.stderr)
                    results.append({
                        "sample_id": sample_id,
                        "session": meta["session_key"],
                        "user": user_key,
                        "reply": f"[ERROR] {e}",
                        "usage": {},
                    })

                if session_id is None:
                    session_id = get_session_id(user_key, args.agent_id)
                if session_id:
                    reset_session(session_id, args.agent_id)

        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    for r in results:
                        f.write(f"[{r['sample_id']}/{r['session']}] user={r['user']}\n")
                        f.write(f"  {r['reply']}\n\n")
                print(f"Results written to {args.output}", file=sys.stderr)

                json_path = args.output + ".json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
                print(f"Results (JSON) written to {json_path}", file=sys.stderr)
            except IOError as e:
                print(f"Warning: Error writing output files: {e}", file=sys.stderr)

        # Save ingest record
        save_ingest_record(ingest_record)
        total_processed = len(results) + skipped_count
        print(f"\n=== Ingest summary ===", file=sys.stderr)
        print(f"Total sessions: {total_processed}", file=sys.stderr)
        print(f"Completed: {len(results)}", file=sys.stderr)
        print(f"Skipped (already ingested): {skipped_count}", file=sys.stderr)

    else:
        # Original txt mode
        sessions = parse_test_file(args.input)
        print(f"Running {len(sessions)} session(s)", file=sys.stderr)

        results = []
        for idx, session in enumerate(sessions, start=1):
            session_key = args.user or "eval-1"
            print(f"--- Session {idx} (user={session_key}) ---", file=sys.stderr)

            session_id = None
            turns = []
            for msg in session["messages"]:
                print(f"  [user] {msg}", file=sys.stderr)
                try:
                    reply, _usage = send_message(args.base_url, args.token, session_key, msg, args.agent_id)
                    print(f"  [assistant] {reply[:80]}{'...' if len(reply) > 80 else ''}", file=sys.stderr)
                    turns.append(("user", msg))
                    turns.append(("assistant", reply))
                except Exception as e:
                    print(f"  [ERROR] {e}", file=sys.stderr)
                    turns.append(("user", msg))
                    turns.append(("error", str(e)))
                    break

            if session_id is None:
                session_id = get_session_id(session_key, args.agent_id)
            if session_id:
                reset_session(session_id, args.agent_id)

            results.append({"index": idx, "turns": turns, "evals": session["evals"]})

        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    for r in results:
                        f.write(f"=== Session {r['index']} ===\n")
                        for role, text in r["turns"]:
                            f.write(f"[{role}] {text}\n")
                        for ev in r["evals"]:
                            f.write(f"[eval] {ev}\n")
                        f.write("\n")
                print(f"\nResults written to {args.output}", file=sys.stderr)
            except IOError as e:
                print(f"Warning: Error writing output file: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# QA: run QA questions and compare with expected answers
# ---------------------------------------------------------------------------

def process_single_question(
    sample_id: str,
    sample_idx: int,
    original_qi: int,
    qa: dict,
    args: argparse.Namespace,
    csv_path: str,
    question_time: str | None = None,
) -> dict:
    """Process a single QA question. Returns the record."""
    question = qa["question"]
    expected = str(qa["answer"])
    category = qa.get("category", "")
    evidence = qa.get("evidence", [])

    # Generate unique session_key based on sample_id + question_index
    session_key = f"qa-{sample_id}-q{original_qi}"
    user_key = args.user or f"eval-{sample_idx}"

    print(f"  [{sample_idx}] Q{original_qi}: {question[:60]}{'...' if len(question) > 60 else ''}", file=sys.stderr)
    # 如果有 question_time，注入到 prompt 中
    if question_time:
        input_msg = f"Current date: {question_time}. Answer the question directly: {question}"
    else:
        input_msg = f"Answer the question directly: {question}"

    jsonl_filename = ""
    elapsed_seconds = 0.0
    rounds = 0
    started_at = time.perf_counter()
    try:
        response, api_usage = send_message_with_retry(
            args.base_url, args.token, sample_id, input_msg, 2, args.agent_id, session_key
        )
        elapsed_seconds = time.perf_counter() - started_at
        print(f"  [{sample_idx}]   A: {response[:60]}{'...' if len(response) > 60 else ''}", file=sys.stderr)

        # Get sessionFile path from sessions.json using session_key
        session_file_path = get_session_id_from_key(session_key, user_key, args.agent_id)
        jsonl_filename = ""

        # Archive the session file if we found it
        if session_file_path:
            jsonl_filename = reset_session(session_file_path, args.agent_id)

        # Calculate usage/rounds from JSONL file if available, otherwise use API usage
        if jsonl_filename and session_file_path:
            usage = calculate_session_metrics_from_jsonl(
                os.path.join(os.path.dirname(session_file_path), jsonl_filename),
                args.agent_id,
            )
            rounds = usage.pop("rounds", 0)
            print(f"  [{sample_idx}]   tokens (from JSONL): in={usage['input_tokens']} out={usage['output_tokens']} cacheRead={usage['cacheRead']} cacheWrite={usage['cacheWrite']} total={usage['total_tokens']} rounds={rounds}", file=sys.stderr)
        else:
            usage = {
                "input_tokens": api_usage.get("input_tokens", 0),
                "output_tokens": api_usage.get("output_tokens", 0),
                "cacheRead": api_usage.get("cacheRead", 0),
                "cacheWrite": api_usage.get("cacheWrite", 0),
                "total_tokens": api_usage.get("total_tokens", 0),
            }
            rounds = 1 if response and not response.startswith("[ERROR]") else 0
            print(f"  [{sample_idx}]   tokens (from API): in={usage['input_tokens']} out={usage['output_tokens']} cacheRead={usage['cacheRead']} cacheWrite={usage['cacheWrite']} total={usage['total_tokens']} rounds={rounds}", file=sys.stderr)

    except Exception as e:
        elapsed_seconds = time.perf_counter() - started_at
        response = f"[ERROR] {e}"
        usage = {}
        jsonl_filename = ""
        print(f"  [{sample_idx}]   A: {response}", file=sys.stderr)

    record = {
        "sample_id": sample_id,
        "sample_idx": sample_idx,
        "qi": original_qi,
        "question": question,
        "expected": expected,
        "response": response,
        "category": category,
        "evidence": evidence,
        "usage": usage,
        "elapsed_seconds": elapsed_seconds,
        "rounds": rounds,
        "jsonl_filename": jsonl_filename,
    }

    # Save to CSV with lock for thread safety
    with csv_lock:
        save_record_to_csv(csv_path, record)
    print(f"  [{sample_idx}]   Saved to CSV: Q{original_qi}", file=sys.stderr)

    return record


def run_sample_qa(
    item: dict,
    sample_idx: int,
    args: argparse.Namespace,
    executed_records: set,
    csv_path: str,
) -> tuple[list[dict], dict]:
    """Process QA for a single sample with concurrent question execution. Returns (records, sample_usage)."""
    sample_id = item["sample_id"]
    user_key = args.user or f"eval-{sample_idx}"
    question_time = get_sample_question_time(item)
    qas = [q for q in item.get("qa", []) if str(q.get("category", "")) != "5"]
    if args.count is not None:
        qas = qas[:args.count]

    # Filter out already executed questions
    filtered_qas = []
    for qi, qa in enumerate(qas, start=1):
        if (sample_id, qi) not in executed_records:
            filtered_qas.append((qi, qa))
        else:
            print(f"  [{sample_idx}] Skipping Q{qi}: already executed", file=sys.stderr)

    qas = filtered_qas
    if not qas:
        print(f"\n=== Sample {sample_id} [{sample_idx}] (user={user_key}) ===", file=sys.stderr)
        print(f"    All QA questions already executed, skipping sample.", file=sys.stderr)
        return [], {"input_tokens": 0, "output_tokens": 0, "cacheRead": 0, "cacheWrite": 0, "total_tokens": 0}

    print(f"\n=== Sample {sample_id} [{sample_idx}] (user={user_key}) ===", file=sys.stderr)
    if question_time:
        print(f"    Question time context: {question_time}", file=sys.stderr)
    print(f"    Running {len(qas)} QA question(s) with max {args.parallel} workers...", file=sys.stderr)

    records = []
    sample_usage = {"input_tokens": 0, "output_tokens": 0, "cacheRead": 0, "cacheWrite": 0, "total_tokens": 0}

    # Use ThreadPoolExecutor for concurrent question execution
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = []
        for original_qi, qa in qas:
            future = executor.submit(
                process_single_question,
                sample_id, sample_idx, original_qi, qa, args, csv_path, question_time
            )
            futures.append(future)

        # Collect results
        for future in as_completed(futures):
            try:
                record = future.result()
                records.append(record)
                # Accumulate usage
                usage = record.get("usage", {})
                for k in sample_usage:
                    sample_usage[k] += usage.get(k, 0)
            except Exception as e:
                print(f"  [{sample_idx}] Error in question task: {e}", file=sys.stderr)

    return records, sample_usage


def load_executed_records(csv_path: str) -> set:
    """Load already executed records from CSV file, returns set of (sample_id, qi) tuples."""
    executed = set()
    if os.path.exists(csv_path):
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Use sample_id and question index as unique identifier
                    executed.add((row["sample_id"], int(row["qi"])))
        except csv.Error as e:
            print(f"Warning: Error reading CSV file {csv_path}: {e}", file=sys.stderr)
        except IOError as e:
            print(f"Warning: Error reading CSV file {csv_path}: {e}", file=sys.stderr)
    return executed


def save_record_to_csv(csv_path: str, record: dict) -> None:
    """Save a single QA record to CSV file."""
    file_exists = os.path.exists(csv_path)
    fieldnames = [
        "sample_id", "sample_idx", "qi", "question", "expected",
        "response", "category", "evidence", "elapsed_seconds", "rounds", "input_tokens",
        "output_tokens", "cacheRead", "cacheWrite", "total_tokens",
        "timestamp", "jsonl_filename", "result", "reasoning"
    ]

    # Flatten usage fields
    flat_record = record.copy()
    usage = flat_record.pop("usage", {})
    flat_record["elapsed_seconds"] = f"{flat_record.get('elapsed_seconds', 0.0):.3f}"
    flat_record["rounds"] = flat_record.get("rounds", 0)
    flat_record["input_tokens"] = usage.get("input_tokens", 0)
    flat_record["output_tokens"] = usage.get("output_tokens", 0)
    flat_record["cacheRead"] = usage.get("cacheRead", 0)
    flat_record["cacheWrite"] = usage.get("cacheWrite", 0)
    flat_record["total_tokens"] = usage.get("total_tokens", 0)
    flat_record["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    flat_record["jsonl_filename"] = flat_record.get("jsonl_filename", "")
    flat_record["result"] = ""  # 默认为空，由 judge.py 填充
    flat_record["reasoning"] = ""  # 默认为空，由 judge.py 填充

    try:
        with open(csv_path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(flat_record)
            f.flush()
    except csv.Error as e:
        print(f"Warning: Error writing to CSV file {csv_path}: {e}", file=sys.stderr)
    except IOError as e:
        print(f"Warning: Error writing to CSV file {csv_path}: {e}", file=sys.stderr)


def run_qa(
    args: argparse.Namespace,
) -> None:
    """QA only: send questions and get responses. No ingestion."""
    if not args.input.endswith(".json"):
        print("Error: QA mode only works with LoCoMo JSON files", file=sys.stderr)
        sys.exit(1)

    # Ensure parallel is within reasonable bounds (1-40)
    args.parallel = max(1, min(40, args.parallel))

    samples = load_locomo_data(args.input, args.sample)
    print(f"    user: {args.user or 'eval-{sample_idx}'}", file=sys.stderr)
    print(f"    running with {args.parallel} concurrent workers", file=sys.stderr)

    # Load already executed records from CSV
    csv_path = f"{args.output}.csv" if args.output else args.default_csv_path
    # 确保输出目录存在
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    executed_records = load_executed_records(csv_path)
    print(f"    Loaded {len(executed_records)} already executed records from {csv_path}", file=sys.stderr)

    results_list = []
    for idx, item in enumerate(samples):
        result = run_sample_qa(item, idx + 1, args, executed_records, csv_path)
        results_list.append(result)

    total_usage = {"input_tokens": 0, "output_tokens": 0, "cacheRead": 0, "cacheWrite": 0, "total_tokens": 0}
    for _, sample_usage in results_list:
        for k in total_usage:
            total_usage[k] += sample_usage[k]

    print(f"\n    total tokens: in={total_usage['input_tokens']} out={total_usage['output_tokens']} total={total_usage['total_tokens']}", file=sys.stderr)

    # Generate timestamp once for all backups
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    import shutil

    # Backup CSV file with timestamp
    if os.path.exists(csv_path):
        csv_path_obj = Path(csv_path)
        backup_csv_path = csv_path_obj.parent / f"{csv_path_obj.stem}_{timestamp}{csv_path_obj.suffix}"
        try:
            shutil.copy2(csv_path, backup_csv_path)
            print(f"    CSV backed up to: {backup_csv_path}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Failed to backup CSV file: {e}", file=sys.stderr)

    if args.output:
        # Backup output summary file too
        if os.path.exists(args.output):
            output_path_obj = Path(args.output)
            backup_output_path = output_path_obj.parent / f"{output_path_obj.stem}_{timestamp}{output_path_obj.suffix}"
            try:
                shutil.copy2(args.output, backup_output_path)
                print(f"    Summary backed up to: {backup_output_path}", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Failed to backup summary file: {e}", file=sys.stderr)

        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write("=== TOTAL USAGE ===\n")
                f.write(f"input_tokens: {total_usage['input_tokens']}\n")
                f.write(f"output_tokens: {total_usage['output_tokens']}\n")
                f.write(f"total_tokens: {total_usage['total_tokens']}\n")
            print(f"Summary written to {args.output}", file=sys.stderr)
        except IOError as e:
            print(f"Warning: Error writing output file: {e}", file=sys.stderr)
    else:
        print("\nDone (no output file requested).", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_session_range(s: str) -> tuple[int, int]:
    """Parse '1-4' or '3' into (lo, hi) inclusive tuple."""
    if "-" in s:
        lo, hi = s.split("-", 1)
        return int(lo), int(hi)
    n = int(s)
    return n, n


def main():
    # 基于脚本所在目录计算默认 CSV 路径
    script_dir = Path(__file__).parent.resolve()
    default_csv_path = str(script_dir / "result" / "qa_results.csv")

    parser = argparse.ArgumentParser(description="Evaluate OpenClaw responses")
    parser.add_argument("mode", choices=["ingest", "qa"], help="Mode: ingest (load conversations) or qa (run QA eval)")
    parser.add_argument("input", help="Path to test file (.txt or .json)")
    parser.add_argument(
        "--output",
        default=None,
        help="Path to output file (omit to skip writing)",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="OpenClaw gateway base URL (default: http://127.0.0.1:18789)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OPENCLAW_GATEWAY_TOKEN"),
        help="Auth token (or set OPENCLAW_GATEWAY_TOKEN env var)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="LoCoMo: sample index (0-based). Default: all samples.",
    )
    parser.add_argument(
        "--sessions",
        default=None,
        help="LoCoMo: session range, e.g. '1-4' or '3'. Default: all sessions.",
    )
    parser.add_argument(
        "--tail",
        default="[]",
        help="Tail message appended after conversation messages per session (default: '[]')",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="QA mode: number of QA questions to run. Default: all.",
    )
    parser.add_argument(
        "--user",
        default="eval-1",
        help="QA mode: user UUID from a prior ingest run to target.",
    )
    parser.add_argument(
        "-p", "--parallel",
        type=int,
        default=10,
        metavar="N",
        help="QA mode: number of questions to process concurrently (max 40, default 10).",
    )
    parser.add_argument(
        "--agent-id",
        default=DEFAULT_AGENT_ID,
        help="X-OpenClaw-Agent-ID header value for API requests (default: locomo-eval)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session ID for API requests (ingest mode only).",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        default=False,
        help="Ingest mode: force re-ingest even if already recorded as completed",
    )
    parser.add_argument(
        "--clear-ingest-record",
        action="store_true",
        default=False,
        help="Clear all existing ingest records before running",
    )
    args = parser.parse_args()
    # 添加默认 CSV 路径到 args
    args.default_csv_path = default_csv_path

    if not args.token:
        print("Error: --token or OPENCLAW_GATEWAY_TOKEN env var is required", file=sys.stderr)
        sys.exit(1)

    if args.mode == "ingest":
        run_ingest(args)
    elif args.mode == "qa":
        run_qa(args)


if __name__ == "__main__":
    main()
