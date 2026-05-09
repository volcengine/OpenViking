#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from benchmark.longmemeval.vikingbot.judge import create_llm_client
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from judge import create_llm_client


env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)

DEFAULT_AZURE_API_VERSION = "2025-01-01-preview"
MEMORY_FIELDS_RE = re.compile(r"\n\n<!--\s*MEMORY_FIELDS\s*\n.*?\n-->", re.DOTALL)


MEMORY_DIAGNOSIS_PROMPT = """You are diagnosing whether LongMemEval memory files contain enough evidence for a question.

Decide whether the provided memory contents contain sufficient evidence to answer the question with the given correct answer.

Rules:
- Judge evidence sufficiency, not model response quality.
- The answer may require combining multiple files, counting items, comparing dates, or doing arithmetic.
- User facts and assistant suggestions are different. Assistant advice is not user experience unless the user later did it.
- Plans are not completed actions unless the memory clearly says they happened, or gives a dated plan with no contradiction.
- If the correct answer appears inconsistent with the provided memories or judging rubric, set benchmark_ambiguity=true.
- Return related_uris when files are relevant but not sufficient alone.

Return ONLY valid JSON:
{{
  "sufficient": true or false,
  "benchmark_ambiguity": true or false,
  "reason": "short explanation",
  "supporting_uris": ["uri1"],
  "related_uris": ["uri2"]
}}

Question ID: {sample_id}
Question Type: {question_type}
Question Date: {question_time}
Question: {question}
Correct Answer: {answer}
Model Response: {response}

Memory contents:
{evidence}
"""


def build_sample_user_id(sample_id: str | int) -> str:
    digest = hashlib.md5(f"user:{sample_id}".encode("utf-8")).hexdigest()[:12]
    return f"lm_user_{digest}"


def strip_memory_metadata(content: str) -> str:
    return MEMORY_FIELDS_RE.sub("", content or "").strip()


