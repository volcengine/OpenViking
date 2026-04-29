import argparse
import asyncio
import csv
import json
import os
import re
import sys
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


EVIDENCE_JUDGE_PROMPT = """You are judging retrieval evidence for a LongMemEval question.

Decide whether the provided retrieved memory contents contain enough evidence to answer the question with the given correct answer.

Important:
- Judge ONLY whether the evidence is sufficient, not whether a model response is good.
- The evidence may require arithmetic, counting, date comparison, or combining multiple memories.
- If the correct answer says the information is not enough, evidence is sufficient only when it supports that abstention.
- Extra irrelevant evidence is allowed unless it contradicts the needed answer.
- Be strict about entity identity, dates, variants, and user-vs-assistant facts.

Return ONLY valid JSON:
{{
  "sufficient": true or false,
  "reason": "short explanation",
  "supporting_uris": ["uri1", "uri2"]
}}

Question: {question}
Correct answer: {answer}
Question type: {question_type}
Question date: {question_time}

Retrieved evidence:
{evidence}
"""


def parse_retrieved_iterations(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def get_ranked_uris(row: dict[str, str], include_unread: bool) -> list[str]:
    iterations = parse_retrieved_iterations(row.get("retrieved_uris_by_iteration", ""))
    if not iterations:
        return []

    uris: list[str] = []
    seen = set()
    for item in iterations:
        search_uris = item.get("search_result_uris") or []
        read_uris = (
            item.get("read_success_uris")
            or item.get("read_uris")
            or item.get("attempted_read_uris")
            or []
        )
        allowed = set(search_uris if include_unread else read_uris)
        for uri in search_uris:
            if uri in allowed and uri not in seen:
                uris.append(uri)
                seen.add(uri)
        for uri in read_uris:
            if uri not in seen:
                uris.append(uri)
                seen.add(uri)
    return uris


def strip_memory_metadata(content: str) -> str:
    return MEMORY_FIELDS_RE.sub("", content or "").strip()


def candidate_data_roots(explicit: str | None) -> list[Path]:
    roots: list[Path] = []
    if explicit:
        roots.append(Path(explicit).expanduser())
    env_root = os.getenv("OPENVIKING_DATA_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.append(Path.home() / ".openviking" / "data")
    roots.extend(sorted((Path.home() / ".openviking").glob("*data")))

    deduped: list[Path] = []
    seen = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            deduped.append(root)
            seen.add(key)
    return deduped


def uri_to_local_path(uri: str, data_root: Path) -> Path | None:
    if uri.startswith("viking://user/"):
        rest = uri[len("viking://user/") :]
        kind = "user"
    elif uri.startswith("viking://agent/"):
        rest = uri[len("viking://agent/") :]
        kind = "agent"
    else:
        return None

    candidates = [
        data_root / "viking" / "default" / kind / rest,
        data_root / "default" / kind / rest,
        data_root / kind / rest,
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def read_uri_content(uri: str, data_roots: list[Path], max_chars: int) -> tuple[str, str]:
    for root in data_roots:
        path = uri_to_local_path(uri, root)
        if path and path.exists() and path.is_file():
            content = strip_memory_metadata(path.read_text(encoding="utf-8", errors="replace"))
            if max_chars > 0:
                content = content[:max_chars]
            return content, str(path)
    return "", ""


def build_evidence_block(items: list[dict[str, str]]) -> str:
    chunks = []
    for idx, item in enumerate(items, start=1):
        chunks.append(
            f"[{idx}] URI: {item['uri']}\n"
            f"Content:\n{item['content'] if item['content'] else '[READ_FAILED_OR_EMPTY]'}"
        )
    return "\n\n".join(chunks)


def parse_json_response(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {"sufficient": False, "reason": f"[PARSE ERROR] {content}", "supporting_uris": []}
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {"sufficient": False, "reason": f"[PARSE ERROR] {content}", "supporting_uris": []}
    return {
        "sufficient": bool(parsed.get("sufficient", False)),
        "reason": str(parsed.get("reason", "")),
        "supporting_uris": parsed.get("supporting_uris", []) or [],
    }


async def judge_prefix(
    client,
    model: str,
    row: dict[str, str],
    evidence_items: list[dict[str, str]],
    timeout: int,
) -> dict[str, Any]:
    prompt = EVIDENCE_JUDGE_PROMPT.format(
        question=row.get("question", ""),
        answer=row.get("answer", ""),
        question_type=row.get("question_type", ""),
        question_time=row.get("question_time", ""),
        evidence=build_evidence_block(evidence_items),
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            timeout=timeout,
        )
        content = resp.choices[0].message.content or ""
        result = parse_json_response(content)
        result["raw_response"] = content
        return result
    except Exception as exc:
        return {
            "sufficient": False,
            "reason": f"[API ERROR] {exc}",
            "supporting_uris": [],
            "raw_response": "",
        }


def load_rows(path: str) -> tuple[list[dict[str, str]], list[str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, fieldnames


def save_rows(path: str, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


async def main():
    parser = argparse.ArgumentParser(
        description="Judge whether retrieved URI contents contain sufficient evidence and report minimal top-k."
    )
    parser.add_argument("--input", required=True, help="Eval CSV with retrieved_uris_by_iteration")
    parser.add_argument("--output", required=True, help="Output CSV")
    parser.add_argument("--data-root", default=None, help="OpenViking data root, e.g. ~/.openviking/20260428-data")
    parser.add_argument("--max-topk", type=int, default=30, help="Max retrieved top-k prefix to judge")
    parser.add_argument("--max-chars-per-uri", type=int, default=4000)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--include-unread", action="store_true", help="Also judge search hits that were not read")
    parser.add_argument("--force", action="store_true", help="Rejudge rows with existing evidence result")
    parser.add_argument("--base-url", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"))
    parser.add_argument("--provider", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_PROVIDER", "openai"), choices=("openai", "azure"))
    parser.add_argument("--token", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_API_KEY", os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", ""))))
    parser.add_argument("--api-version", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_API_VERSION", DEFAULT_AZURE_API_VERSION))
    parser.add_argument("--model", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_MODEL", "doubao-seed-2-0-pro-260215"))
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("Error: API token is required")

    rows, base_fieldnames = load_rows(args.input)
    output_fields = [
        "evidence_sufficient",
        "evidence_topk",
        "evidence_checked_k",
        "evidence_uris",
        "evidence_supporting_uris",
        "evidence_reason",
        "evidence_raw_response",
    ]
    fieldnames = list(base_fieldnames)
    for field in output_fields:
        if field not in fieldnames:
            fieldnames.append(field)

    data_roots = candidate_data_roots(args.data_root)
    client = create_llm_client(
        args.provider,
        base_url=args.base_url,
        token=args.token,
        api_version=args.api_version,
    )
    semaphore = asyncio.Semaphore(args.parallel)
    save_lock = asyncio.Lock()
    output_path = args.output

    async def persist():
        async with save_lock:
            save_rows(output_path, rows, fieldnames)

    async def process_row(idx: int):
        row = rows[idx]
        if row.get("evidence_sufficient") and not args.force:
            return

        ranked_uris = get_ranked_uris(row, include_unread=args.include_unread)[: args.max_topk]
        evidence_items = []
        for uri in ranked_uris:
            content, path = read_uri_content(uri, data_roots, args.max_chars_per_uri)
            evidence_items.append({"uri": uri, "path": path, "content": content})

        final_result = {
            "sufficient": False,
            "reason": "No retrieved URI content was available",
            "supporting_uris": [],
            "raw_response": "",
        }
        checked_k = 0
        topk = ""
        async with semaphore:
            for k in range(1, len(evidence_items) + 1):
                checked_k = k
                result = await judge_prefix(client, args.model, row, evidence_items[:k], args.timeout)
                final_result = result
                if result.get("sufficient"):
                    topk = str(k)
                    break

        row["evidence_sufficient"] = "YES" if final_result.get("sufficient") else "NO"
        row["evidence_topk"] = topk
        row["evidence_checked_k"] = str(checked_k)
        row["evidence_uris"] = json.dumps(
            [item["uri"] for item in evidence_items[: int(topk or checked_k or 0)]],
            ensure_ascii=False,
        )
        row["evidence_supporting_uris"] = json.dumps(
            final_result.get("supporting_uris", []), ensure_ascii=False
        )
        row["evidence_reason"] = final_result.get("reason", "")
        row["evidence_raw_response"] = final_result.get("raw_response", "")
        await persist()
        print(
            f"[{idx + 1}/{len(rows)}] {row.get('sample_id','')} "
            f"sufficient={row['evidence_sufficient']} topk={row['evidence_topk'] or '-'}"
        )

    await persist()
    await asyncio.gather(*(process_row(i) for i in range(len(rows))))


if __name__ == "__main__":
    asyncio.run(main())
