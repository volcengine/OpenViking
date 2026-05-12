"""
Ingest LoCoMo conversations into Claude Code's auto-memory system.

Same flow as openclaw: for each sample, send each session's bundled conversation
to Claude Code via `claude -p`, letting its auto-memory system extract and persist
memories. Each session is a separate `claude -p` invocation (independent conversation).

All samples share one isolated project directory so Claude Code accumulates memories
from all sessions into a single memory store — matching how openclaw uses one agent
namespace per user.

Usage:
    python ingest.py
    python ingest.py --sample 0
    python ingest.py --sample conv-26
    python ingest.py --api-url http://localhost:8000 --api-key sk-xxx
    python ingest.py --force-ingest   # re-ingest even if already done
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "locomo10.json")
DEFAULT_PROJECT_ROOT = "/tmp/locomo-eval"
DEFAULT_HOME = "/tmp/claude-eval-home"
DEFAULT_INGEST_RECORD = str(SCRIPT_DIR / "result" / ".ingest_record.json")
DEFAULT_SUCCESS_CSV = str(SCRIPT_DIR / "result" / "ingest_success.csv")
DEFAULT_ERROR_LOG = str(SCRIPT_DIR / "result" / "ingest_errors.log")

SUCCESS_CSV_FIELDS = [
    "timestamp",
    "sample_id",
    "session",
    "date_time",
    "speakers",
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_cost_usd",
    "duration_ms",
    "response_preview",
]


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


def format_locomo_message(msg: dict) -> str:
    speaker = msg.get("speaker", "unknown")
    text = msg.get("text", "")
    line = f"{speaker}: {text}"

    img_urls = msg.get("img_url", [])
    if isinstance(img_urls, str) and img_urls:
        img_urls = [img_urls]
    for _url in img_urls or []:
        caption = msg.get("blip_caption", "")
        if caption:
            line += f"\n  (image: {caption})"

    return line


def build_session_messages(item: dict) -> list[dict]:
    """Build bundled session messages for one LoCoMo sample.

    Same format as openclaw: each session becomes a single string with all
    messages concatenated, prefixed with date/time header.
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
        raw_messages = conv[sk]
        if not isinstance(raw_messages, list) or not raw_messages:
            continue

        dt_key = f"{sk}_date_time"
        date_time = conv.get(dt_key, "")

        parts = [f"[group chat conversation: {date_time}]"]
        for msg in raw_messages:
            parts.append(format_locomo_message(msg))
        combined = "\n\n".join(parts)

        sessions.append(
            {
                "message": combined,
                "meta": {
                    "sample_id": item["sample_id"],
                    "session_key": sk,
                    "session_num": sess_num,
                    "date_time": date_time,
                    "speakers": f"{speaker_a} & {speaker_b}",
                },
            }
        )

    return sessions


# ---------------------------------------------------------------------------
# Ingest record (deduplication)
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
    key = f"claudecode:{sample_id}:{session_key}"
    return key in record and record[key].get("success", False)


def mark_ingested(sample_id: str, session_key: str, record: dict, meta: dict) -> None:
    key = f"claudecode:{sample_id}:{session_key}"
    record[key] = {"success": True, "timestamp": int(time.time()), "meta": meta}


def write_success_csv(record: dict, csv_path: str) -> None:
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUCCESS_CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)


def write_error_log(path: str, sample_id: str, session_key: str, error: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] ERROR [{sample_id}/{session_key}]: {error}\n")


# ---------------------------------------------------------------------------
# Claude Code invocation
# ---------------------------------------------------------------------------


