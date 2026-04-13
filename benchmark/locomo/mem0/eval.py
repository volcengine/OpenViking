"""
Evaluate LoCoMo QA via mem0 + OpenClaw (agent mode).

Questions are sent to an OpenClaw agent which calls mem0 internally.
Before each request, ~/.openclaw/openclaw.json is updated so that the
openclaw-mem0 plugin uses userId = sample_id, giving each conversation
sample its own isolated memory namespace.

Prerequisites:
  - Conversations already ingested into mem0 via ingest.py (user_id = sample_id)
  - OpenClaw running locally with the openclaw-mem0 plugin installed

Usage:
    # Run QA + auto-judge
    python eval.py --openclaw-url http://127.0.0.1:18789 --openclaw-token xxx \\
                   --judge --judge-token xxx

    # Single sample
    python eval.py --sample conv-26 --openclaw-token xxx

    # Only judge an existing result CSV (skip QA)
    python eval.py --judge-only --output result/qa_results.csv --judge-token xxx
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / ".openviking_benchmark_env")

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "data" / "locomo10.json")
DEFAULT_OUTPUT_PATH = str(SCRIPT_DIR / "result" / "qa_results.csv")
DEFAULT_OPENCLAW_URL = "http://127.0.0.1:18789"
DEFAULT_SESSION_KEY = "locomo-eval"
OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"

# Serialize openclaw config updates across threads so each request sees the right userId
_openclaw_config_lock = threading.Lock()

# ---------------------------------------------------------------------------
# openclaw.json config helpers
# ---------------------------------------------------------------------------

def _update_openclaw_mem0_user(sample_id: str) -> None:
    """
    Rewrite ~/.openclaw/openclaw.json so that openclaw-mem0 uses sample_id as userId.
    Also ensures the plugin is enabled.
    Must be called while holding _openclaw_config_lock.
    """
    with open(OPENCLAW_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    entries = config.setdefault("plugins", {}).setdefault("entries", {})
    mem0_entry = entries.setdefault("openclaw-mem0", {})
    mem0_entry["enabled"] = True
    mem0_entry.setdefault("config", {})["userId"] = sample_id

    tmp = str(OPENCLAW_CONFIG_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.replace(tmp, str(OPENCLAW_CONFIG_PATH))


def _restart_openclaw_gateway(base_url: str, sample_id: str, startup_timeout: int = 30) -> None:
    """
    Kill the running openclaw gateway process and restart it.
    Waits until the gateway is ready to accept requests.
    Must be called while holding _openclaw_config_lock.
    """
    # Kill existing gateway
    try:
        subprocess.run(["pkill", "-f", "openclaw gateway"], capture_output=True)
    except Exception as e:
        print(f"    [gateway] pkill failed: {e}", file=sys.stderr)

    # Start new gateway in background
    try:
        subprocess.Popen(
            ["openclaw", "gateway"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to start openclaw gateway: {e}")

    # Wait for process to fully start before checking health
    time.sleep(5)

    # Wait until gateway is ready
    health_url = f"{base_url.rstrip('/')}/health"
    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        try:
            resp = requests.get(health_url, timeout=2)
            if resp.status_code < 500:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError(f"openclaw gateway did not become ready within {startup_timeout}s")

    # Verify the correct userId is active by sending a dummy request and checking session log
    _verify_openclaw_user(base_url, sample_id, max_retries=3)


def _verify_openclaw_user(base_url: str, expected_user: str, max_retries: int = 3) -> None:
    """
    Send a dummy request and check the session jsonl to confirm
    openclaw-mem0 is searching with the correct userId.
    Retries up to max_retries times with 3s interval.
    """
    verify_session_key = f"locomo-verify-{expected_user}-{int(time.time())}"
    url = f"{base_url.rstrip('/')}/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "X-OpenClaw-Session-Key": verify_session_key,
    }
    payload = {
        "model": "openclaw",
        "input": "What did we talk about recently?",
        "stream": False,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=120)
            resp.raise_for_status()
        except Exception as e:
            print(f"    [verify] request failed: {e}", file=sys.stderr)
            time.sleep(3)
            continue

        # Wait for session jsonl to be written
        time.sleep(1)
        session_id = get_openclaw_session_id(verify_session_key)
        if not session_id:
            time.sleep(3)
            continue

        # Check session log for the userId in the memories context
        sessions_dir = os.path.expanduser("~/.openclaw/agents/main/sessions")
        jsonl_path = os.path.join(sessions_dir, f"{session_id}.jsonl")
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                content = f.read()
            if f'user "{expected_user}"' in content or f'user \\"{expected_user}\\"' in content:
                print(f"    [verify] userId confirmed: {expected_user}", file=sys.stderr)
                return
            else:
                print(f"    [verify] userId mismatch, retrying in 3s...", file=sys.stderr)
        except Exception:
            pass
        time.sleep(3)

    raise RuntimeError(f"openclaw userId did not switch to {expected_user} after {max_retries} retries")


CATEGORY_NAMES = {
    1: "single-hop",
    2: "multi-hop",
    3: "temporal",
    4: "world-knowledge",
    5: "adversarial",
}

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


def get_sample_last_session_date(sample: dict) -> Optional[str]:
    """Return the date of the last session as YYYY-MM-DD, or None."""
    conv = sample.get("conversation", {})
    session_keys = [k for k in conv if k.startswith("session_") and "date_time" not in k]
    if not session_keys:
        return None

    def sess_num(k: str) -> int:
        try:
            return int(k.split("_")[1])
        except ValueError:
            return 0

    for sk in sorted(session_keys, key=sess_num, reverse=True):
        if conv.get(sk):
            dt_key = f"{sk}_date_time"
            date_str = conv.get(dt_key, "")
            if date_str and " on " in date_str:
                try:
                    from datetime import datetime
                    date_part = date_str.split(" on ")[-1]
                    dt = datetime.strptime(date_part.strip(), "%d %B, %Y")
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
    return None


def load_qa_items(
    data: list[dict],
    skip_adversarial: bool = True,
    question_index: Optional[int] = None,
    count: Optional[int] = None,
) -> list[dict]:
    items = []
    for sample in data:
        sample_id = sample.get("sample_id", "")
        question_time = get_sample_last_session_date(sample)

        for q_idx, qa in enumerate(sample.get("qa", [])):
            if question_index is not None and q_idx != question_index:
                continue
            category = qa.get("category", 0)
            if skip_adversarial and str(category) == "5":
                continue
            items.append(
                {
                    "sample_id": sample_id,
                    "question_index": q_idx,
                    "question_id": f"{sample_id}_qa{q_idx}",
                    "question": qa["question"],
                    "answer": str(qa["answer"]),
                    "category": category,
                    "category_name": CATEGORY_NAMES.get(category, "unknown"),
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

QA_FIELDNAMES = [
    "sample_id",
    "question_index",
    "question_id",
    "question",
    "answer",
    "category",
    "category_name",
    "question_time",
    "evidence",
    "response",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "time_cost",
    "result",
    "reasoning",
    "timestamp",
]


def load_processed_ids(output_path: str) -> set[str]:
    processed: set[str] = set()
    if not os.path.exists(output_path):
        return processed
    try:
        with open(output_path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("response"):
                    processed.add(row.get("question_id", ""))
    except Exception as e:
        print(f"[WARN] Error reading {output_path}: {e}", file=sys.stderr)
    return processed


def save_row(output_path: str, row: dict, write_lock: threading.Lock) -> None:
    with write_lock:
        file_exists = os.path.exists(output_path)
        with open(output_path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=QA_FIELDNAMES, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
            f.flush()


# ---------------------------------------------------------------------------
# OpenClaw agent
# ---------------------------------------------------------------------------

def extract_openclaw_text(body: dict) -> str:
    """Extract assistant text from /v1/responses API response."""
    try:
        for item in body.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        return content.get("text", "")
        for item in body.get("output", []):
            if "text" in item:
                return item["text"]
            for content in item.get("content", []):
                if "text" in content:
                    return content["text"]
    except Exception:
        pass
    return f"[ERROR: could not parse response: {body}]"


def get_openclaw_session_id(session_key: str) -> Optional[str]:
    # main agent sessions
    sessions_file = os.path.expanduser("~/.openclaw/agents/main/sessions/sessions.json")
    try:
        with open(sessions_file, "r") as f:
            data = json.load(f)
        return data.get(session_key, {}).get("sessionId")
    except Exception:
        return None



def parse_session_tokens(session_id: str, agent_id: str) -> dict:
    """Sum up all LLM usage across all assistant messages in the session jsonl."""
    sessions_dir = os.path.expanduser(f"~/.openclaw/agents/{agent_id}/sessions")
    src = os.path.join(sessions_dir, f"{session_id}.jsonl")
    total_input = total_output = total_cache_read = 0
    try:
        with open(src, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") == "message" and obj.get("message", {}).get("role") == "assistant":
                    usage = obj["message"].get("usage", {})
                    total_input += usage.get("input", 0)
                    total_output += usage.get("output", 0)
                    total_cache_read += usage.get("cacheRead", 0)
    except Exception:
        pass
    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output + total_cache_read,
    }


def send_to_openclaw(
    question: str,
    sample_id: str,
    base_url: str,
    token: str,
    question_time: Optional[str] = None,
    question_id: Optional[str] = None,
    retries: int = 2,
) -> tuple[str, dict, float]:
    """
    Send a question to an OpenClaw agent.

    Before each request we update ~/.openclaw/openclaw.json to set the
    openclaw-mem0 userId = sample_id, providing per-sample memory isolation.
    A global lock serializes these config writes so concurrent threads don't
    clobber each other's userId.

    Returns (response_text, usage, time_cost).
    """
    # Send only the question as input so mem0 semantic search isn't polluted by the date prefix.
    input_text = question

    # Use a unique session key per question to avoid cross-thread session collision.
    session_key = f"{DEFAULT_SESSION_KEY}-{question_id}" if question_id else DEFAULT_SESSION_KEY

    url = f"{base_url.rstrip('/')}/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-OpenClaw-Session-Key": session_key,
    }
    payload = {
        "model": "openclaw",
        "input": input_text,
        "stream": False,
        "user": sample_id,
    }

    last_exc: Optional[Exception] = None
    t0 = time.time()
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=300)
            resp.raise_for_status()
            body = resp.json()
            response_text = extract_openclaw_text(body)

            # Wait for openclaw to flush the session jsonl before parsing tokens
            time.sleep(1)
            session_id = get_openclaw_session_id(session_key)
            if session_id:
                usage = parse_session_tokens(session_id, "main")
            else:
                usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

            return response_text, usage, time.time() - t0
        except Exception as e:
            last_exc = e
            if attempt < retries:
                print(f"    [retry {attempt + 1}/{retries}] {e}", file=sys.stderr)

    raise RuntimeError(f"OpenClaw request failed after {retries + 1} attempts: {last_exc}")


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = "You are an expert grader that determines if answers to questions match a gold standard answer"

JUDGE_ACCURACY_PROMPT = """Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {response}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Respond with JSON only: {{"reasoning": "your explanation", "is_correct": "CORRECT" or "WRONG"}}"""



def judge_answer(
    question: str,
    gold_answer: str,
    response: str,
    judge_base_url: str,
    judge_token: str,
    judge_model: str,
) -> tuple[str, str]:
    from openai import OpenAI
    client = OpenAI(base_url=judge_base_url, api_key=judge_token)
    prompt = JUDGE_ACCURACY_PROMPT.format(
        question=question, gold_answer=gold_answer, response=response
    )
    try:
        resp = client.chat.completions.create(
            model=judge_model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            timeout=60,
        )
        content = resp.choices[0].message.content.strip()
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(content[start : end + 1])
            label = "CORRECT" if parsed.get("is_correct", "WRONG").strip().upper() == "CORRECT" else "WRONG"
            return label, parsed.get("reasoning", "")
        return "WRONG", f"[PARSE ERROR] {content}"
    except Exception as e:
        return "WRONG", f"[API ERROR] {e}"


# ---------------------------------------------------------------------------
# Accuracy summary
# ---------------------------------------------------------------------------

def print_accuracy(rows: list[dict]) -> None:
    graded = [r for r in rows if r.get("result") in ("CORRECT", "WRONG")]
    if not graded:
        print("\n[INFO] No graded results to summarize.", file=sys.stderr)
        return

    correct_total = sum(1 for r in graded if r["result"] == "CORRECT")
    print("\n=== Accuracy Summary ===", file=sys.stderr)
    print(f"  Overall: {correct_total}/{len(graded)} = {correct_total/len(graded):.2%}", file=sys.stderr)

    by_cat: dict[str, list[str]] = {}
    for r in graded:
        cat = r.get("category_name") or str(r.get("category", "?"))
        by_cat.setdefault(cat, []).append(r["result"])

    print("  By category:", file=sys.stderr)
    for cat, results in sorted(by_cat.items()):
        n = sum(1 for r in results if r == "CORRECT")
        print(f"    {cat:20s}: {n}/{len(results)} = {n/len(results):.2%}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main runners
# ---------------------------------------------------------------------------

def run_qa(args: argparse.Namespace) -> None:
    openclaw_token = args.openclaw_token or os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")

    judge_token = args.judge_token or os.environ.get("ARK_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    if args.judge and not judge_token:
        print(
            "Error: judge token required (--judge-token or OPENAI_API_KEY env var)",
            file=sys.stderr,
        )
        sys.exit(1)

    data = load_locomo_data(args.input, args.sample)
    qa_items = load_qa_items(
        data,
        skip_adversarial=args.skip_adversarial,
        question_index=args.question_index,
        count=args.count,
    )
    print(f"[INFO] {len(qa_items)} QA items loaded", file=sys.stderr)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    # Remove ERROR rows from CSV before loading processed ids
    if os.path.exists(args.output):
        with open(args.output, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or QA_FIELDNAMES
            clean_rows = [r for r in reader if not r.get("response", "").startswith("[ERROR]")]
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(clean_rows)

    processed_ids = load_processed_ids(args.output)
    remaining = [qa for qa in qa_items if qa["question_id"] not in processed_ids]
    print(
        f"[INFO] {len(processed_ids)} already done, {len(remaining)} remaining",
        file=sys.stderr,
    )

    if not remaining:
        print("[INFO] All questions already processed.", file=sys.stderr)
    else:
        write_lock = threading.Lock()
        total = len(remaining)

        # Group remaining questions by sample_id to minimize gateway restarts
        from collections import defaultdict
        by_sample: dict[str, list[tuple[int, dict]]] = defaultdict(list)
        for i, qa in enumerate(remaining):
            by_sample[qa["sample_id"]].append((i + 1, qa))

        def run_one(qa: dict, idx: int) -> None:
            print(
                f"  [{idx}/{total}] {qa['question_id']}: {qa['question'][:60]}...",
                file=sys.stderr,
            )
            try:
                response, usage, time_cost = send_to_openclaw(
                    qa["question"],
                    qa["sample_id"],
                    args.openclaw_url,
                    openclaw_token,
                    qa.get("question_time"),
                    qa["question_id"],
                )
            except Exception as e:
                response = f"[ERROR] {e}"
                usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                time_cost = 0.0

            result_label, reasoning = "", ""
            if args.judge and response and not response.startswith("[ERROR]"):
                result_label, reasoning = judge_answer(
                    qa["question"],
                    qa["answer"],
                    response,
                    args.judge_base_url or args.openclaw_url,
                    judge_token,
                    args.judge_model,
                )

            row = {
                "sample_id": qa["sample_id"],
                "question_index": qa["question_index"],
                "question_id": qa["question_id"],
                "question": qa["question"],
                "answer": qa["answer"],
                "category": qa["category"],
                "category_name": qa["category_name"],
                "question_time": qa.get("question_time", ""),
                "evidence": json.dumps(qa.get("evidence", [])),
                "response": response,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "time_cost": round(time_cost, 2),
                "result": result_label,
                "reasoning": reasoning,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_row(args.output, row, write_lock)
            label_str = f"  → {result_label}" if result_label else ""
            print(f"  [{idx}/{total}] done {time_cost:.1f}s{label_str}", file=sys.stderr)

        # Process sample by sample: restart gateway once per sample to pick up new userId
        for sample_id, qa_list in by_sample.items():
            print(f"\n[INFO] Switching to sample {sample_id}, restarting openclaw gateway...", file=sys.stderr)
            with _openclaw_config_lock:
                _update_openclaw_mem0_user(sample_id)
                _restart_openclaw_gateway(args.openclaw_url, sample_id)
            print(f"[INFO] Gateway ready, running {len(qa_list)} questions for {sample_id}", file=sys.stderr)

            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                futures = {
                    executor.submit(run_one, qa, idx): qa
                    for idx, qa in qa_list
                }
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as e:
                        qa = futures[fut]
                        print(f"  [ERROR] {qa['question_id']}: {e}", file=sys.stderr)

    # Print token and latency summary
    try:
        with open(args.output, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        total_input = sum(int(r.get("input_tokens") or 0) for r in rows)
        total_input_with_cache = sum(
            int(r.get("total_tokens") or 0) - int(r.get("output_tokens") or 0) for r in rows
        )
        times = [float(r["time_cost"]) for r in rows if r.get("time_cost")]
        avg_time = sum(times) / len(times) if times else 0.0
        print(f"\n=== Token & Latency Summary ===", file=sys.stderr)
        print(f"  Total input tokens             : {total_input}", file=sys.stderr)
        print(f"  Total input tokens (with cache): {total_input_with_cache}", file=sys.stderr)
        print(f"  Avg time per query             : {avg_time:.1f}s", file=sys.stderr)
    except Exception:
        pass

    if args.judge:
        try:
            with open(args.output, "r", encoding="utf-8", newline="") as f:
                print_accuracy(list(csv.DictReader(f)))
        except Exception:
            pass


def run_judge_only(args: argparse.Namespace) -> None:
    """Grade responses in an existing CSV that lack a result label."""
    if not os.path.exists(args.output):
        print(f"Error: output file not found: {args.output}", file=sys.stderr)
        sys.exit(1)

    judge_token = args.judge_token or os.environ.get("ARK_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    if not judge_token:
        print(
            "Error: judge token required (--judge-token or ARK_API_KEY env var)",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(args.output, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or QA_FIELDNAMES)
        rows = list(reader)

    for extra in ("result", "reasoning"):
        if extra not in fieldnames:
            fieldnames.append(extra)

    ungraded_indices = [i for i, row in enumerate(rows) if not row.get("result")]
    print(f"[INFO] {len(rows)} rows total, {len(ungraded_indices)} ungraded", file=sys.stderr)

    if not ungraded_indices:
        print("[INFO] All rows already graded.", file=sys.stderr)
        print_accuracy(rows)
        return

    judge_base_url = args.judge_base_url or "https://ark.cn-beijing.volces.com/api/v3"
    file_lock = threading.Lock()

    def grade_one(idx: int) -> None:
        row = rows[idx]
        label, reasoning = judge_answer(
            row.get("question", ""),
            row.get("answer", ""),
            row.get("response", ""),
            judge_base_url,
            judge_token,
            args.judge_model,
        )
        row["result"] = label
        row["reasoning"] = reasoning
        with file_lock:
            tmp = args.output + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp, args.output)
        print(f"  Graded {row.get('question_id','?')}: {label}", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = [executor.submit(grade_one, idx) for idx in ungraded_indices]
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                print(f"[ERROR] grading failed: {e}", file=sys.stderr)

    print_accuracy(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate LoCoMo QA via OpenClaw agent (mem0-backed)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Data selection
    parser.add_argument("--input", default=DEFAULT_DATA_PATH, help="Path to locomo10.json")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help="Path to output CSV")
    parser.add_argument(
        "--sample",
        default=None,
        help="Sample index (int) or sample_id (e.g. conv-26). Default: all.",
    )
    parser.add_argument(
        "--question-index",
        type=int,
        default=None,
        help="Single question index (0-based) within the sample.",
    )
    parser.add_argument("--count", type=int, default=None, help="Max QA items to process.")
    parser.add_argument(
        "--no-skip-adversarial",
        dest="skip_adversarial",
        action="store_false",
        default=True,
        help="Include category-5 adversarial questions (skipped by default).",
    )
    parser.add_argument("--threads", type=int, default=10, help="Concurrent threads (default: 10)")

    # OpenClaw
    parser.add_argument(
        "--openclaw-url",
        default=DEFAULT_OPENCLAW_URL,
        help=f"OpenClaw gateway URL (default: {DEFAULT_OPENCLAW_URL})",
    )
    parser.add_argument(
        "--openclaw-token",
        default=None,
        help="OpenClaw auth token (or OPENCLAW_GATEWAY_TOKEN env var)",
    )
    # Judge
    parser.add_argument(
        "--judge",
        action="store_true",
        default=False,
        help="Auto-judge each response right after answering.",
    )
    parser.add_argument(
        "--judge-only",
        action="store_true",
        default=False,
        help="Skip QA; only grade ungraded responses in the existing --output CSV.",
    )
    parser.add_argument(
        "--judge-base-url",
        default="https://ark.cn-beijing.volces.com/api/v3",
        help="OpenAI-compatible API base URL for judge (default: Volcengine ARK)",
    )
    parser.add_argument(
        "--judge-token",
        default=None,
        help="API token for judge (or ARK_API_KEY / OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--judge-model",
        default="doubao-seed-2-0-pro-260215",
        help="Judge model (default: doubao-seed-2-0-pro-260215)",
    )

    args = parser.parse_args()

    if args.judge_only:
        run_judge_only(args)
    else:
        run_qa(args)


if __name__ == "__main__":
    main()
