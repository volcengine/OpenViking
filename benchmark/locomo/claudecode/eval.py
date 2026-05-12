"""
Run LoCoMo QA evaluation against Claude Code.

Each question is sent to `claude -p` in the corresponding sample's isolated
project directory. Claude Code's auto-memory loads MEMORY.md from that
project's memory directory, and it can Read individual session files.

Usage:
    python eval.py --input ../data/locomo10.json
    python eval.py --sample 0 --parallel 5
    python eval.py --sample conv-26 --api-url http://localhost:8000 --api-key sk-xxx
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "locomo10.json")
DEFAULT_PROJECT_ROOT = "/tmp/locomo-eval"
DEFAULT_HOME = "/tmp/claude-eval-home"
DEFAULT_OUTPUT = str(SCRIPT_DIR / "result" / "qa_results.csv")

csv_lock = Lock()

FIELDNAMES = [
    "sample_id",
    "question_index",
    "question",
    "answer",
    "category",
    "question_time",
    "evidence",
    "response",
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_cost_usd",
    "elapsed_seconds",
    "num_turns",
    "ov_recall_hooks",
    "ov_mcp_calls",
    "result",
    "reasoning",
]

OV_HOOK_LOG = None  # resolved per-call using home_dir
OV_MCP_LOG = str(SCRIPT_DIR / ".tmp" / "mcp-calls.log")  # observability only


def _count_file_lines(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


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


def parse_locomo_datetime(date_str: str) -> Optional[datetime]:
    try:
        if " on " in date_str:
            date_part = date_str.split(" on ")[-1]
            return datetime.strptime(date_part.strip(), "%d %B, %Y")
    except ValueError:
        pass
    return None


def get_sample_question_time(sample: dict) -> Optional[str]:
    conversation = sample.get("conversation", {})
    session_keys = [
        k for k in conversation.keys() if k.startswith("session_") and "date_time" not in k
    ]
    if not session_keys:
        return None

    def get_num(key):
        try:
            return int(key.replace("session_", ""))
        except ValueError:
            return 0

    session_keys.sort(key=get_num, reverse=True)
    for sk in session_keys:
        if conversation.get(sk):
            dt_key = f"{sk.split('_')[0]}_{sk.split('_')[1]}_date_time"
            sess_num = get_num(sk)
            dt_key = f"session_{sess_num}_date_time"
            date_str = conversation.get(dt_key)
            if date_str:
                dt = parse_locomo_datetime(date_str)
                if dt:
                    return dt.strftime("%Y-%m-%d")
    return None


def load_qa_items(
    data: list[dict],
    count: Optional[int] = None,
    question_index: Optional[int] = None,
) -> list[dict]:
    items = []
    for sample in data:
        sample_id = sample.get("sample_id", "")
        question_time = get_sample_question_time(sample)

        for qi, qa in enumerate(sample.get("qa", [])):
            if question_index is not None and qi != question_index:
                continue
            category = str(qa.get("category", ""))
            if category == "5":
                continue
            items.append(
                {
                    "sample_id": sample_id,
                    "question_index": qi,
                    "question": qa["question"],
                    "answer": str(qa["answer"]),
                    "category": category,
                    "evidence": qa.get("evidence", []),
                    "question_time": question_time,
                }
            )

    if count is not None:
        items = items[:count]
    return items


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def load_processed_keys(output_path: str) -> set[str]:
    processed: set[str] = set()
    if not os.path.exists(output_path):
        return processed
    try:
        with open(output_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                resp = row.get("response", "")
                if resp and not resp.startswith("[ERROR]"):
                    key = f"{row.get('sample_id', '')}_{row.get('question_index', '')}"
                    processed.add(key)
    except Exception:
        pass
    return processed


def append_row(output_path: str, row: dict) -> None:
    with csv_lock:
        file_exists = os.path.exists(output_path)
        with open(output_path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
            f.flush()


# ---------------------------------------------------------------------------
# Claude Code invocation
# ---------------------------------------------------------------------------


def _run_claude_once(
    prompt: str,
    project_dir: str,
    env: dict,
    model: Optional[str] = None,
    timeout_sec: int = 300,
    extra_flags: Optional[list] = None,
) -> dict:
    """Single claude -p invocation."""
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
                "num_turns": 0,
                "cost": 0,
            }

        try:
            body = json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "response": f"[ERROR] JSON parse: {stdout[:200]}",
                "usage": {},
                "duration_ms": 0,
                "num_turns": 0,
                "cost": 0,
            }

        if body.get("is_error"):
            return {
                "response": f"[ERROR] {body.get('result', 'unknown')}",
                "usage": body.get("usage", {}),
                "duration_ms": body.get("duration_ms", 0),
                "num_turns": body.get("num_turns", 0),
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
        return {
            "response": "[TIMEOUT]",
            "usage": {},
            "duration_ms": timeout_sec * 1000,
            "num_turns": 0,
            "cost": 0,
        }
    except Exception as e:
        return {
            "response": f"[ERROR] {e}",
            "usage": {},
            "duration_ms": 0,
            "num_turns": 0,
            "cost": 0,
        }


def run_claude_code(
    prompt: str,
    project_dir: str,
    home_dir: str,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    auth_token: Optional[str] = None,
    model: Optional[str] = None,
    timeout_sec: int = 300,
    retries: int = 2,
    hooks_settings: Optional[str] = None,
    mcp_config: Optional[str] = None,
    ov_config: Optional[str] = None,
    ov_cli_config: Optional[str] = None,
    ov_agent_id: Optional[str] = None,
) -> dict:
    """Run claude -p with retries on TIMEOUT/ERROR."""
    env = os.environ.copy()
    env["HOME"] = home_dir
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    if auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    if api_url:
        env["ANTHROPIC_BASE_URL"] = api_url
    if ov_config:
        env["OPENVIKING_CONFIG_FILE"] = ov_config
        env["OPENVIKING_MEMORY_ENABLED"] = "1"
        env["OPENVIKING_DEBUG"] = "1"
    if ov_cli_config:
        env["OPENVIKING_CLI_CONFIG_FILE"] = ov_cli_config
    if ov_agent_id:
        env["OPENVIKING_AGENT_ID"] = ov_agent_id
        env["OPENVIKING_USER"] = ov_agent_id
    # Disable detached-worker write path (subprocess.run reaps the worker
    # before persistence). Strip any inherited prod URL/auth that the parent
    # shell's `claude` shell function exports from the default ovcli.conf.
    env["OPENVIKING_WRITE_PATH_ASYNC"] = "0"
    env.pop("OPENVIKING_URL", None)
    env.pop("OPENVIKING_BASE_URL", None)
    env.pop("OPENVIKING_API_KEY", None)
    env.pop("OPENVIKING_BEARER_TOKEN", None)

    extra_flags = []
    if hooks_settings:
        extra_flags.extend(["--settings", hooks_settings])
    if mcp_config:
        extra_flags.extend(["--mcp-config", mcp_config])

    for attempt in range(retries + 1):
        result = _run_claude_once(prompt, project_dir, env, model, timeout_sec, extra_flags)
        resp = result["response"]
        if not resp.startswith("[TIMEOUT]") and not resp.startswith("[ERROR]"):
            return result
        if attempt < retries:
            print(f"    [retry {attempt + 1}/{retries}] {resp[:80]}", file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# Question processing
# ---------------------------------------------------------------------------


def process_question(
    qa: dict,
    project_root: str,
    home_dir: str,
    api_url: Optional[str],
    api_key: Optional[str],
    auth_token: Optional[str],
    model: Optional[str],
    output_path: str,
    timeout_sec: int,
    hooks_settings: Optional[str] = None,
    mcp_config: Optional[str] = None,
    ov_config: Optional[str] = None,
    ov_cli_config: Optional[str] = None,
    ov_shared_id: Optional[str] = None,
    ov_preamble_override: Optional[str] = None,
    shared_cwd: bool = False,
) -> dict:
    sample_id = qa["sample_id"]
    qi = qa["question_index"]
    question = qa["question"]
    question_time = qa.get("question_time")

    project_dir = project_root if shared_cwd else os.path.join(project_root, sample_id)

    if ov_preamble_override is not None:
        ov_preamble = ov_preamble_override + "\n\n" if ov_preamble_override else ""
    elif ov_config:
        ov_preamble = "If context is insufficient, use OpenViking MCP tools or auto-memory files to find more information.\n\n"
    else:
        ov_preamble = ""

    if question_time:
        prompt = (
            f"{ov_preamble}Current date: {question_time}. Answer the question directly: {question}"
        )
    else:
        prompt = f"{ov_preamble}Answer the question directly: {question}"

    print(
        f"  [{sample_id}] Q{qi}: {question[:60]}{'...' if len(question) > 60 else ''}",
        file=sys.stderr,
    )

    hook_log = os.path.join(home_dir, ".openviking", "logs", "cc-hooks.log") if ov_config else ""
    hook_before = _count_file_lines(hook_log) if hook_log else 0
    mcp_before = _count_file_lines(OV_MCP_LOG) if ov_config else 0

    t0 = time.perf_counter()
    result = run_claude_code(
        prompt,
        project_dir,
        home_dir,
        api_url=api_url,
        api_key=api_key,
        auth_token=auth_token,
        model=model,
        timeout_sec=timeout_sec,
        hooks_settings=hooks_settings,
        mcp_config=mcp_config,
        ov_config=ov_config,
        ov_cli_config=ov_cli_config,
        ov_agent_id=(ov_shared_id if ov_shared_id is not None else sample_id),
    )
    elapsed = time.perf_counter() - t0

    hook_delta = _count_file_lines(hook_log) - hook_before if hook_log else 0
    mcp_delta = _count_file_lines(OV_MCP_LOG) - mcp_before if ov_config else 0

    usage = result.get("usage", {})
    response = result["response"]

    print(
        f"  [{sample_id}] A{qi}: {response[:60]}{'...' if len(response) > 60 else ''}"
        f"  ({elapsed:.1f}s)",
        file=sys.stderr,
    )

    row = {
        "sample_id": sample_id,
        "question_index": qi,
        "question": question,
        "answer": qa["answer"],
        "category": qa["category"],
        "question_time": question_time or "",
        "evidence": json.dumps(qa.get("evidence", [])),
        "response": response,
        "input_tokens": usage.get("input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "reasoning_tokens": usage.get("reasoning_tokens", 0),
        "total_cost_usd": result.get("cost", 0),
        "elapsed_seconds": round(elapsed, 2),
        "num_turns": result.get("num_turns", 0),
        "ov_recall_hooks": hook_delta,
        "ov_mcp_calls": mcp_delta,
        "result": "",
        "reasoning": "",
    }

    append_row(output_path, row)
    return row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Run LoCoMo QA evaluation against Claude Code")
    parser.add_argument(
        "--input",
        default=DEFAULT_DATA_PATH,
        help=f"Path to locomo JSON (default: {DEFAULT_DATA_PATH})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--sample",
        default=None,
        help="Sample index (0-based) or sample_id. Default: all.",
    )
    parser.add_argument(
        "--question-index",
        type=int,
        default=None,
        help="Single question index (0-based) within each sample.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Max number of QA items to process.",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        help="Concurrent workers (default: 5, be mindful of API rate limits)",
    )
    parser.add_argument(
        "--project-root",
        default=DEFAULT_PROJECT_ROOT,
        help=f"Root directory for sample project dirs (default: {DEFAULT_PROJECT_ROOT})",
    )
    parser.add_argument(
        "--home",
        default=DEFAULT_HOME,
        help=f"Isolated HOME directory (default: {DEFAULT_HOME})",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Custom Anthropic-compatible API base URL (ANTHROPIC_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (ANTHROPIC_API_KEY). Also reads from env if not specified.",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Auth token (ANTHROPIC_AUTH_TOKEN), alternative to --api-key",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name/alias (e.g. sonnet, opus, claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout per question in seconds (default: 300)",
    )
    parser.add_argument(
        "--hooks-settings",
        default=None,
        help="Path to hooks settings JSON (passed as --settings to claude)",
    )
    parser.add_argument(
        "--mcp-config",
        default=None,
        help="Path to MCP server config JSON (passed as --mcp-config to claude)",
    )
    parser.add_argument(
        "--ov-config",
        default=None,
        help="Path to ov.conf for OpenViking (sets OPENVIKING_CONFIG_FILE env var)",
    )
    parser.add_argument(
        "--ov-cli-config",
        default=None,
        help="Path to ovcli.conf (sets OPENVIKING_CLI_CONFIG_FILE; pin local OV)",
    )
    parser.add_argument(
        "--ov-shared-id",
        default=None,
        help="If set, use this single agent_id/user for all samples (no per-sample isolation). Empty string '' means do NOT set OPENVIKING_AGENT_ID at all (server falls back to 'default').",
    )
    parser.add_argument(
        "--ov-preamble",
        default=None,
        help="Custom preamble for OV-enabled QA (overrides default)",
    )
    parser.add_argument(
        "--shared-cwd",
        action="store_true",
        help="Use --project-root directly as cwd for every sample (no per-sample subdir). "
        "Required when ingest stored CC MEMORY.md in a single shared cwd.",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    auth_token = args.auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    api_url = args.api_url or os.environ.get("ANTHROPIC_BASE_URL")

    if not api_key and not auth_token:
        print(
            "[INFO] No API key/token provided - assuming subscription auth in HOME", file=sys.stderr
        )

    # Verify project root has been ingested (skip in shared-cwd mode where ingest_e2e.py owns it)
    if not args.shared_cwd:
        mapping_path = os.path.join(args.project_root, "sample_mapping.json")
        if not os.path.exists(mapping_path):
            print(
                f"Error: {mapping_path} not found. Run ingest.py first.",
                file=sys.stderr,
            )
            sys.exit(1)

    data = load_locomo_data(args.input, args.sample)
    qa_items = load_qa_items(data, count=args.count, question_index=args.question_index)
    print(f"[INFO] {len(qa_items)} QA items loaded", file=sys.stderr)

    # Ensure output directory
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    # Remove ERROR rows from existing output
    if os.path.exists(args.output):
        with open(args.output, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or FIELDNAMES
            clean_rows = [r for r in reader if not r.get("response", "").startswith("[ERROR]")]
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(clean_rows)

    processed = load_processed_keys(args.output)
    remaining = [
        qa for qa in qa_items if f"{qa['sample_id']}_{qa['question_index']}" not in processed
    ]
    print(
        f"[INFO] {len(processed)} already done, {len(remaining)} remaining",
        file=sys.stderr,
    )

    if not remaining:
        print("[INFO] All questions already processed.", file=sys.stderr)
        return

    print(
        f"[INFO] Running with {args.parallel} workers, "
        f"model={args.model or 'default'}, "
        f"api_url={api_url or 'default'}",
        file=sys.stderr,
    )

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {}
        for qa in remaining:
            fut = executor.submit(
                process_question,
                qa,
                args.project_root,
                args.home,
                api_url,
                api_key,
                auth_token,
                args.model,
                args.output,
                args.timeout,
                hooks_settings=args.hooks_settings,
                mcp_config=args.mcp_config,
                ov_config=args.ov_config,
                ov_cli_config=args.ov_cli_config,
                ov_shared_id=args.ov_shared_id,
                ov_preamble_override=args.ov_preamble,
                shared_cwd=args.shared_cwd,
            )
            futures[fut] = qa

        done_count = 0
        for fut in as_completed(futures):
            done_count += 1
            try:
                fut.result()
            except Exception as e:
                qa = futures[fut]
                print(
                    f"  [ERROR] {qa['sample_id']} Q{qa['question_index']}: {e}",
                    file=sys.stderr,
                )
            if done_count % 10 == 0:
                print(f"[INFO] Progress: {done_count}/{len(remaining)}", file=sys.stderr)

    print(f"\n[INFO] Done. Results: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