def _run_claude_once(
    prompt: str,
    project_dir: str,
    env: dict,
    model: Optional[str] = None,
    timeout_sec: int = 600,
    extra_flags: Optional[list] = None,
) -> dict:
    """Single claude -p invocation. Returns parsed result dict."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--strict-mcp-config",
    ]
    if extra_flags:
        cmd.extend(extra_flags)
    if model:
        cmd.extend(["--model", model])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=project_dir,
            env=env,
        )

        stdout = result.stdout.strip()
        if not stdout:
            return {
                "response": f"[ERROR] empty stdout: {result.stderr[:200]}",
                "usage": {},
                "duration_ms": 0,
                "cost": 0,
            }

        try:
            body = json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "response": f"[ERROR] JSON parse: {stdout[:200]}",
                "usage": {},
                "duration_ms": 0,
                "cost": 0,
            }

        if body.get("is_error"):
            return {
                "response": f"[ERROR] {body.get('result', 'unknown')}",
                "usage": body.get("usage", {}),
                "duration_ms": body.get("duration_ms", 0),
                "cost": body.get("total_cost_usd", 0),
            }

        return {
            "response": body.get("result", ""),
            "usage": body.get("usage", {}),
            "duration_ms": body.get("duration_ms", 0),
            "num_turns": body.get("num_turns", 0),
            "cost": body.get("total_cost_usd", 0),
        }

    except subprocess.TimeoutExpired:
        return {"response": "[TIMEOUT]", "usage": {}, "duration_ms": timeout_sec * 1000, "cost": 0}
    except Exception as e:
        return {"response": f"[ERROR] {e}", "usage": {}, "duration_ms": 0, "cost": 0}


def _build_env(
    home_dir: str, api_key: Optional[str], auth_token: Optional[str], api_url: Optional[str]
) -> dict:
    env = os.environ.copy()
    env["HOME"] = home_dir
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    if auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    if api_url:
        env["ANTHROPIC_BASE_URL"] = api_url
    return env


def _run_with_retry(
    prompt: str,
    project_dir: str,
    env: dict,
    model: Optional[str] = None,
    timeout_sec: int = 600,
    retries: int = 2,
    extra_flags: Optional[list] = None,
) -> dict:
    """Run claude -p with retries on TIMEOUT/ERROR."""
    for attempt in range(retries + 1):
        result = _run_claude_once(prompt, project_dir, env, model, timeout_sec, extra_flags)
        resp = result["response"]
        if not resp.startswith("[TIMEOUT]") and not resp.startswith("[ERROR]"):
            return result
        if attempt < retries:
            print(f"    [retry {attempt + 1}/{retries}] {resp[:80]}", file=sys.stderr)
    return result


def run_claude_ingest(
    message: str,
    project_dir: str,
    home_dir: str,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    auth_token: Optional[str] = None,
    model: Optional[str] = None,
    timeout_sec: int = 600,
    retries: int = 2,
) -> dict:
    """Send a session's conversation to Claude Code and let auto-memory process it."""
    env = _build_env(home_dir, api_key, auth_token, api_url)
    return _run_with_retry(message, project_dir, env, model, timeout_sec, retries)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Ingest LoCoMo conversations into Claude Code auto-memory"
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_DATA_PATH,
        help=f"Path to locomo JSON (default: {DEFAULT_DATA_PATH})",
    )
    parser.add_argument(
        "--sample",
        default=None,
        help="Sample index (0-based) or sample_id. Default: all.",
    )
    parser.add_argument(
        "--project-root",
        default=DEFAULT_PROJECT_ROOT,
        help=f"Root for sample project dirs (default: {DEFAULT_PROJECT_ROOT})",
    )
    parser.add_argument(
        "--home",
        default=DEFAULT_HOME,
        help=f"Isolated HOME directory (default: {DEFAULT_HOME})",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Custom API base URL (ANTHROPIC_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Auth token (ANTHROPIC_AUTH_TOKEN), alternative to --api-key",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name/alias (e.g. sonnet, opus)",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        help="Re-ingest even if already recorded as done",
    )
    parser.add_argument(
        "--clear-ingest-record",
        action="store_true",
        help="Clear all ingest records before running",
    )
    parser.add_argument(
        "--record",
        default=DEFAULT_INGEST_RECORD,
        help=f"Ingest record file (default: {DEFAULT_INGEST_RECORD})",
    )
    parser.add_argument(
        "--success-csv",
        default=DEFAULT_SUCCESS_CSV,
        help=f"Success CSV path (default: {DEFAULT_SUCCESS_CSV})",
    )
    parser.add_argument(
        "--error-log",
        default=DEFAULT_ERROR_LOG,
        help=f"Error log path (default: {DEFAULT_ERROR_LOG})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout per session in seconds (default: 600)",
    )
    parser.add_argument(
        "--prompt-prefix",
        default="",
        help="Optional prefix prepended before each session's conversation text "
        "(e.g. to nudge auto-memory). Default: empty (bare conversation).",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    auth_token = args.auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    api_url = args.api_url or os.environ.get("ANTHROPIC_BASE_URL")

    if not api_key and not auth_token:
        print(
            "Error: API key required (--api-key/ANTHROPIC_API_KEY or --auth-token/ANTHROPIC_AUTH_TOKEN)",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.exists(args.input):
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Ingest record
    if args.clear_ingest_record:
        ingest_record: dict = {}
        save_ingest_record(ingest_record, args.record)
        print("[INFO] Cleared ingest records", file=sys.stderr)
    else:
        ingest_record = load_ingest_record(args.record)

    samples = load_locomo_data(args.input, args.sample)
    print(f"[INFO] Loaded {len(samples)} sample(s)", file=sys.stderr)
    print(f"[INFO] Project root: {args.project_root}", file=sys.stderr)
    print(f"[INFO] HOME: {args.home}", file=sys.stderr)

    # Ensure isolated HOME has .claude directory
    os.makedirs(os.path.join(args.home, ".claude"), exist_ok=True)

    total_sessions = 0
    success_count = 0
    skip_count = 0
    error_count = 0

    for item in samples:
        sample_id = item["sample_id"]
        sessions = build_session_messages(item)

        # All samples share one project dir so memories accumulate together
        project_dir = os.path.join(args.project_root, sample_id)
        os.makedirs(project_dir, exist_ok=True)

        print(f"\n=== Sample {sample_id} ({len(sessions)} sessions) ===", file=sys.stderr)

        for sess in sessions:
            meta = sess["meta"]
            msg = sess["message"]
            session_key = meta["session_key"]
            label = f"{session_key} ({meta['date_time']})"
            total_sessions += 1

            if not args.force_ingest and is_already_ingested(sample_id, session_key, ingest_record):
                print(f"  [{label}] SKIP (already ingested)", file=sys.stderr)
                skip_count += 1
                continue

            preview = msg.replace("\n", " | ")[:80]
            print(f"  [{label}] {preview}...", file=sys.stderr)

            send_msg = f"{args.prompt_prefix}\n\n{msg}" if args.prompt_prefix else msg

            t0 = time.time()
            result = run_claude_ingest(
                send_msg,
                project_dir,
                args.home,
                api_url=api_url,
                api_key=api_key,
                auth_token=auth_token,
                model=args.model,
                timeout_sec=args.timeout,
            )
            elapsed = time.time() - t0

            response = result["response"]
            usage = result.get("usage", {})

            if response.startswith("[ERROR]") or response.startswith("[TIMEOUT]"):
                print(f"    -> {response[:80]}  ({elapsed:.1f}s)", file=sys.stderr)
                write_error_log(args.error_log, sample_id, session_key, response)
                error_count += 1
            else:
                print(
                    f"    -> {response[:80]}{'...' if len(response) > 80 else ''}"
                    f"  ({elapsed:.1f}s)",
                    file=sys.stderr,
                )
                mark_ingested(
                    sample_id,
                    session_key,
                    ingest_record,
                    {
                        "date_time": meta["date_time"],
                        "duration_ms": result.get("duration_ms", 0),
                    },
                )
                save_ingest_record(ingest_record, args.record)

                write_success_csv(
                    {
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "sample_id": sample_id,
                        "session": session_key,
                        "date_time": meta["date_time"],
                        "speakers": meta["speakers"],
                        "input_tokens": usage.get("input_tokens", 0),
                        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "reasoning_tokens": usage.get("reasoning_tokens", 0),
                        "total_cost_usd": result.get("cost", 0),
                        "duration_ms": result.get("duration_ms", 0),
                        "response_preview": response[:100],
                    },
                    args.success_csv,
                )

                success_count += 1

    print("\n=== Ingest summary ===", file=sys.stderr)
    print(f"  Total sessions: {total_sessions}", file=sys.stderr)
    print(f"  Succeeded:      {success_count}", file=sys.stderr)
    print(f"  Skipped:        {skip_count}", file=sys.stderr)
    print(f"  Failed:         {error_count}", file=sys.stderr)

    # Write sample mapping for eval.py
    mapping_path = os.path.join(args.project_root, "sample_mapping.json")
    mapping = {}
    for item in samples:
        sid = item["sample_id"]
        mapping[sid] = {
            "sample_id": sid,
            "project_dir": os.path.join(args.project_root, sid),
        }
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
    print(f"  Mapping: {mapping_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