def parse_json_response(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {
            "sufficient": False,
            "benchmark_ambiguity": False,
            "reason": f"[PARSE ERROR] {content}",
            "supporting_uris": [],
            "related_uris": [],
        }
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {
            "sufficient": False,
            "benchmark_ambiguity": False,
            "reason": f"[PARSE ERROR] {content}",
            "supporting_uris": [],
            "related_uris": [],
        }
    return {
        "sufficient": bool(parsed.get("sufficient", False)),
        "benchmark_ambiguity": bool(parsed.get("benchmark_ambiguity", False)),
        "reason": str(parsed.get("reason", "")),
        "supporting_uris": parsed.get("supporting_uris", []) or [],
        "related_uris": parsed.get("related_uris", []) or [],
    }


def normalize_diagnosis_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    reason = str(normalized.get("reason", "")).lower()
    ambiguity_markers = (
        "correct answer appears inconsistent",
        "correct answer is inconsistent",
        "inconsistent with the provided memories",
        "inconsistent with provided memories",
        "inconsistent with the memories",
        "does not match the provided memories",
        "contradicts the provided memories",
    )
    if any(marker in reason for marker in ambiguity_markers):
        normalized["benchmark_ambiguity"] = True
        normalized["sufficient"] = False
    return normalized


def load_csv_rows(path: str) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_json_dataset(path: str) -> dict[str, dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("data", data) if isinstance(data, dict) else data
    return {str(row.get("question_id", "")): row for row in rows if row.get("question_id")}


def build_stability_map(repeat_runs: list[list[dict[str, str]]]) -> dict[str, dict[str, Any]]:
    run_total = len(repeat_runs)
    sample_map: dict[str, dict[str, Any]] = {}
    for run_index, rows in enumerate(repeat_runs, start=1):
        for row in rows:
            sample_id = row.get("sample_id", "")
            if not sample_id:
                continue
            entry = sample_map.setdefault(
                sample_id,
                {
                    "sample_id": sample_id,
                    "question_type": row.get("question_type", ""),
                    "question": row.get("question", ""),
                    "correct_count": 0,
                    "wrong_count": 0,
                    "other_count": 0,
                    "run_total": run_total,
                },
            )
            result = row.get("result", "").strip().upper()
            entry[f"run{run_index}_result"] = result or "MISSING"
            if result == "CORRECT":
                entry["correct_count"] += 1
            elif result == "WRONG":
                entry["wrong_count"] += 1
            else:
                entry["other_count"] += 1

    for entry in sample_map.values():
        if entry["wrong_count"] == run_total:
            stability = "stable_wrong"
        elif entry["correct_count"] == run_total:
            stability = "stable_correct"
        else:
            stability = "flaky"
        if entry["other_count"]:
            stability = f"{stability}_with_other"
        entry["stability"] = stability
    return sample_map


def stable_wrong_sample_ids(sample_map: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        sample_id
        for sample_id, row in sample_map.items()
        if row.get("stability") == "stable_wrong"
    )


def classify_failure_mode(
    topk_sufficient: bool,
    memory_space_sufficient: bool,
    benchmark_ambiguity: bool,
) -> str:
    if benchmark_ambiguity:
        return "benchmark_or_judge_ambiguity"
    if topk_sufficient:
        return "answer_failure"
    if memory_space_sufficient:
        return "retrieval_miss"
    return "memory_missing"


def evidence_sufficient(row: dict[str, str]) -> bool:
    return (row.get("evidence_sufficient") or "").strip().upper() == "YES"


def parse_json_list_field(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]


def candidate_memory_roots(data_root: Path, user_id: str) -> list[Path]:
    return [
        data_root / "viking" / "default" / "user" / user_id / "memories",
        data_root / "default" / "user" / user_id / "memories",
        data_root / "user" / user_id / "memories",
        data_root / user_id / "memories",
    ]


def find_memory_root(data_root: Path, sample_id: str) -> Path | None:
    user_id = build_sample_user_id(sample_id)
    for root in candidate_memory_roots(data_root, user_id):
        if root.exists() and root.is_dir():
            return root
    return None


def path_to_memory_uri(path: Path, memory_root: Path, sample_id: str) -> str:
    user_id = build_sample_user_id(sample_id)
    rel = path.relative_to(memory_root).as_posix()
    return f"viking://user/{user_id}/memories/{rel}"


def uri_to_local_path(uri: str, data_root: str) -> Path | None:
    if uri.startswith("viking://user/"):
        rest = uri[len("viking://user/") :]
        kind = "user"
    elif uri.startswith("viking://agent/"):
        rest = uri[len("viking://agent/") :]
        kind = "agent"
    else:
        return None

    root = Path(data_root).expanduser()
    candidates = [
        root / "viking" / "default" / kind / rest,
        root / "default" / kind / rest,
        root / kind / rest,
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def read_uri_items(uris: list[str], data_root: str, max_chars_per_file: int) -> list[dict[str, str]]:
    items = []
    for uri in uris:
        path = uri_to_local_path(uri, data_root)
        if not path:
            continue
        content = strip_memory_metadata(path.read_text(encoding="utf-8", errors="replace"))
        if max_chars_per_file > 0:
            content = content[:max_chars_per_file]
        items.append({"uri": uri, "path": str(path), "content": content})
    return items


def collect_memory_items(
    data_root: str,
    sample_id: str,
    max_chars_per_file: int,
    max_memory_files: int,
) -> list[dict[str, str]]:
    memory_root = find_memory_root(Path(data_root).expanduser(), sample_id)
    if memory_root is None:
        return []

    items = []
    for path in sorted(memory_root.rglob("*.md")):
        if not path.is_file():
            continue
        content = strip_memory_metadata(path.read_text(encoding="utf-8", errors="replace"))
        if max_chars_per_file > 0:
            content = content[:max_chars_per_file]
        items.append(
            {
                "uri": path_to_memory_uri(path, memory_root, sample_id),
                "path": str(path),
                "content": content,
            }
        )
        if max_memory_files > 0 and len(items) >= max_memory_files:
            break
    return items


def build_evidence_block(items: list[dict[str, str]]) -> str:
    chunks = []
    for idx, item in enumerate(items, start=1):
        chunks.append(
            f"[{idx}] URI: {item['uri']}\n"
            f"Content:\n{item['content'] if item['content'] else '[EMPTY]'}"
        )
    return "\n\n".join(chunks)


def chunk_items(items: list[dict[str, str]], max_chars_per_batch: int) -> list[list[dict[str, str]]]:
    if max_chars_per_batch <= 0:
        return [items]

    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_chars = 0
    for item in items:
        item_chars = len(item.get("content", "")) + len(item.get("uri", "")) + 64
        if current and current_chars + item_chars > max_chars_per_batch:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        chunks.append(current)
    return chunks


async def judge_memory_items(
    client,
    model: str,
    sample_row: dict[str, str],
    items: list[dict[str, str]],
    timeout: int,
) -> dict[str, Any]:
    prompt = MEMORY_DIAGNOSIS_PROMPT.format(
        sample_id=sample_row.get("sample_id", ""),
        question_type=sample_row.get("question_type", ""),
        question_time=sample_row.get("question_time", ""),
        question=sample_row.get("question", ""),
        answer=sample_row.get("answer", ""),
        response=sample_row.get("response", ""),
        evidence=build_evidence_block(items),
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            timeout=timeout,
        )
        content = resp.choices[0].message.content or ""
        result = normalize_diagnosis_result(parse_json_response(content))
        result["raw_response"] = content
        return result
    except Exception as exc:
        return {
            "sufficient": False,
            "benchmark_ambiguity": False,
            "reason": f"[API ERROR] {exc}",
            "supporting_uris": [],
            "related_uris": [],
            "raw_response": "",
        }


def unique_items_by_uri(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    unique = []
    for item in items:
        uri = item.get("uri", "")
        if uri and uri not in seen:
            unique.append(item)
            seen.add(uri)
    return unique


async def diagnose_memory_space(
    client,
    model: str,
    sample_row: dict[str, str],
    memory_items: list[dict[str, str]],
    max_chars_per_batch: int,
    timeout: int,
) -> dict[str, Any]:
    if not memory_items:
        return {
            "sufficient": False,
            "benchmark_ambiguity": False,
            "reason": "No local memory files found for this sample.",
            "supporting_uris": [],
            "related_uris": [],
        }

    related_uris: list[str] = []
    reasons: list[str] = []
    uri_to_item = {item["uri"]: item for item in memory_items}
    for batch in chunk_items(memory_items, max_chars_per_batch):
        result = await judge_memory_items(client, model, sample_row, batch, timeout)
        reasons.append(result.get("reason", ""))
        related_uris.extend(result.get("related_uris", []))
        related_uris.extend(result.get("supporting_uris", []))
        if result.get("sufficient") or result.get("benchmark_ambiguity"):
            return result

    related_items = unique_items_by_uri(
        [uri_to_item[uri] for uri in related_uris if uri in uri_to_item]
    )
    if related_items:
        result = await judge_memory_items(client, model, sample_row, related_items, timeout)
        result["related_uris"] = [item["uri"] for item in related_items]
        return result

    return {
        "sufficient": False,
        "benchmark_ambiguity": False,
        "reason": "No memory batch contained sufficient or related evidence. " + " | ".join(reasons[:3]),
        "supporting_uris": [],
        "related_uris": [],
    }


def merge_sample_row(
    sample_id: str,
    stability_row: dict[str, Any],
    evidence_row: dict[str, str] | None,
    dataset_row: dict[str, Any] | None,
) -> dict[str, str]:
    source = evidence_row or {}
    dataset_row = dataset_row or {}
    return {
        "sample_id": sample_id,
        "question": source.get("question") or dataset_row.get("question", "") or stability_row.get("question", ""),
        "answer": source.get("answer") or dataset_row.get("answer", ""),
        "question_type": source.get("question_type") or dataset_row.get("question_type", "") or stability_row.get("question_type", ""),
        "question_time": source.get("question_time") or dataset_row.get("question_date", ""),
        "response": source.get("response", ""),
        "result": source.get("result", ""),
        "evidence_sufficient": source.get("evidence_sufficient", ""),
        "evidence_topk": source.get("evidence_topk", ""),
        "evidence_reason": source.get("evidence_reason", ""),
        "evidence_uris": source.get("evidence_uris", ""),
        "evidence_supporting_uris": source.get("evidence_supporting_uris", ""),
    }


async def diagnose_one_sample(
    client,
    model: str,
    sample_row: dict[str, str],
    data_root: str,
    max_chars_per_file: int,
    max_chars_per_batch: int,
    max_memory_files: int,
    timeout: int,
) -> dict[str, Any]:
    topk_sufficient = evidence_sufficient(sample_row)
    if topk_sufficient:
        evidence_uris = parse_json_list_field(sample_row.get("evidence_uris", ""))
        evidence_supporting_uris = parse_json_list_field(
            sample_row.get("evidence_supporting_uris", "")
        )
        topk_items = read_uri_items(
            evidence_uris,
            data_root=data_root,
            max_chars_per_file=max_chars_per_file,
        )
        if topk_items:
            topk_result = await judge_memory_items(
                client=client,
                model=model,
                sample_row=sample_row,
                items=topk_items,
                timeout=timeout,
            )
            topk_result["sufficient"] = True
            if evidence_supporting_uris:
                topk_result["supporting_uris"] = evidence_supporting_uris
            elif not topk_result.get("supporting_uris"):
                topk_result["supporting_uris"] = evidence_uris
            if not topk_result.get("related_uris"):
                topk_result["related_uris"] = evidence_uris
            memory_result = topk_result
        else:
            memory_result = {
                "sufficient": True,
                "benchmark_ambiguity": False,
                "reason": sample_row.get("evidence_reason", "Top-k evidence was already judged sufficient."),
                "supporting_uris": evidence_supporting_uris or evidence_uris,
                "related_uris": evidence_uris,
            }
    else:
        memory_items = collect_memory_items(
            data_root=data_root,
            sample_id=sample_row["sample_id"],
            max_chars_per_file=max_chars_per_file,
            max_memory_files=max_memory_files,
        )
        memory_result = await diagnose_memory_space(
            client=client,
            model=model,
            sample_row=sample_row,
            memory_items=memory_items,
            max_chars_per_batch=max_chars_per_batch,
            timeout=timeout,
        )

    memory_space_sufficient = bool(memory_result.get("sufficient"))
    benchmark_ambiguity = bool(memory_result.get("benchmark_ambiguity"))
    category = classify_failure_mode(
        topk_sufficient=topk_sufficient,
        memory_space_sufficient=memory_space_sufficient,
        benchmark_ambiguity=benchmark_ambiguity,
    )
    supporting_uris = memory_result.get("supporting_uris", []) or []
    related_uris = memory_result.get("related_uris", []) or []
    return {
        "sample_id": sample_row["sample_id"],
        "question_type": sample_row.get("question_type", ""),
        "category": category,
        "question": sample_row.get("question", ""),
        "answer": sample_row.get("answer", ""),
        "response": sample_row.get("response", ""),
        "topk_sufficient": "YES" if topk_sufficient else "NO",
        "memory_space_sufficient": "YES" if memory_space_sufficient else "NO",
        "benchmark_ambiguity": "YES" if benchmark_ambiguity else "NO",
        "evidence_topk": sample_row.get("evidence_topk", ""),
        "supporting_uris": supporting_uris,
        "related_uris": related_uris,
        "reason": memory_result.get("reason", ""),
    }


def render_markdown(rows: list[dict[str, Any]], run_count: int) -> str:
    category_counter = Counter(row["category"] for row in rows)
    type_counter: dict[str, Counter[str]] = {}
    for row in rows:
        question_type = row.get("question_type") or "<missing>"
        type_counter.setdefault(question_type, Counter())[row["category"]] += 1

    lines = [
        "# LongMemEval 稳定错题 Memory 缺失诊断",
        "",
        f"- repeat runs: {run_count}",
        f"- stable_wrong samples: {len(rows)}",
        "",
        "## 分类汇总",
        "",
    ]
    for category in (
        "memory_missing",
        "retrieval_miss",
        "answer_failure",
        "benchmark_or_judge_ambiguity",
    ):
        lines.append(f"- `{category}`: {category_counter.get(category, 0)}")

    lines.extend(["", "## 按 Question Type 汇总", ""])
    lines.append("| question_type | memory_missing | retrieval_miss | answer_failure | benchmark_or_judge_ambiguity | total |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for question_type in sorted(type_counter):
        counter = type_counter[question_type]
        total = sum(counter.values())
        lines.append(
            f"| `{question_type}` | {counter.get('memory_missing', 0)} | "
            f"{counter.get('retrieval_miss', 0)} | {counter.get('answer_failure', 0)} | "
            f"{counter.get('benchmark_or_judge_ambiguity', 0)} | {total} |"
        )

    lines.extend(["", "## 逐题诊断", ""])
    for row in rows:
        supporting_uris = row.get("supporting_uris", []) or []
        related_uris = row.get("related_uris", []) or []
        lines.extend(
            [
                f"### {row['sample_id']} - `{row.get('category', '')}`",
                "",
                f"- question_type: `{row.get('question_type', '')}`",
                f"- topK sufficient: `{row.get('topk_sufficient', '')}`",
                f"- memory space sufficient: `{row.get('memory_space_sufficient', '')}`",
                f"- evidence_topk: `{row.get('evidence_topk', '') or '-'}`",
                f"- benchmark ambiguity: `{row.get('benchmark_ambiguity', '')}`",
                f"- Question: {row.get('question', '')}",
                f"- Answer: {row.get('answer', '')}",
                f"- Model Wrong Response: {row.get('response', '')}",
                f"- Reason: {row.get('reason', '')}",
                f"- Supporting URIs: {', '.join(supporting_uris) if supporting_uris else '-'}",
                f"- Related URIs: {', '.join(related_uris) if related_uris else '-'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def save_csv(path: str, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "sample_id",
        "question_type",
        "category",
        "topk_sufficient",
        "memory_space_sufficient",
        "benchmark_ambiguity",
        "evidence_topk",
        "question",
        "answer",
        "response",
        "reason",
        "supporting_uris",
        "related_uris",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serializable = dict(row)
            serializable["supporting_uris"] = json.dumps(row.get("supporting_uris", []), ensure_ascii=False)
            serializable["related_uris"] = json.dumps(row.get("related_uris", []), ensure_ascii=False)
            writer.writerow({field: serializable.get(field, "") for field in fieldnames})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose stable-wrong LongMemEval samples by memory evidence availability.")
    parser.add_argument("--repeat-inputs", nargs="+", required=True, help="Judged repeat eval CSVs.")
    parser.add_argument("--evidence-input", required=True, help="Evidence judge CSV for one representative run.")
    parser.add_argument("--dataset", required=True, help="Original LongMemEval JSON dataset.")
    parser.add_argument("--data-root", required=True, help="OpenViking data root containing viking/default/user.")
    parser.add_argument("--output-md", required=True, help="Chinese markdown diagnosis output.")
    parser.add_argument("--output-csv", default="", help="Optional detailed CSV output. Defaults to <output-md>.csv.")
    parser.add_argument("--max-chars-per-file", type=int, default=6000)
    parser.add_argument("--max-chars-per-batch", type=int, default=35000)
    parser.add_argument("--max-memory-files", type=int, default=0, help="0 means no file count limit.")
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--base-url", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"))
    parser.add_argument("--provider", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_PROVIDER", "openai"), choices=("openai", "azure"))
    parser.add_argument("--token", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_API_KEY", os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", ""))))
    parser.add_argument("--api-version", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_API_VERSION", DEFAULT_AZURE_API_VERSION))
    parser.add_argument("--model", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_MODEL", "doubao-seed-2-0-pro-260215"))
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    if not args.token:
        raise SystemExit("Error: API token is required")

    repeat_runs = [load_csv_rows(path) for path in args.repeat_inputs]
    stability_map = build_stability_map(repeat_runs)
    stable_wrong_ids = stable_wrong_sample_ids(stability_map)
    evidence_rows = {row.get("sample_id", ""): row for row in load_csv_rows(args.evidence_input)}
    dataset_rows = load_json_dataset(args.dataset)

    client = create_llm_client(
        args.provider,
        base_url=args.base_url,
        token=args.token,
        api_version=args.api_version,
    )
    semaphore = asyncio.Semaphore(args.parallel)
    results: list[dict[str, Any]] = []

    async def worker(sample_id: str) -> None:
        sample_row = merge_sample_row(
            sample_id=sample_id,
            stability_row=stability_map[sample_id],
            evidence_row=evidence_rows.get(sample_id),
            dataset_row=dataset_rows.get(sample_id),
        )
        async with semaphore:
            result = await diagnose_one_sample(
                client=client,
                model=args.model,
                sample_row=sample_row,
                data_root=args.data_root,
                max_chars_per_file=args.max_chars_per_file,
                max_chars_per_batch=args.max_chars_per_batch,
                max_memory_files=args.max_memory_files,
                timeout=args.timeout,
            )
        results.append(result)
        print(
            f"[{len(results)}/{len(stable_wrong_ids)}] {sample_id} "
            f"{result['category']} topK={result['topk_sufficient']} "
            f"memory={result['memory_space_sufficient']}",
            flush=True,
        )

    await asyncio.gather(*(worker(sample_id) for sample_id in stable_wrong_ids))
    results.sort(key=lambda row: row["sample_id"])

    output_md = Path(args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(results, run_count=len(args.repeat_inputs)), encoding="utf-8")

    output_csv = Path(args.output_csv) if args.output_csv else output_md.with_suffix(".csv")
    save_csv(str(output_csv), results)
    print(f"Saved markdown to {output_md}")
    print(f"Saved csv to {output_csv}")
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
