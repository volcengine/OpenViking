"""Run existing prepared AML data through Add, then Search."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence, TypeVar

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8088"
DEFAULT_TIMEOUT_SECONDS = 1_000.0
DEFAULT_ADD_CONCURRENCY = 16
DEFAULT_SEARCH_CONCURRENCY = 16
MAX_MESSAGES = 20
MAX_WORDS = 2_000
TOP_K = 100

_WORD = re.compile(r"\S+")
T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class AddOperation:
    user_id: str
    session_id: str
    request_id: str
    messages: list[dict[str, Any]]

    def payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "messages": self.messages,
            "user_id": self.user_id,
            "session_id": self.session_id,
        }


@dataclass(frozen=True, slots=True)
class SearchCase:
    user_id: str
    query: str
    options: list[str]
    output: dict[str, Any]


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _cached_digest(value: Any, cache: dict[int, str]) -> str:
    identity = id(value)
    digest = cache.get(identity)
    if digest is None:
        digest = _digest(value)
        cache[identity] = digest
    return digest


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-blank string")
    return value


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return parsed


def _resolve_concurrency(specific: int | None, shared: int | None, default: int) -> int:
    if specific is not None:
        return specific
    if shared is not None:
        return shared
    return default


def _load_cases(
    paths: Sequence[str | Path], selected_units: Sequence[str] | None
) -> dict[str, list[dict[str, Any]]]:
    selected = set(selected_units) if selected_units else None
    units: dict[str, list[dict[str, Any]]] = {}
    seen: dict[tuple[str, str], str] = {}
    digest_cache: dict[int, str] = {}

    for raw_path in paths:
        path = Path(raw_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list):
            raise ValueError(f"{path}: expected an object with a cases array")
        history_references = {}
        for history in payload.get("histories", []):
            if not isinstance(history, dict):
                raise ValueError(f"{path}: every history must be an object")
            key = (
                _text(history.get("unit"), "history.unit"),
                _text(history.get("history_key"), "history.history_key"),
            )
            sessions = history.get("sessions")
            if not isinstance(sessions, list) or not sessions:
                raise ValueError(f"{path}: history.sessions must be a non-empty array")
            history_references[key] = sessions
        for raw in cases:
            if not isinstance(raw, dict):
                raise ValueError(f"{path}: every case must be an object")
            unit = _text(raw.get("unit"), "case.unit")
            if selected is not None and unit not in selected:
                continue
            case_id = _text(raw.get("case_id"), f"{unit}.case_id")
            normalized = dict(raw)
            if "histories" not in normalized:
                history_key = _text(raw.get("history_key"), f"{case_id}.history_key")
                try:
                    normalized["histories"] = history_references[(unit, history_key)]
                except KeyError as exc:
                    raise ValueError(f"missing history reference: {unit}/{history_key}") from exc
            key = (unit, case_id)
            fingerprint_case = dict(normalized)
            histories = fingerprint_case.pop("histories")
            fingerprint = _digest([fingerprint_case, _cached_digest(histories, digest_cache)])
            if key in seen:
                if seen[key] != fingerprint:
                    raise ValueError(f"conflicting duplicate case: {unit}/{case_id}")
                continue
            seen[key] = fingerprint
            units.setdefault(unit, []).append(normalized)

    missing = sorted((selected or set()) - units.keys())
    if missing:
        raise ValueError(f"selected unit has no prepared cases: {', '.join(missing)}")
    if not units:
        raise ValueError("no prepared cases selected")
    return units


def _split_content(content: str) -> list[str]:
    words = list(_WORD.finditer(content))
    if not words:
        raise ValueError("message.content must be non-blank")
    parts = []
    for first in range(0, len(words), MAX_WORDS):
        last = min(first + MAX_WORDS, len(words)) - 1
        parts.append(content[words[first].start() : words[last].end()])
    return parts


def _message_chunks(raw_messages: Any, label: str) -> list[list[dict[str, Any]]]:
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError(f"{label}.messages must be a non-empty array")

    messages: list[tuple[dict[str, Any], int]] = []
    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            raise ValueError(f"{label}.messages[{index}] must be an object")
        role = raw.get("role")
        if role not in {"user", "assistant"}:
            raise ValueError(f"{label}.messages[{index}].role is invalid")
        content = _text(raw.get("content"), f"{label}.messages[{index}].content")
        timestamp = raw.get("timestamp")
        if timestamp is not None and (
            isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0
        ):
            raise ValueError(f"{label}.messages[{index}].timestamp is invalid")
        for part in _split_content(content):
            message: dict[str, Any] = {"role": role, "content": part}
            if timestamp is not None:
                message["timestamp"] = timestamp
            messages.append((message, len(_WORD.findall(part))))

    chunks: list[list[dict[str, Any]]] = []
    chunk: list[dict[str, Any]] = []
    chunk_words = 0
    for message, words in messages:
        if chunk and (len(chunk) == MAX_MESSAGES or chunk_words + words > MAX_WORDS):
            chunks.append(chunk)
            chunk = []
            chunk_words = 0
        chunk.append(message)
        chunk_words += words
    if chunk:
        chunks.append(chunk)
    return chunks


def _prepare_unit(
    unit: str,
    raw_cases: Sequence[dict[str, Any]],
    namespace: str,
) -> tuple[list[list[AddOperation]], list[SearchCase]]:
    histories: dict[tuple[str, str], str] = {}
    digest_cache: dict[int, str] = {}
    session_groups: dict[tuple[str, str], list[AddOperation]] = {}
    searches: list[SearchCase] = []

    for raw in raw_cases:
        case_id = _text(raw.get("case_id"), f"{unit}.case_id")
        history_key = _text(raw.get("history_key"), f"{case_id}.history_key")
        raw_histories = raw.get("histories")
        if not isinstance(raw_histories, list) or not raw_histories:
            raise ValueError(f"{case_id}.histories must be a non-empty array")
        history_hash = _cached_digest(raw_histories, digest_cache)
        history_identity = (history_key, history_hash)
        user_id = histories.get(history_identity)

        if user_id is None:
            user_id = f"aml-{_digest([namespace, unit, history_key, history_hash])[:32]}"
            histories[history_identity] = user_id
            for session_index, raw_session in enumerate(raw_histories):
                if not isinstance(raw_session, dict):
                    raise ValueError(f"{case_id}.histories[{session_index}] must be an object")
                source_session_id = _text(
                    raw_session.get("session_id"),
                    f"{case_id}.histories[{session_index}].session_id",
                )
                session_id = "session-" + _digest([user_id, session_index, source_session_id])[:32]
                operations = []
                for chunk_index, messages in enumerate(
                    _message_chunks(raw_session.get("messages"), source_session_id)
                ):
                    operations.append(
                        AddOperation(
                            user_id=user_id,
                            session_id=session_id,
                            request_id="request-" + _digest([session_id, chunk_index])[:32],
                            messages=messages,
                        )
                    )
                session_groups[(user_id, session_id)] = operations

        options = raw.get("options", [])
        if not isinstance(options, list) or any(
            not isinstance(option, str) or not option.strip() for option in options
        ):
            raise ValueError(f"{case_id}.options must contain strings")
        extra = raw.get("extra", {})
        if not isinstance(extra, dict):
            raise ValueError(f"{case_id}.extra must be an object")
        question = _text(raw.get("question"), f"{case_id}.question")
        searches.append(
            SearchCase(
                user_id=user_id,
                query=question,
                options=list(options),
                output={
                    **extra,
                    "id": case_id,
                    "unit": unit,
                    "case_id": case_id,
                    "question": question,
                    "gold_answer": raw.get("gold_answer"),
                    "options": options,
                },
            )
        )

    return list(session_groups.values()), searches


async def _bounded_map(
    values: Sequence[T], limit: int, function: Callable[[T], Awaitable[R]]
) -> list[R]:
    semaphore = asyncio.Semaphore(limit)

    async def invoke(value: T) -> R:
        async with semaphore:
            return await function(value)

    tasks = [asyncio.create_task(invoke(value)) for value in values]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        # Stop queued histories and drain in-flight coroutines before the caller
        # closes its HTTP client. Remote Add writes still require reconciliation.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Token {api_key}"} if api_key else {}


async def _post(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post(base_url.rstrip("/") + path, headers=headers, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"{path} returned HTTP {response.status_code}: {response.text[:500]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{path} returned non-JSON data") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"{path} response must be an object")
    return body


async def _add_session(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    operations: Sequence[AddOperation],
) -> None:
    # One AML session may span multiple requests. Each request waits for its
    # commit to complete before the next chunk is sent.
    for operation in operations:
        body = await _post(client, base_url, "/add", headers, operation.payload())
        if body.get("success") is not True:
            raise RuntimeError(f"/add rejected {operation.request_id}")


async def _search(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    case: SearchCase,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": case.query,
        "user_id": case.user_id,
        "top_k": TOP_K,
    }
    if case.options:
        payload["options"] = case.options
    body = await _post(client, base_url, "/search", headers, payload)
    data = body.get("data")
    if not isinstance(data, list):
        raise RuntimeError("/search response must contain a data array")
    contexts = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise RuntimeError(f"/search data[{index}] has no valid id")
        content = item.get("content")
        if not isinstance(content, str) or not content:
            raise RuntimeError(f"/search data[{index}] has no valid content")
        contexts.append(content)
    return {**case.output, "retrieved_context": contexts}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


async def run_public(
    prepared: Sequence[str | Path],
    *,
    output_dir: str | Path,
    namespace: str,
    units: Sequence[str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str | None = None,
    concurrency: int | None = None,
    add_concurrency: int | None = None,
    search_concurrency: int | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Add histories concurrently, preserving session order, then Search."""
    _text(namespace, "namespace")
    if concurrency is not None and concurrency < 1:
        raise ValueError("concurrency must be positive")
    if add_concurrency is not None and add_concurrency < 1:
        raise ValueError("add_concurrency must be positive")
    if search_concurrency is not None and search_concurrency < 1:
        raise ValueError("search_concurrency must be positive")
    resolved_add_concurrency = _resolve_concurrency(
        add_concurrency, concurrency, DEFAULT_ADD_CONCURRENCY
    )
    resolved_search_concurrency = _resolve_concurrency(
        search_concurrency, concurrency, DEFAULT_SEARCH_CONCURRENCY
    )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")

    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("output directory is not empty")
    destination.mkdir(parents=True, exist_ok=True)

    prepared_units = _load_cases(prepared, units)
    headers = _headers(api_key)
    own_client = client is None
    active_client = client or httpx.AsyncClient(timeout=timeout_seconds)
    summaries = []
    try:
        for unit, raw_cases in prepared_units.items():
            session_groups, searches = _prepare_unit(unit, raw_cases, namespace)
            add_count = sum(len(group) for group in session_groups)
            print(f"[{unit}] Add {add_count} requests", flush=True)

            # Sessions belonging to one user update shared memories. Preserve
            # their prepared order and parallelize only across isolated users.
            history_groups: dict[str, list[list[AddOperation]]] = {}
            for group in session_groups:
                history_groups.setdefault(group[0].user_id, []).append(group)

            async def add_history(sessions: list[list[AddOperation]]) -> None:
                for group in sessions:
                    await _add_session(active_client, base_url, headers, group)

            await _bounded_map(list(history_groups.values()), resolved_add_concurrency, add_history)
            print(f"[{unit}] Search {len(searches)} questions", flush=True)

            async def search_case(case: SearchCase) -> dict[str, Any]:
                return await _search(active_client, base_url, headers, case)

            rows = await _bounded_map(searches, resolved_search_concurrency, search_case)
            unit_dir = destination / unit
            unit_dir.mkdir()
            retrieval_path = unit_dir / "retrieval.jsonl"
            _write_jsonl(retrieval_path, rows)
            summaries.append(
                {
                    "unit": unit,
                    "cases": len(rows),
                    "add_requests": add_count,
                    "retrieval": str(retrieval_path.resolve()),
                }
            )
    finally:
        if own_client:
            await active_client.aclose()

    summary = {"success": True, "units": summaries}
    _write_json(destination / "summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", action="append", required=True, type=Path)
    parser.add_argument("--unit", action="append")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--add-concurrency",
        type=_positive_int,
        help=f"parallel Add histories/users (default: {DEFAULT_ADD_CONCURRENCY})",
    )
    parser.add_argument(
        "--search-concurrency",
        type=_positive_int,
        help=f"parallel Search requests (default: {DEFAULT_SEARCH_CONCURRENCY})",
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        help="set both Add and Search concurrency; phase-specific options take precedence",
    )
    parser.add_argument("--timeout-seconds", type=_positive_float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(
            run_public(
                args.prepared,
                output_dir=args.output_dir,
                namespace=args.namespace,
                units=args.unit,
                base_url=args.base_url,
                api_key=os.getenv("AML_API_KEY") or None,
                concurrency=args.concurrency,
                add_concurrency=args.add_concurrency,
                search_concurrency=args.search_concurrency,
                timeout_seconds=args.timeout_seconds,
            )
        )
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
