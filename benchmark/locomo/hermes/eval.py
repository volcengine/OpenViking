"""
Shared Hermes LoCoMo QA evaluator.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock

import httpx
import requests

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(*_args, **_kwargs):
        return False


env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)

DEFAULT_BASE_URL = os.getenv("HERMES_GATEWAY_BASE_URL", "http://127.0.0.1:8642")
DEFAULT_MODEL = os.getenv("HERMES_GATEWAY_MODEL", "hermes-agent")
DEFAULT_TOKEN = os.getenv("HERMES_GATEWAY_TOKEN") or os.getenv("API_SERVER_KEY") or ""
DEFAULT_OPENVIKING_URL = os.getenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
DEFAULT_DATASET_LOCATION = "benchmark/locomo/data/locomo10.json"
MAX_HTTP_ATTEMPTS = 2
DEFAULT_ERROR_RETRIES = int(os.getenv("QA_ERROR_RETRIES", "2"))
QA_INSTRUCTIONS = "Answer to the best of your ability and reasonable inference."

csv_lock = Lock()


def default_locomo_input(script_dir: Path) -> str:
    return os.getenv("LOCOMO_JSON") or str(script_dir.parent / "data" / "locomo10.json")


def dataset_missing_message(path: str) -> str:
    return (
        f"LoCoMo dataset not found: {path}\n"
        f"Set LOCOMO_JSON=/path/to/locomo10.json, pass a script input path, "
        f"or place it at {DEFAULT_DATASET_LOCATION}."
    )


def openviking_headers() -> dict[str, str]:
    headers = {
        "X-OpenViking-Account": os.getenv("OPENVIKING_ACCOUNT", "default"),
        "X-OpenViking-User": os.getenv("OPENVIKING_USER", "default"),
    }
    api_key = os.getenv("OPENVIKING_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def require_suite(value: str) -> str:
    if value not in {"baseline", "e2e", "preingest"}:
        raise argparse.ArgumentTypeError("suite must be one of: baseline, e2e, preingest")
    return value


def result_dir_name(suite: str) -> str:
    if suite == "baseline":
        return "result_baseline"
    if suite == "e2e":
        return "result_e2e"
    return "result_preingest"


def conversation_prefix(suite: str) -> str:
    if suite == "baseline":
        return "locomo-native-qa-"
    if suite == "e2e":
        return "locomo-e2e-qa-"
    return "locomo-ovpreingest-qa-"


def load_locomo_data(path: str, sample_index: int | None = None) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: {dataset_missing_message(path)}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(1)

    if sample_index is None:
        return data
    if sample_index < 0 or sample_index >= len(data):
        print(
            f"Error: sample index {sample_index} out of range (0-{len(data) - 1})", file=sys.stderr
        )
        sys.exit(1)
    return [data[sample_index]]


def parse_locomo_datetime(date_str: str) -> datetime | None:
    try:
        if " on " in date_str:
            return datetime.strptime(date_str.split(" on ")[-1].strip(), "%d %B, %Y")
    except ValueError:
        return None
    return None


def get_sample_question_time(sample: dict) -> str | None:
    conversation = sample.get("conversation", {})
    session_keys = [
        key for key in conversation.keys() if key.startswith("session_") and "date_time" not in key
    ]
    if not session_keys:
        return None

    def session_num(key: str) -> int:
        try:
            return int(key.replace("session_", ""))
        except ValueError:
            return 0

    session_keys.sort(key=session_num, reverse=True)
    for key in session_keys:
        if not conversation.get(key):
            continue
        date_str = conversation.get(f"session_{session_num(key)}_date_time")
        if not date_str:
            continue
        parsed = parse_locomo_datetime(date_str)
        if parsed:
            return parsed.strftime("%Y-%m-%d")
    return None


def normalize_usage(raw: dict | None) -> dict:
    raw = raw or {}
    input_tokens = raw.get("input_tokens") or raw.get("prompt_tokens", 0)
    output_tokens = raw.get("output_tokens") or raw.get("completion_tokens", 0)
    cache_read_tokens = raw.get("cache_read_tokens") or raw.get("cacheRead", 0)
    cache_write_tokens = raw.get("cache_write_tokens") or raw.get("cacheWrite", 0)
    total_tokens = raw.get("total_tokens") or raw.get("totalTokens")
    if total_tokens is None:
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)

    return {
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cache_read_tokens": int(cache_read_tokens or 0),
        "cache_write_tokens": int(cache_write_tokens or 0),
        "total_tokens": int(total_tokens or 0),
    }


def extract_response_text(body: dict) -> str:
    for item in body.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
            if "text" in content:
                return content["text"]
    return ""


def extract_tool_names(body: dict) -> list[str]:
    return [item["requested_name"] for item in extract_tool_trace(body)]


def extract_tool_trace(body: dict) -> list[dict]:
    trace = []
    for item in body.get("output", []):
        if item.get("type") != "function_call":
            continue
        requested_name = item.get("requested_name") or item.get("name")
        if not isinstance(requested_name, str) or not requested_name:
            continue
        executed_name = item.get("executed_name")
        if not isinstance(executed_name, str):
            executed_name = ""
        if not executed_name and not item.get("was_blocked"):
            executed_name = requested_name
        trace.append(
            {
                "requested_name": requested_name,
                "executed_name": executed_name,
                "was_rewritten": bool(item.get("was_rewritten")),
                "was_blocked": bool(item.get("was_blocked")),
            }
        )
    return trace


def send_gateway_request(
    base_url: str,
    token: str,
    model: str,
    conversation: str,
    input_text: str,
    instructions: str = QA_INSTRUCTIONS,
    timeout: int = 600,
) -> tuple[dict, float, str]:
    url = f"{base_url.rstrip('/')}/v1/responses"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": input_text,
        "conversation": conversation,
        "session_id": conversation,
        "store": False,
    }
    if instructions:
        payload["instructions"] = instructions

    last_error: requests.RequestException | None = None
    total_elapsed = 0.0
    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        started_at = time.perf_counter()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            elapsed = time.perf_counter() - started_at
            total_elapsed += elapsed
            resp.raise_for_status()
            return resp.json(), total_elapsed, resp.headers.get("X-Hermes-Session-Id", "")
        except requests.HTTPError as exc:
            elapsed = time.perf_counter() - started_at
            total_elapsed += elapsed
            if attempt >= MAX_HTTP_ATTEMPTS:
                raise
            last_error = exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            elapsed = time.perf_counter() - started_at
            total_elapsed += elapsed
            if attempt >= MAX_HTTP_ATTEMPTS:
                raise
            last_error = exc
        time.sleep(float(2 ** (attempt - 1)))
    if last_error is not None:
        raise last_error
    raise RuntimeError("unreachable")


def build_question_prompt(
    suite: str,
    question: str,
    question_time: str | None,
    sample_id: str,
) -> str:
    if question_time:
        return f"Current date: {question_time}. {question}"
    return question


def process_single_question(
    item: dict,
    sample_idx: int,
    question_index: int,
    qa: dict,
    args: argparse.Namespace,
    csv_path: str,
) -> dict:
    sample_id = item["sample_id"]
    question = qa["question"]
    expected = str(qa["answer"])
    category = str(qa.get("category", ""))
    evidence = qa.get("evidence", [])
    question_time = get_sample_question_time(item)
    conversation = f"{args.conversation_prefix}{sample_id}-q{question_index}"

    if args.suite == "baseline":
        mode = "native DB, session_search"
    elif args.suite == "e2e":
        mode = "mixed memory E2E"
    else:
        mode = "ov-preingest, no replay"
    print(f"[{sample_id}] Q{question_index}: asking ({mode})", file=sys.stderr)

    try:
        body, qa_latency, hermes_session_id = send_gateway_request(
            base_url=args.base_url,
            token=args.token,
            model=args.model,
            conversation=conversation,
            input_text=build_question_prompt(
                args.suite,
                question,
                question_time,
                sample_id,
            ),
            timeout=args.timeout,
        )
        qa_usage = normalize_usage(body.get("usage"))
        response = extract_response_text(body)
        tool_trace = extract_tool_trace(body)
        tool_names = [item["requested_name"] for item in tool_trace]
    except requests.HTTPError as e:
        response = f"[ERROR] HTTP {e.response.status_code}: {e.response.text}"
        qa_usage = normalize_usage({})
        qa_latency = 0.0
        tool_trace = []
        tool_names = []
        hermes_session_id = ""
    except Exception as e:
        response = f"[ERROR] {e}"
        qa_usage = normalize_usage({})
        qa_latency = 0.0
        tool_trace = []
        tool_names = []
        hermes_session_id = ""

    record = {
        "sample_id": sample_id,
        "sample_idx": sample_idx,
        "qi": question_index,
        "question": question,
        "expected": expected,
        "response": response,
        "category": category,
        "evidence": json.dumps(evidence, ensure_ascii=False),
        "question_time": question_time or "",
        "conversation": conversation,
        "hermes_session_id": hermes_session_id,
        "qa_input_tokens": qa_usage["input_tokens"],
        "qa_output_tokens": qa_usage["output_tokens"],
        "qa_cache_read_tokens": qa_usage["cache_read_tokens"],
        "qa_cache_write_tokens": qa_usage["cache_write_tokens"],
        "qa_total_tokens": qa_usage["total_tokens"],
        "qa_latency_sec": f"{qa_latency:.4f}",
        "tool_call_count": len(tool_names),
        "tool_names": json.dumps(tool_names, ensure_ascii=False),
        "executed_tool_call_count": len([item for item in tool_trace if item["executed_name"]]),
        "executed_tool_names": json.dumps(
            [item["executed_name"] for item in tool_trace if item["executed_name"]],
            ensure_ascii=False,
        ),
        "rewritten_tool_call_count": sum(1 for item in tool_trace if item["was_rewritten"]),
        "blocked_tool_call_count": sum(1 for item in tool_trace if item["was_blocked"]),
        "result": "",
        "reasoning": "",
    }

    with csv_lock:
        save_record_to_csv(csv_path, record)

    print(f"[{sample_id}] Q{question_index}: done ({response[:60]}...)", file=sys.stderr)
    return record


def is_error_record(row: dict) -> bool:
    return str(row.get("response", "")).lstrip().startswith("[ERROR]")


def count_error_records(csv_path: str) -> int:
    if not os.path.exists(csv_path):
        return 0

    count = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if is_error_record(row):
                count += 1
    return count


def prune_error_records(csv_path: str) -> int:
    if not os.path.exists(csv_path):
        return 0

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    kept = [row for row in rows if not is_error_record(row)]
    removed = len(rows) - len(kept)
    if removed <= 0:
        return 0

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
        f.flush()
    return removed


def load_executed_records(csv_path: str) -> set[tuple[str, int]]:
    executed = set()
    if not os.path.exists(csv_path):
        return executed

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if is_error_record(row):
                continue
            try:
                executed.add((row["sample_id"], int(row["qi"])))
            except Exception:
                continue
    return executed


def save_record_to_csv(csv_path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    fieldnames = [
        "sample_id",
        "sample_idx",
        "qi",
        "question",
        "expected",
        "response",
        "category",
        "evidence",
        "question_time",
        "conversation",
        "hermes_session_id",
        "qa_input_tokens",
        "qa_output_tokens",
        "qa_cache_read_tokens",
        "qa_cache_write_tokens",
        "qa_total_tokens",
        "qa_latency_sec",
        "tool_call_count",
        "tool_names",
        "executed_tool_call_count",
        "executed_tool_names",
        "rewritten_tool_call_count",
        "blocked_tool_call_count",
        "result",
        "reasoning",
    ]
    expected_header = ",".join(fieldnames)
    should_write_header = not os.path.exists(csv_path)

    if not should_write_header:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            current_header = f.readline().strip()
        if current_header != expected_header:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                existing_rows = list(csv.DictReader(f))
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in existing_rows:
                    writer.writerow({key: row.get(key, "") for key in fieldnames})

    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if should_write_header:
            writer.writeheader()
        writer.writerow(record)
        f.flush()


def reset_generated_file(path: str | Path, label: str) -> None:
    target = Path(path)
    if target.exists():
        target.unlink()
        print(f"Removed existing {label}: {target}", file=sys.stderr)


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


def _append_true_token_record(output_file: str, record: dict) -> None:
    fieldnames = [
        "timestamp",
        "embedding_input_tokens",
        "embedding_output_tokens",
        "vlm_llm_input_tokens",
        "vlm_llm_output_tokens",
    ]
    expected_header = ",".join(fieldnames)
    mode = "a"
    should_write_header = not os.path.exists(output_file)

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


async def wait_for_queues_and_record_totals(
    openviking_url: str,
    output_file: str,
    baseline_processed: int = 0,
    baseline_model_totals: tuple[int, int, int, int] | None = None,
    max_wait_sec: int = 1800,
    settle_checks: int = 3,
    poll_interval: float = 2.0,
) -> None:
    print(
        f"\n[INFO] Waiting for OpenViking background queues to drain "
        f"(baseline_processed={baseline_processed}, settle={settle_checks})...",
        file=sys.stderr,
    )

    async with httpx.AsyncClient(headers=openviking_headers()) as client:
        started = time.perf_counter()
        idle_streak = 0
        last_totals: tuple[int, int, int] | None = None

        while True:
            elapsed = time.perf_counter() - started
            if elapsed > max_wait_sec:
                print(
                    f"[WARN] Queue drain timeout after {elapsed:.0f}s (last totals={last_totals})",
                    file=sys.stderr,
                )
                break

            totals = await _read_queue_totals(client, openviking_url)
            if totals is None:
                await asyncio.sleep(poll_interval)
                continue

            pending, in_progress, processed = totals
            last_totals = totals
            queues_idle = pending == 0 and in_progress == 0

            if queues_idle:
                idle_streak += 1
                if idle_streak >= settle_checks:
                    print(
                        f"[INFO] Background queues drained. Processed {processed - baseline_processed} item(s) since baseline.",
                        file=sys.stderr,
                    )
                    break
            else:
                idle_streak = 0
                print(
                    f"  .. queue state pending={pending} in_progress={in_progress} processed={processed}",
                    file=sys.stderr,
                )

            await asyncio.sleep(poll_interval)

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

            _append_true_token_record(
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


def run_eval_pass(
    samples: list[dict], args: argparse.Namespace, executed_records: set[tuple[str, int]]
) -> None:
    for sample_idx, item in enumerate(samples, start=1):
        sample_id = item["sample_id"]
        qas = [qa for qa in item.get("qa", []) if str(qa.get("category", "")) != "5"]
        if args.count is not None:
            qas = qas[: args.count]

        tasks = []
        for question_index, qa in enumerate(qas, start=1):
            if (sample_id, question_index) in executed_records:
                print(f"[{sample_id}] skip Q{question_index}: already recorded", file=sys.stderr)
                continue
            tasks.append((question_index, qa))

        if not tasks:
            continue

        print(
            f"[{sample_id}] running {len(tasks)} question(s) with {args.parallel} workers",
            file=sys.stderr,
        )
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as executor:
            futures = [
                executor.submit(
                    process_single_question, item, sample_idx, question_index, qa, args, args.output
                )
                for question_index, qa in tasks
            ]
            for future in as_completed(futures):
                future.result()


def run_eval(args: argparse.Namespace) -> None:
    if not args.token:
        print("Error: HERMES_GATEWAY_TOKEN or --token is required", file=sys.stderr)
        sys.exit(1)

    samples = load_locomo_data(args.input, args.sample)

    if args.force:
        reset_generated_file(args.output, "QA CSV")
        reset_generated_file(
            Path(args.output).parent / "eval_true_tokens.csv", "eval true-token CSV"
        )
    else:
        removed = prune_error_records(args.output)
        if removed:
            print(f"Pruned {removed} previous error row(s) for retry", file=sys.stderr)

    baseline_processed = 0
    baseline_model_totals = None
    if args.suite in {"e2e", "preingest"}:

        async def snapshot_processed() -> int:
            async with httpx.AsyncClient(headers=openviking_headers()) as client:
                totals = await _read_queue_totals(client, args.openviking_url)
            return totals[2] if totals else 0

        async def snapshot_models() -> tuple[int, int, int, int] | None:
            async with httpx.AsyncClient(headers=openviking_headers()) as client:
                return await _read_model_totals(client, args.openviking_url)

        baseline_processed = asyncio.run(snapshot_processed())
        baseline_model_totals = asyncio.run(snapshot_models())
        print(f"[INFO] Observer baseline processed={baseline_processed}", file=sys.stderr)

    max_rounds = max(0, args.error_retries) + 1
    for round_index in range(max_rounds):
        executed_records = (
            set() if args.force and round_index == 0 else load_executed_records(args.output)
        )
        print(
            f"Loaded {len(executed_records)} executed records from {args.output}", file=sys.stderr
        )
        if round_index:
            print(f"Retry round {round_index}/{max_rounds - 1}", file=sys.stderr)

        run_eval_pass(samples, args, executed_records)

        error_count = count_error_records(args.output)
        if error_count == 0 or round_index >= max_rounds - 1:
            break

        removed = prune_error_records(args.output)
        print(f"Retrying {removed} error row(s)", file=sys.stderr)

    print(f"Done. Results written to {args.output}", file=sys.stderr)

    if args.suite in {"e2e", "preingest"}:
        true_token_file = Path(args.output).parent / "eval_true_tokens.csv"
        asyncio.run(
            wait_for_queues_and_record_totals(
                args.openviking_url,
                str(true_token_file),
                baseline_processed=baseline_processed,
                baseline_model_totals=baseline_model_totals,
            )
        )


def main() -> None:
    script_dir = Path(__file__).parent.resolve()
    parser = argparse.ArgumentParser(description="Shared Hermes LoCoMo QA evaluator")
    parser.add_argument(
        "input", nargs="?", default=default_locomo_input(script_dir), help="Path to LoCoMo JSON"
    )
    parser.add_argument("--suite", type=require_suite, required=True, help="Benchmark suite")
    parser.add_argument("--output", default=None, help="Path to output CSV")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Hermes gateway base URL")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Hermes gateway token")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Hermes gateway model name")
    parser.add_argument("--sample", type=int, default=None, help="Sample index (0-based)")
    parser.add_argument("--count", type=int, default=None, help="Questions per sample")
    parser.add_argument("--parallel", type=int, default=4, help="Parallel question workers")
    parser.add_argument("--timeout", type=int, default=600, help="Gateway request timeout seconds")
    parser.add_argument(
        "--force", action="store_true", help="Ignore existing CSV and rerun questions"
    )
    parser.add_argument(
        "--error-retries",
        type=int,
        default=DEFAULT_ERROR_RETRIES,
        help="Retry rows whose response starts with [ERROR] this many times",
    )
    parser.add_argument(
        "--openviking-url", default=DEFAULT_OPENVIKING_URL, help="OpenViking service URL"
    )
    args = parser.parse_args()

    if args.output is None:
        args.output = str(script_dir / result_dir_name(args.suite) / "qa_results.csv")
    args.conversation_prefix = conversation_prefix(args.suite)

    run_eval(args)


if __name__ == "__main__":
    main()
