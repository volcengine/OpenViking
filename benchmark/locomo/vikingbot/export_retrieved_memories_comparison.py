#!/usr/bin/env python3
"""Export per-query retrieved-memory details for two LoCoMo runs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

RAW_MEMORY_HEADER_RE = re.compile(
    r"\[RAW_MEMORIES\]\r?\nuser_memory\[(?P<count>\d+)\],"
    r"strategy=(?P<strategy>[^:\r\n]+):\r?\n"
)
RAW_MIDTERM_MEMORY_HEADER_RE = re.compile(
    r"\[RAW_MIDTERM_MEMORIES\]\r?\nmidterm_memory\[(?P<count>\d+)\]:\r?\n"
)
RAW_MEMORY_ROW_RE = re.compile(
    r"^(?P<rank>\d+),(?P<uri>viking://[^,\r\n]+),"
    r"(?P<score>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare concrete memories retrieved for every query in two LoCoMo runs."
    )
    parser.add_argument("--baseline-run", required=True, type=Path)
    parser.add_argument("--feature-run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--baseline-output",
        type=Path,
        help="Optional standalone JSON containing only baseline query retrievals.",
    )
    parser.add_argument(
        "--feature-output",
        type=Path,
        help="Optional standalone JSON containing only feature query retrievals.",
    )
    return parser.parse_args()


def load_result_rows(run_dir: Path) -> dict[int, dict[str, str]]:
    csv_path = run_dir / "locomo_result.csv"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {int(row["question_index"]): row for row in rows}


def extract_summary(content: str) -> str:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() != "# summary":
            continue
        summary: list[str] = []
        for candidate in lines[index + 1 :]:
            stripped = candidate.strip()
            if stripped.startswith("#") or stripped.startswith("<!-- MEMORY_FIELDS"):
                break
            if stripped:
                summary.append(stripped)
        if summary:
            return "\n".join(summary)
    return "\n".join(line.strip() for line in lines if line.strip())[:1000]


def _locomo_message_text(message: dict[str, Any]) -> str:
    text = str(message.get("text") or "").strip()
    image_parts: list[str] = []
    caption = str(message.get("blip_caption") or "").strip()
    query = str(message.get("query") or "").strip()
    if caption:
        image_parts.append(f"image description: {caption}")
    if query:
        image_parts.append(f"image search/query text: {query}")
    if not image_parts:
        return text
    image_note = f"(attached image; {'; '.join(image_parts)})"
    return f"{text}\n{image_note}" if text else image_note


def reconstruct_midterm_payloads(data_path: Path) -> dict[str, dict[str, Any]]:
    """Rebuild deterministic mid-term recall files deleted after conversion."""

    if not data_path.is_file():
        return {}
    raw = json.loads(data_path.read_text(encoding="utf-8"))
    payloads: dict[str, dict[str, Any]] = {}
    for sample in raw:
        conversation = sample.get("conversation") or {}
        peer_id = str(sample.get("sample_id") or "")
        session_keys = sorted(
            (
                key
                for key in conversation
                if key.startswith("session_") and not key.endswith("_date_time")
            ),
            key=lambda value: int(value.removeprefix("session_")),
        )
        for session_key in session_keys:
            base_datetime = datetime.strptime(
                conversation[f"{session_key}_date_time"],
                "%I:%M %p on %d %B, %Y",
            )
            blocks: list[dict[str, Any]] = []
            for index, message in enumerate(conversation[session_key]):
                speaker = str(message.get("speaker") or "unknown").strip()
                text = _locomo_message_text(message)
                created_at = (base_datetime + timedelta(seconds=index)).isoformat()
                if blocks and blocks[-1]["speaker"].casefold() == speaker.casefold():
                    blocks[-1]["ended_at"] = created_at
                    blocks[-1]["texts"].append(text)
                else:
                    blocks.append(
                        {
                            "speaker": speaker,
                            "role": "user",
                            "peer_id": peer_id,
                            "created_at": created_at,
                            "ended_at": created_at,
                            "texts": [text],
                        }
                    )

            for offset in range(0, len(blocks), 2):
                pair = blocks[offset : offset + 2]
                canonical = json.dumps(
                    pair,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                kind = "dialogue_pair" if len(pair) == 2 else "single_turn"
                prefix = "pair" if kind == "dialogue_pair" else "single_turn"
                participants = ", ".join(
                    dict.fromkeys(block["speaker"] for block in pair)
                )
                lines = [
                    "# Dialogue Pair" if kind == "dialogue_pair" else "# Single Turn",
                    "",
                    "Source-Kind: midterm_qa",
                    f"Participants: {participants}",
                    f"Timestamp: {pair[0]['created_at']}",
                    f"Content-Hash: {content_hash}",
                ]
                for block in pair:
                    lines.extend(
                        ["", f"## {block['speaker']}", "", "\n".join(block["texts"])]
                    )
                content = "\n".join(lines).rstrip() + "\n"
                payloads[f"{prefix}_{content_hash}.md"] = {
                    "summary": extract_summary(content),
                    "content": content,
                    "content_source": "locomo_dataset_reconstruction",
                }
    return payloads


def uri_to_snapshot_path(uri: str, snapshot_root: Path) -> Path | None:
    if not uri.startswith("viking://user/"):
        return None
    logical_path = uri[len("viking://user/") :].split("?", 1)[0].split("#", 1)[0]
    parts = [part for part in logical_path.split("/") if part]
    if len(parts) >= 3 and parts[1] == "peers":
        relative_parts = parts[2:]
    elif len(parts) >= 2:
        relative_parts = parts[1:]
    else:
        return None
    return snapshot_root.joinpath(*relative_parts)


def memory_payload(
    uri: str,
    snapshot_root: Path,
    repo_root: Path,
    reconstructed_midterms: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    path = uri_to_snapshot_path(uri, snapshot_root)
    if path is None:
        return {
            "memory_type": None,
            "summary": None,
            "content": None,
            "snapshot_path": None,
            "snapshot_found": False,
            "content_source": None,
        }

    try:
        relative_path = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        relative_path = path.as_posix()

    memory_type = None
    uri_parts = uri.split("/")
    try:
        memories_index = uri_parts.index("memories")
        if len(uri_parts) > memories_index + 1:
            memory_type = uri_parts[memories_index + 1]
    except ValueError:
        pass

    if not path.is_file():
        reconstructed = reconstructed_midterms.get(path.name)
        if memory_type == "midterm_qa" and reconstructed is not None:
            return {
                "memory_type": memory_type,
                "summary": reconstructed["summary"],
                "content": reconstructed["content"],
                "snapshot_path": relative_path,
                "snapshot_found": False,
                "content_source": reconstructed["content_source"],
            }
        return {
            "memory_type": memory_type,
            "summary": None,
            "content": None,
            "snapshot_path": relative_path,
            "snapshot_found": False,
            "content_source": None,
        }

    content = path.read_text(encoding="utf-8", errors="replace")
    return {
        "memory_type": memory_type,
        "summary": extract_summary(content),
        "content": content,
        "snapshot_path": relative_path,
        "snapshot_found": True,
        "content_source": "run_snapshot",
    }


def parse_retrieval_rounds(
    log_path: Path,
    snapshot_root: Path,
    repo_root: Path,
    reconstructed_midterms: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    rounds: list[dict[str, Any]] = []
    payload_cache: dict[str, dict[str, Any]] = {}

    headers = [
        (match.start(), match, match.group("strategy"))
        for match in RAW_MEMORY_HEADER_RE.finditer(text)
    ]
    headers.extend(
        (match.start(), match, "midterm_vector")
        for match in RAW_MIDTERM_MEMORY_HEADER_RE.finditer(text)
    )
    headers.sort(key=lambda item: item[0])

    for round_index, (_, match, strategy) in enumerate(headers, start=1):
        memories: list[dict[str, Any]] = []
        tail = text[match.end() :]
        for line in tail.splitlines():
            row_match = RAW_MEMORY_ROW_RE.match(line.strip())
            if row_match is None:
                break
            uri = row_match.group("uri")
            payload = payload_cache.setdefault(
                uri,
                memory_payload(
                    uri,
                    snapshot_root,
                    repo_root,
                    reconstructed_midterms,
                ),
            )
            memories.append(
                {
                    "rank": int(row_match.group("rank")),
                    "uri": uri,
                    "score": float(row_match.group("score")),
                    **payload,
                }
            )
        rounds.append(
            {
                "round": round_index,
                "strategy": strategy,
                "reported_count": int(match.group("count")),
                "parsed_count": len(memories),
                "memories": memories,
            }
        )
    return rounds


def result_log_path(run_dir: Path, row: dict[str, str]) -> Path:
    filename = Path(row.get("bot_log_file", "").replace("\\", "/")).name
    if not filename:
        filename = f"vikingbot.{row['sample_id']}_qa{row['question_index']}.log"
    return run_dir / "locomo_result_bot_logs" / filename


def arm_payload(
    run_dir: Path,
    row: dict[str, str],
    repo_root: Path,
    reconstructed_midterms: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    log_path = result_log_path(run_dir, row)
    rounds = (
        parse_retrieval_rounds(
            log_path,
            run_dir / "memories",
            repo_root,
            reconstructed_midterms,
        )
        if log_path.is_file()
        else []
    )
    unique_memories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for retrieval_round in rounds:
        for memory in retrieval_round["memories"]:
            if memory["uri"] in seen:
                continue
            seen.add(memory["uri"])
            unique_memories.append(memory)
    return {
        "judge_result": row.get("result"),
        "response": row.get("response"),
        "reasoning": row.get("reasoning"),
        "time_cost_seconds": float(row["time_cost"]) if row.get("time_cost") else None,
        "iteration": int(float(row["iteration"])) if row.get("iteration") else None,
        "tools_used_names": json.loads(row.get("tools_used_names") or "[]"),
        "log_path": log_path.resolve().relative_to(repo_root.resolve()).as_posix(),
        "log_found": log_path.is_file(),
        "retrieval_rounds": rounds,
        "unique_retrieved_count": len(unique_memories),
        "unique_retrieved_memories": unique_memories,
    }


def correctness_change(baseline_result: str | None, feature_result: str | None) -> str:
    baseline_correct = baseline_result == "CORRECT"
    feature_correct = feature_result == "CORRECT"
    if baseline_correct and feature_correct:
        return "both_correct"
    if not baseline_correct and feature_correct:
        return "feature_improved"
    if baseline_correct and not feature_correct:
        return "feature_regressed"
    return "both_wrong"


def compare_retrievals(
    baseline: dict[str, Any], feature: dict[str, Any]
) -> dict[str, Any]:
    baseline_memories = {
        item["uri"]: item for item in baseline["unique_retrieved_memories"]
    }
    feature_memories = {
        item["uri"]: item for item in feature["unique_retrieved_memories"]
    }
    baseline_uris = set(baseline_memories)
    feature_uris = set(feature_memories)
    common_uris = sorted(baseline_uris & feature_uris)
    union_uris = baseline_uris | feature_uris
    return {
        "correctness_change": correctness_change(
            baseline.get("judge_result"), feature.get("judge_result")
        ),
        "baseline_only_uris": sorted(baseline_uris - feature_uris),
        "feature_only_uris": sorted(feature_uris - baseline_uris),
        "common_memories": [
            {
                "uri": uri,
                "baseline_rank": baseline_memories[uri]["rank"],
                "baseline_score": baseline_memories[uri]["score"],
                "feature_rank": feature_memories[uri]["rank"],
                "feature_score": feature_memories[uri]["score"],
                "score_delta": feature_memories[uri]["score"]
                - baseline_memories[uri]["score"],
            }
            for uri in common_uris
        ],
        "uri_jaccard": len(common_uris) / len(union_uris) if union_uris else 1.0,
    }


def standalone_arm_output(
    arm: str,
    run_dir: Path,
    queries: list[dict[str, Any]],
    repo_root: Path,
) -> dict[str, Any]:
    arm_queries = [
        {
            "sample_id": query["sample_id"],
            "question_index": query["question_index"],
            "category": query["category"],
            "question": query["question"],
            "expected_answer": query["expected_answer"],
            "question_time": query["question_time"],
            "evidence": query["evidence"],
            "evidence_text": query["evidence_text"],
            **query[arm],
        }
        for query in queries
    ]
    return {
        "schema_version": 1,
        "description": (
            f"Per-query concrete memories retrieved by the {arm} LoCoMo run. "
            "Memory content is resolved from the run's snapshot."
        ),
        "arm": arm,
        "run": run_dir.relative_to(repo_root.resolve()).as_posix(),
        "summary": {
            "query_count": len(arm_queries),
            "correct": sum(
                query.get("judge_result") == "CORRECT" for query in arm_queries
            ),
            "wrong": sum(
                query.get("judge_result") != "CORRECT" for query in arm_queries
            ),
            "total_unique_retrievals": sum(
                query["unique_retrieved_count"] for query in arm_queries
            ),
            "queries_without_retrievals": sum(
                query["unique_retrieved_count"] == 0 for query in arm_queries
            ),
            "midterm_retrievals": sum(
                memory.get("memory_type") == "midterm_qa"
                for query in arm_queries
                for memory in query["unique_retrieved_memories"]
            ),
            "queries_with_midterm_retrievals": sum(
                any(
                    memory.get("memory_type") == "midterm_qa"
                    for memory in query["unique_retrieved_memories"]
                )
                for query in arm_queries
            ),
            "retrievals_with_content": sum(
                bool(memory.get("content"))
                for query in arm_queries
                for memory in query["unique_retrieved_memories"]
            ),
            "reconstructed_midterm_retrievals": sum(
                memory.get("content_source") == "locomo_dataset_reconstruction"
                for query in arm_queries
                for memory in query["unique_retrieved_memories"]
            ),
        },
        "queries": arm_queries,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    args = parse_args()
    repo_root = Path.cwd()
    baseline_run = args.baseline_run.resolve()
    feature_run = args.feature_run.resolve()
    baseline_rows = load_result_rows(baseline_run)
    feature_rows = load_result_rows(feature_run)
    reconstructed_midterms = reconstruct_midterm_payloads(
        repo_root / "benchmark" / "locomo" / "data" / "locomo10.json"
    )
    question_indexes = sorted(set(baseline_rows) & set(feature_rows))

    queries: list[dict[str, Any]] = []
    for question_index in question_indexes:
        baseline_row = baseline_rows[question_index]
        feature_row = feature_rows[question_index]
        baseline = arm_payload(
            baseline_run, baseline_row, repo_root, reconstructed_midterms
        )
        feature = arm_payload(feature_run, feature_row, repo_root, reconstructed_midterms)
        queries.append(
            {
                "sample_id": feature_row.get("sample_id"),
                "question_index": question_index,
                "category": int(feature_row["category"]),
                "question": feature_row.get("question"),
                "expected_answer": feature_row.get("answer"),
                "question_time": feature_row.get("question_time"),
                "evidence": json.loads(feature_row.get("evidence") or "[]"),
                "evidence_text": json.loads(feature_row.get("evidence_text") or "[]"),
                "baseline": baseline,
                "feature": feature,
                "comparison": compare_retrievals(baseline, feature),
            }
        )

    change_counts: dict[str, int] = {}
    for query in queries:
        change = query["comparison"]["correctness_change"]
        change_counts[change] = change_counts.get(change, 0) + 1

    output = {
        "schema_version": 1,
        "description": (
            "Per-query comparison of concrete memories retrieved by the baseline and "
            "feature LoCoMo runs. Memory content is resolved from each run's snapshot."
        ),
        "baseline_run": baseline_run.relative_to(repo_root.resolve()).as_posix(),
        "feature_run": feature_run.relative_to(repo_root.resolve()).as_posix(),
        "summary": {
            "matched_queries": len(queries),
            "correctness_changes": change_counts,
            "baseline_total_unique_retrievals": sum(
                query["baseline"]["unique_retrieved_count"] for query in queries
            ),
            "feature_total_unique_retrievals": sum(
                query["feature"]["unique_retrieved_count"] for query in queries
            ),
            "baseline_queries_without_retrievals": sum(
                query["baseline"]["unique_retrieved_count"] == 0 for query in queries
            ),
            "feature_queries_without_retrievals": sum(
                query["feature"]["unique_retrieved_count"] == 0 for query in queries
            ),
            "baseline_midterm_retrievals": sum(
                memory.get("memory_type") == "midterm_qa"
                for query in queries
                for memory in query["baseline"]["unique_retrieved_memories"]
            ),
            "feature_midterm_retrievals": sum(
                memory.get("memory_type") == "midterm_qa"
                for query in queries
                for memory in query["feature"]["unique_retrieved_memories"]
            ),
        },
        "queries": queries,
    }

    write_json(args.output, output)
    print(args.output)

    if args.baseline_output:
        write_json(
            args.baseline_output,
            standalone_arm_output("baseline", baseline_run, queries, repo_root),
        )
        print(args.baseline_output)
    if args.feature_output:
        write_json(
            args.feature_output,
            standalone_arm_output("feature", feature_run, queries, repo_root),
        )
        print(args.feature_output)


if __name__ == "__main__":
    main()
