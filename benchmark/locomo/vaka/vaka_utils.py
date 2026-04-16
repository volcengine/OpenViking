from __future__ import annotations

import csv
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any


DEFAULT_INPUT = "data/vaka_locomo.csv"
DEFAULT_CASE_SIZE = 10
SCRIPT_DIR = Path(__file__).parent

REFERENCE_RE = re.compile(r"<reference\b[^>]*>.*?</reference>", re.IGNORECASE | re.DOTALL)


def strip_references(text: str | None) -> str:
    """Remove Vaka inline reference tags and keep answer text readable."""
    if not text:
        return ""
    cleaned = REFERENCE_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def parse_session_selector(selector: str) -> set[int]:
    """Parse selectors like '1-7', '8,9,10', or '8-10' into local session IDs."""
    selected: set[int] = set()
    for chunk in selector.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo_text, hi_text = chunk.split("-", 1)
            lo = int(lo_text.strip())
            hi = int(hi_text.strip())
            if lo > hi:
                raise ValueError(f"Invalid session range: {chunk}")
            selected.update(range(lo, hi + 1))
        else:
            selected.add(int(chunk))
    if not selected:
        raise ValueError("Session selector cannot be empty")
    return selected


def _parse_positive_int(value: str | None, *, field: str, row_number: int) -> int:
    text = (value or "").strip()
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}={text!r} at CSV row {row_number}") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be positive at CSV row {row_number}: {parsed}")
    return parsed


def load_vaka_cases(input_path: str, case_size: int = DEFAULT_CASE_SIZE) -> list[dict[str, Any]]:
    """Load a Vaka CSV and group rows by global session-id blocks.

    Case grouping follows the benchmark rule:
    session_id 1-10 is case 1, 11-20 is case 2, 21-30 is case 3, ...
    Within each case, local_session_id 1-7 are memory turns and 8-10 are eval turns.
    """
    path = Path(input_path).expanduser()
    if not path.is_absolute() and not path.exists():
        path = SCRIPT_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Input CSV has no header: {path}")
        rows = list(reader)

    required = {"session_id", "query"}
    missing = sorted(required - set(reader.fieldnames))
    if missing:
        raise ValueError(f"Input CSV missing required columns: {', '.join(missing)}")

    cases: OrderedDict[int, dict[str, Any]] = OrderedDict()
    for row_index, row in enumerate(rows):
        row_number = row_index + 2
        session_id = _parse_positive_int(
            row.get("session_id"), field="session_id", row_number=row_number
        )
        case_index = (session_id - 1) // case_size
        local_session_id = ((session_id - 1) % case_size) + 1
        session_start = case_index * case_size + 1
        session_end = session_start + case_size - 1

        enriched = dict(row)
        enriched["_row_index"] = row_index
        enriched["_row_number"] = row_number
        enriched["_case_index"] = case_index
        enriched["_case_id"] = f"case_{case_index + 1:04d}"
        enriched["_case_session_range"] = f"{session_start}-{session_end}"
        enriched["_global_session_id"] = session_id
        enriched["_local_session_id"] = local_session_id

        if case_index not in cases:
            cases[case_index] = {
                "case_id": enriched["_case_id"],
                "case_index": case_index,
                "session_start": session_start,
                "session_end": session_end,
                "session_range": enriched["_case_session_range"],
                "rows": [],
            }
        cases[case_index]["rows"].append(enriched)

    return list(cases.values())


def select_cases(cases: list[dict[str, Any]], selector: str | None) -> list[dict[str, Any]]:
    """Select cases by case_id or 1-based case number. '0' is accepted for the first case."""
    if not selector:
        return cases

    selected: list[dict[str, Any]] = []
    wanted = {part.strip() for part in selector.split(",") if part.strip()}
    for case in cases:
        case_id = case["case_id"]
        one_based_index = case["case_index"] + 1
        include = case_id in wanted
        for token in wanted:
            if include:
                break
            try:
                numeric = int(token)
            except ValueError:
                continue
            if numeric == one_based_index or (numeric == 0 and one_based_index == 1):
                include = True
        if include:
            selected.append(case)

    missing = sorted(
        token
        for token in wanted
        if not any(
            token == case["case_id"]
            or token == str(case["case_index"] + 1)
            or (token == "0" and case["case_index"] == 0)
            for case in selected
        )
    )
    if missing:
        raise ValueError(f"Case selector did not match any case: {', '.join(missing)}")
    return selected


def choose_response(row: dict[str, Any], answer_column: str) -> str:
    response = (row.get(answer_column) or "").strip()
    if not response and answer_column != "deepsearch_answer":
        response = (row.get("deepsearch_answer") or "").strip()
    return response


def choose_response_without_refs(row: dict[str, Any], response: str) -> str:
    answer_without_ref = (row.get("answer_without_ref") or "").strip()
    return answer_without_ref or strip_references(response)


def format_turn(
    row: dict[str, Any],
    *,
    answer_column: str = "deepsearch_answer",
    strip_answer_refs: bool = True,
) -> str:
    """Format one CSV row as a query + answer turn for judge context."""
    response = choose_response(row, answer_column)
    if strip_answer_refs:
        response = choose_response_without_refs(row, response)

    meta_parts = [
        f"case={row['_case_id']}",
        f"global_session={row['_global_session_id']}",
        f"local_session={row['_local_session_id']}",
    ]
    if row.get("round"):
        meta_parts.append(f"round={row['round']}")
    if row.get("item_id"):
        meta_parts.append(f"item_id={row['item_id']}")
    doc = row.get("used_doc") or row.get("doc_base")
    if doc:
        meta_parts.append(f"doc={doc}")

    query = (row.get("query") or "").strip()
    return "\n".join(
        [
            f"[{' | '.join(meta_parts)}]",
            f"User: {query}",
            f"Assistant: {response}",
        ]
    ).strip()


def build_context_for_row(
    case: dict[str, Any],
    current_row: dict[str, Any],
    *,
    memory_sessions: set[int],
    eval_sessions: set[int],
    answer_column: str,
    include_eval_history: bool,
) -> tuple[str, str]:
    """Build memory and prior eval context for one eval row."""
    memory_turns = [
        format_turn(row, answer_column=answer_column)
        for row in case["rows"]
        if row["_local_session_id"] in memory_sessions
    ]
    memory_context = "\n\n".join(memory_turns)

    eval_history = ""
    if include_eval_history:
        history_turns = [
            format_turn(row, answer_column=answer_column)
            for row in case["rows"]
            if row["_local_session_id"] in eval_sessions
            and row["_row_index"] < current_row["_row_index"]
        ]
        eval_history = "\n\n".join(history_turns)

    return memory_context, eval_history


def present_session_ids(case: dict[str, Any]) -> set[int]:
    return {row["_local_session_id"] for row in case["rows"]}


def case_has_all_sessions(case: dict[str, Any], case_size: int = DEFAULT_CASE_SIZE) -> bool:
    return present_session_ids(case) == set(range(1, case_size + 1))
