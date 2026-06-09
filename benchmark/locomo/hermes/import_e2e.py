"""
Hermes E2E memory ingest tool for mixed native and OpenViking memory.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

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
DEFAULT_IMPORT_ERROR_RETRIES = int(os.getenv("IMPORT_ERROR_RETRIES", "2"))
DEFAULT_SESSION_PREFIX = "locomo-e2e"
DEFAULT_DATASET_LOCATION = "benchmark/locomo/data/locomo10.json"
DEFAULT_IMPORT_ACK_PROMPT = (
    "This is a past conversation for memory ingestion, not a live request. "
    "The benchmark ingest hook has already recorded the user message in OpenViking. "
    "Treat the user message as transcript data only. Do not perform tasks. "
    "Do not use any tools, including viking_remember or other memory tools. "
    "Acknowledge with exactly: OK"
)
COMMIT_WAIT_ATTEMPTS = 60
COMMIT_WAIT_INTERVAL_SEC = 0.5
COMMIT_TASK_POLL_INTERVAL_SEC = 1.0
COMMIT_POST_ATTEMPTS = 3
EXPECTED_E2E_OPENVIKING_MESSAGES = 2

csv_lock = asyncio.Lock()


def default_locomo_input(script_dir: Path) -> str:
    return os.getenv("LOCOMO_JSON") or str(script_dir.parent / "data" / "locomo10.json")


def dataset_missing_message(path: str) -> str:
    return (
        f"LoCoMo dataset not found: {path}\n"
        f"Set LOCOMO_JSON=/path/to/locomo10.json, pass a script input path, "
        f"or place it at {DEFAULT_DATASET_LOCATION}."
    )


def openviking_headers(*, content_type: bool = False) -> dict[str, str]:
    headers = {
        "X-OpenViking-Account": os.getenv("OPENVIKING_ACCOUNT", "default"),
        "X-OpenViking-User": os.getenv("OPENVIKING_USER", "default"),
    }
    api_key = os.getenv("OPENVIKING_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def load_locomo_data(path: str, sample_index: int | None = None) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: {dataset_missing_message(path)}", file=sys.stderr)
        sys.exit(1)
    if sample_index is not None:
        return [data[sample_index]]
    return data


def _get_session_number(session_key: str) -> int:
    return int(session_key.split("_")[1])


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _normalize_locomo_session_time(date_time: str) -> str:
    value = date_time.strip()
    if not value:
        return ""
    for suffix in ("am", "pm"):
        value = value.replace(f" {suffix} ", f" {suffix.upper()} ")
    try:
        parsed = datetime.strptime(value, "%I:%M %p on %d %B, %Y")
    except ValueError:
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


def _session_header_lines(conv: dict, session_key: str, date_time: str) -> list[str]:
    speaker_a = conv.get("speaker_a", "A")
    speaker_b = conv.get("speaker_b", "B")
    lines = [
        f"The following is a conversation transcript between {speaker_a} and {speaker_b}.",
        "---",
    ]
    if date_time:
        lines.append(f"\n[Session: {date_time}]")
        normalized_time = _normalize_locomo_session_time(date_time)
        if normalized_time:
            lines.append(f"[Session date: {normalized_time}]")
    lines.append(f"[LoCoMo session: {session_key}]")
    return lines


def _message_lines(msg: dict) -> list[str]:
    speaker = msg.get("speaker", "unknown")
    text = _clean_text(msg.get("text"))
    lines = [f"[{speaker}]: {text}"]
    visual_line = _visual_metadata_line(speaker, msg)
    if visual_line:
        lines.append(visual_line)
    return lines


def build_session_transcripts(
    sample: dict,
) -> list[dict]:
    conv = sample.get("conversation", {})
    session_keys = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("_date_time")],
        key=_get_session_number,
    )

    transcripts = []

    for session_key in session_keys:
        date_time = conv.get(f"{session_key}_date_time", "")
        lines = _session_header_lines(conv, session_key, date_time)

        for msg in conv[session_key]:
            lines.extend(_message_lines(msg))

        lines.append("---")
        transcripts.append(
            {
                "sample_id": sample["sample_id"],
                "session": session_key,
                "date_time": date_time,
                "transcript": "\n".join(lines),
            }
        )
    return transcripts


def format_transcript(sample: dict) -> str:
    sample_with_id = dict(sample)
    sample_with_id.setdefault("sample_id", "")
    return "\n\n".join(
        item["transcript"]
        for item in build_session_transcripts(
            sample_with_id,
        )
    )


def _build_session_id(sample_id: str, session_key: str) -> str:
    return f"{DEFAULT_SESSION_PREFIX}-{sample_id}-{session_key}"


def ingest_session(
    sample_id: str,
    session_key: str,
    transcript: str,
    args: argparse.Namespace,
) -> dict:
    url = f"{args.base_url.rstrip('/')}/v1/chat/completions"
    session_id = _build_session_id(sample_id, session_key)
    headers = {
        "Authorization": f"Bearer {args.token}",
        "Content-Type": "application/json",
        "X-Hermes-Session-Id": session_id,
    }
    messages = [
        {"role": "system", "content": DEFAULT_IMPORT_ACK_PROMPT},
        {"role": "user", "content": transcript},
    ]
    payload = {
        "model": args.model,
        "messages": messages,
    }

    started_at = time.perf_counter()
    resp = requests.post(url, headers=headers, json=payload, timeout=args.timeout)
    elapsed = time.perf_counter() - started_at
    resp.raise_for_status()
    body = resp.json()
    resolved_session_id = resp.headers.get("X-Hermes-Session-Id", session_id)

    usage = body.get("usage", {})
    total_tokens = usage.get("total_tokens", 0)
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
    cache_read = usage.get("cache_read_tokens", usage.get("cacheRead", 0))
    cache_write = usage.get("cache_write_tokens", usage.get("cacheWrite", 0))

    print(f"  -> [{sample_id}/{session_key}] Ingested in {elapsed:.2f}s (Tokens: {total_tokens})")

    return {
        "sample_id": sample_id,
        "session": session_key,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "conversation": resolved_session_id,
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "session_id": resolved_session_id,
        "status": "success",
    }


def load_success_keys(csv_path: str) -> set[str]:
    keys: set[str] = set()
    if not os.path.exists(csv_path):
        return keys
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sample_id = row.get("sample_id", "")
            session_key = row.get("session", "")
            if not sample_id:
                continue
            if session_key:
                keys.add(f"{sample_id}:{session_key}")
            else:
                keys.add(f"{sample_id}:*")
    return keys


def is_already_ingested(sample_id: str, session_key: str, success_keys: set[str]) -> bool:
    return f"{sample_id}:{session_key}" in success_keys or f"{sample_id}:*" in success_keys


def _unwrap_openviking_dict(body: object) -> dict:
    if not isinstance(body, dict):
        return {}
    result = body.get("result")
    if isinstance(result, dict):
        return result
    return body


def _read_openviking_session(base_url: str, session_id: str) -> dict | None:
    resp = requests.get(
        f"{base_url}/api/v1/sessions/{session_id}",
        headers=openviking_headers(),
        timeout=10,
    )
    if resp.status_code != 200:
        return None
    try:
        body = resp.json()
    except Exception:
        return None
    if isinstance(body, dict) and body.get("status") not in {None, "ok"}:
        return None
    session = _unwrap_openviking_dict(body)
    return session if session else None


def _resolve_openviking_workspace() -> Path | None:
    source = os.getenv("OPENVIKING_STATE_SOURCE", "").strip()
    if source:
        return Path(source).expanduser().resolve()

    config_candidates: list[Path] = []
    if os.getenv("OPENVIKING_CONFIG_FILE"):
        config_candidates.append(Path(os.environ["OPENVIKING_CONFIG_FILE"]).expanduser())
    config_candidates.append(Path.home() / ".openviking" / "ov.conf")
    config_candidates.append(Path("/etc/openviking/ov.conf"))

    for config_path in config_candidates:
        if not config_path.exists():
            continue
        try:
            raw = os.path.expandvars(config_path.read_text(encoding="utf-8-sig"))
            data = json.loads(raw)
        except Exception:
            continue
        storage = data.get("storage", {})
        if isinstance(storage, dict) and storage.get("workspace"):
            return Path(storage["workspace"]).expanduser().resolve()

    return (Path.home() / ".openviking" / "data").expanduser().resolve()


def _find_done_marker(session_id: str) -> Path | None:
    workspace = _resolve_openviking_workspace()
    if workspace is None or not workspace.exists():
        return None

    account = os.getenv("OPENVIKING_ACCOUNT", "default")
    direct_history = workspace / "viking" / account / "session" / session_id / "history"
    try:
        if direct_history.exists():
            for path in sorted(direct_history.glob("*/.done")):
                return path
    except OSError:
        return None
    return None


def _can_check_done_marker() -> bool:
    workspace = _resolve_openviking_workspace()
    return bool(workspace and workspace.exists())


def _wait_for_done_marker(session_id: str, timeout_sec: float) -> bool | None:
    if not _can_check_done_marker():
        return None

    deadline = time.monotonic() + max(0.0, timeout_sec)
    while True:
        if _find_done_marker(session_id):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(COMMIT_TASK_POLL_INTERVAL_SEC)


def _wait_for_session_write_barrier(base_url: str, session_id: str) -> tuple[bool, int, int]:
    last_pending_tokens = 0
    last_message_count = 0

    for _ in range(COMMIT_WAIT_ATTEMPTS):
        if _wait_for_done_marker(session_id, 0) is True:
            return True, last_pending_tokens, last_message_count

        session = _read_openviking_session(base_url, session_id)
        if session:
            last_pending_tokens = int(session.get("pending_tokens") or 0)
            last_message_count = int(session.get("message_count") or 0)
            if last_message_count >= EXPECTED_E2E_OPENVIKING_MESSAGES:
                print(
                    f"  -> [{session_id}] OpenViking write barrier passed "
                    f"(pending_tokens={last_pending_tokens}, message_count={last_message_count})"
                )
                return True, last_pending_tokens, last_message_count

        time.sleep(COMMIT_WAIT_INTERVAL_SEC)

    return False, last_pending_tokens, last_message_count


def _read_latest_commit_task(base_url: str, session_id: str) -> dict | None:
    try:
        resp = requests.get(
            f"{base_url}/api/v1/tasks",
            headers=openviking_headers(),
            params={
                "task_type": "session_commit",
                "resource_id": session_id,
                "limit": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        return None

    result = body.get("result") if isinstance(body, dict) else None
    if isinstance(result, list) and result:
        task = result[0]
        return task if isinstance(task, dict) else None
    return None


def _wait_for_done_after_task_completion(session_id: str) -> bool:
    done = _wait_for_done_marker(session_id, 30)
    if done is not True:
        raise RuntimeError(f"task completed but .done marker is not visible for {session_id}")
    print(f"  -> [{session_id}] OpenViking memory extraction completed")
    return True


def _wait_for_task_completion(
    base_url: str, session_id: str, task_id: str, deadline: float
) -> bool:
    task_url = f"{base_url}/api/v1/tasks/{task_id}"
    last_status = "unknown"

    while time.monotonic() < deadline:
        if _wait_for_done_marker(session_id, 0) is True:
            print(f"  -> [{session_id}] OpenViking memory extraction completed (.done)")
            return True

        task_resp = requests.get(task_url, headers=openviking_headers(), timeout=10)
        task_resp.raise_for_status()
        task_body = task_resp.json()
        task_result = _unwrap_openviking_dict(task_body)
        last_status = str(task_result.get("status") or "unknown")
        if last_status == "completed":
            return _wait_for_done_after_task_completion(session_id)
        if last_status in {"failed", "cancelled"}:
            raise RuntimeError(f"Task {task_id} {last_status}: {task_result}")
        time.sleep(COMMIT_TASK_POLL_INTERVAL_SEC)

    raise RuntimeError(f"Task {task_id} timed out after commit wait (last_status={last_status})")


def _await_existing_or_ambiguous_commit(base_url: str, session_id: str, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if _wait_for_done_marker(session_id, 0) is True:
            print(f"  -> [{session_id}] OpenViking memory extraction completed (.done)")
            return True

        task = _read_latest_commit_task(base_url, session_id)
        if task:
            status = str(task.get("status") or "unknown")
            if status == "completed":
                return _wait_for_done_after_task_completion(session_id)
            if status in {"failed", "cancelled"}:
                raise RuntimeError(f"Task {task.get('task_id')} {status}: {task}")
            time.sleep(COMMIT_TASK_POLL_INTERVAL_SEC)
            continue

        session = _read_openviking_session(base_url, session_id)
        if session:
            pending_tokens = int(session.get("pending_tokens") or 0)
            message_count = int(session.get("message_count") or 0)
            if message_count >= EXPECTED_E2E_OPENVIKING_MESSAGES:
                return False
            if pending_tokens == 0 and message_count == 0:
                time.sleep(COMMIT_TASK_POLL_INTERVAL_SEC)
                continue

        time.sleep(COMMIT_TASK_POLL_INTERVAL_SEC)

    raise RuntimeError(f"Timed out waiting for ambiguous commit completion for {session_id}")


def commit_openviking_session(
    openviking_url: str,
    session_id: str,
    task_timeout_sec: int | None = None,
) -> bool:
    try:
        if task_timeout_sec is None:
            task_timeout_sec = int(os.getenv("QUEUE_MAX_WAIT_SEC", "1800"))

        base_url = openviking_url.rstrip("/")
        url = f"{base_url}/api/v1/sessions/{session_id}/commit"
        headers = openviking_headers(content_type=True)
        deadline = time.monotonic() + max(1, task_timeout_sec)

        if _wait_for_done_marker(session_id, 0) is True:
            print(f"  -> [{session_id}] OpenViking memory extraction already completed (.done)")
            return True

        ready, last_pending_tokens, last_message_count = _wait_for_session_write_barrier(
            base_url,
            session_id,
        )
        if _wait_for_done_marker(session_id, 0) is True:
            print(f"  -> [{session_id}] OpenViking memory extraction already completed (.done)")
            return True
        if not ready:
            raise RuntimeError(
                f"session {session_id} did not reach the OpenViking write barrier before "
                f"commit (expected_message_count={EXPECTED_E2E_OPENVIKING_MESSAGES}, "
                f"pending_tokens={last_pending_tokens}, message_count={last_message_count})"
            )

        for attempt in range(1, COMMIT_POST_ATTEMPTS + 1):
            if _wait_for_done_marker(session_id, 0) is True:
                print(f"  -> [{session_id}] OpenViking memory extraction already completed (.done)")
                return True

            try:
                resp = requests.post(url, headers=headers, timeout=30)
                resp.raise_for_status()
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                result = _unwrap_openviking_dict(body)
                task_id = result.get("task_id")
                print(
                    f"  -> [{session_id}] Triggered OpenViking session commit (task_id={task_id})"
                )

                if task_id:
                    return _wait_for_task_completion(base_url, session_id, task_id, deadline)

                done = _wait_for_done_marker(session_id, max(0, deadline - time.monotonic()))
                if done is not True:
                    raise RuntimeError(f"session {session_id} committed but .done was not written")
                return True
            except Exception as e:
                print(
                    f"  -> [{session_id}] OpenViking commit attempt "
                    f"{attempt}/{COMMIT_POST_ATTEMPTS} did not finish cleanly: {e}"
                )
                if _await_existing_or_ambiguous_commit(base_url, session_id, deadline):
                    return True
                if attempt >= COMMIT_POST_ATTEMPTS:
                    raise
                time.sleep(COMMIT_TASK_POLL_INTERVAL_SEC)

        return False
    except Exception as e:
        print(f"  -> [{session_id}] Failed to trigger OpenViking commit: {e}")
        return False


async def write_success_record(record: dict, csv_path: str) -> None:
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    fieldnames = [
        "timestamp",
        "sample_id",
        "session",
        "conversation",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cache_read",
        "cache_write",
        "status",
    ]

    async with csv_lock:
        file_exists = os.path.exists(csv_path)
        with open(csv_path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow({key: record.get(key, "") for key in fieldnames})


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
        resp = await client.get(
            f"{openviking_url}/api/v1/observer/models", headers=openviking_headers()
        )
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
        resp = await client.get(
            f"{openviking_url}/api/v1/observer/queue", headers=openviking_headers()
        )
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
        started = time.perf_counter()
        idle_streak = 0
        last_totals: tuple[int, int, int] | None = None
        timed_out = False

        while True:
            elapsed = time.perf_counter() - started
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


async def process_session(
    sample_id: str,
    session_key: str,
    transcript: str,
    args: argparse.Namespace,
) -> dict | None:
    max_attempts = max(0, getattr(args, "error_retries", DEFAULT_IMPORT_ERROR_RETRIES)) + 1
    retry_delay = float(getattr(args, "retry_delay_sec", 2.0))
    loop = asyncio.get_running_loop()
    record: dict | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            record = await loop.run_in_executor(
                None,
                ingest_session,
                sample_id,
                session_key,
                transcript,
                args,
            )
            break
        except Exception as e:
            if attempt >= max_attempts:
                print(f"  -> [{sample_id}/{session_key}] ERROR after {attempt} attempt(s): {e}")
                return None
            print(
                f"  -> [{sample_id}/{session_key}] ERROR attempt {attempt}/{max_attempts}: {e}; retrying"
            )
            await asyncio.sleep(retry_delay)

    try:
        session_id = record.get("session_id")
        if not session_id:
            raise RuntimeError("Hermes response did not include a session id")

        committed = False
        for attempt in range(1, max_attempts + 1):
            committed = await loop.run_in_executor(
                None,
                commit_openviking_session,
                args.openviking_url,
                session_id,
            )
            if committed:
                break
            if attempt < max_attempts:
                print(
                    f"  -> [{session_id}] Commit failed attempt {attempt}/{max_attempts}; retrying"
                )
                await asyncio.sleep(retry_delay)
        if not committed:
            raise RuntimeError("OpenViking session commit failed")

        await write_success_record(record, args.success_csv)
        return record
    except Exception as e:
        print(f"  -> [{sample_id}/{session_key}] ERROR: {e}")
        return None


async def main() -> None:
    script_dir = Path(__file__).parent.resolve()
    default_input = default_locomo_input(script_dir)
    default_success_csv = str(script_dir / "result_e2e" / "import_success.csv")

    parser = argparse.ArgumentParser(
        description="Ingest LoCoMo transcripts into Hermes OpenViking E2E"
    )
    parser.add_argument("--input", default=default_input, help="Path to LoCoMo JSON")
    parser.add_argument("--success-csv", default=default_success_csv, help="Success records CSV")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Hermes gateway URL")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Hermes gateway token")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Hermes model name")
    parser.add_argument("--sample", type=int, default=None, help="Sample index to process")
    parser.add_argument("--timeout", type=int, default=1200, help="Request timeout")
    parser.add_argument(
        "--force-ingest", action="store_true", help="Ignore existing records and re-ingest"
    )
    parser.add_argument(
        "--openviking-url", default=DEFAULT_OPENVIKING_URL, help="OpenViking service URL"
    )
    parser.add_argument("--parallel", type=int, default=4, help="Parallel ingest workers")
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
        help="Retry failed import sessions/commits this many times",
    )
    parser.add_argument(
        "--retry-delay-sec",
        type=float,
        default=2.0,
        help="Seconds to wait between import retry attempts",
    )
    args = parser.parse_args()
    os.environ["QUEUE_MAX_WAIT_SEC"] = str(args.queue_max_wait_sec)

    if not args.token:
        print("Error: API token required", file=sys.stderr)
        sys.exit(1)

    if args.force_ingest:
        reset_generated_file(args.success_csv, "import success CSV")
        reset_generated_file(
            Path(args.success_csv).parent / "import_true_tokens.csv", "import true-token CSV"
        )

    existing_success_keys = set()
    if not args.force_ingest:
        existing_success_keys = load_success_keys(args.success_csv)

    samples = load_locomo_data(args.input, args.sample)

    print(
        f"Starting E2E ingest for {len(samples)} samples with {args.parallel} workers "
        "(commit=locomo-session, session_ids=locomo-session)..."
    )

    async with httpx.AsyncClient() as snap_client:
        baseline_totals = await _read_queue_totals(snap_client, args.openviking_url)
        baseline_model_totals = await _read_model_totals(snap_client, args.openviking_url)
    baseline_processed = baseline_totals[2] if baseline_totals else 0
    print(f"[INFO] Observer baseline processed={baseline_processed}", file=sys.stderr)

    semaphore = asyncio.Semaphore(max(1, args.parallel))
    failed_sessions: list[str] = []

    async def run_limited(sample_id: str, session_key: str, payload: dict) -> None:
        async with semaphore:
            record = await process_session(sample_id, session_key, payload["transcript"], args)
            if not record:
                failed_sessions.append(f"{sample_id}/{session_key}")
            await asyncio.sleep(1)

    tasks = []
    for item in samples:
        sample_id = item["sample_id"]
        for session_payload in build_session_transcripts(item):
            session_key = session_payload["session"]
            if not args.force_ingest and is_already_ingested(
                sample_id, session_key, existing_success_keys
            ):
                print(f"  -> [{sample_id}/{session_key}] SKIP (already ingested)")
                continue
            tasks.append(run_limited(sample_id, session_key, session_payload))

    if tasks:
        await asyncio.gather(*tasks)

    print("Ingest complete.")

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


if __name__ == "__main__":
    asyncio.run(main())
