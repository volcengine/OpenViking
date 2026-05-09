import argparse
import asyncio
import csv
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
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


RESPONSE_CORRECTNESS_PROMPT = """You are auditing a LongMemEval judge result.

The benchmark judge marked the model response as WRONG. Decide whether that judge result is itself wrong.

Return ONLY valid JSON:
{{
  "correct": true or false,
  "reason": "short explanation"
}}

Rules:
- Judge semantic correctness, not exact wording.
- If the model response correctly answers the question according to the correct answer/rubric, set correct=true.
- If the model response abstains when the correct answer is answerable, set correct=false.
- If the correct answer is an abstention and the model response also abstains, set correct=true.
- Ignore hidden chain-of-thought tags and judge only the visible final answer meaning.

Question: {question}
Correct answer: {answer}
Question type: {question_type}
Question date: {question_time}
Model response:
{response}
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
        retrieved_uris = item.get("retrieved_uris") or []
        if include_unread and retrieved_uris:
            for uri in retrieved_uris:
                if uri not in seen:
                    uris.append(uri)
                    seen.add(uri)
            continue

        context_uris = item.get("context_uris") or []
        if context_uris:
            for uri in context_uris:
                if uri not in seen:
                    uris.append(uri)
                    seen.add(uri)
            continue

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


def build_sample_user_id(sample_id: str | int) -> str:
    import hashlib

    digest = hashlib.md5(f"user:{sample_id}".encode("utf-8")).hexdigest()[:12]
    return f"lm_user_{digest}"


def candidate_memory_roots(data_root: Path, sample_id: str) -> list[Path]:
    user_id = build_sample_user_id(sample_id)
    return [
        data_root / "viking" / "default" / "user" / user_id / "memories",
        data_root / "default" / "user" / user_id / "memories",
        data_root / "user" / user_id / "memories",
        data_root / user_id / "memories",
    ]


def find_memory_root(data_roots: list[Path], sample_id: str) -> Path | None:
    for data_root in data_roots:
        for root in candidate_memory_roots(data_root, sample_id):
            if root.exists() and root.is_dir():
                return root
    return None


def path_to_memory_uri(path: Path, memory_root: Path, sample_id: str) -> str:
    user_id = build_sample_user_id(sample_id)
    rel = path.relative_to(memory_root).as_posix()
    return f"viking://user/{user_id}/memories/{rel}"


def read_uri_content(uri: str, data_roots: list[Path], max_chars: int) -> tuple[str, str]:
    for root in data_roots:
        path = uri_to_local_path(uri, root)
        if path and path.exists() and path.is_file():
            content = strip_memory_metadata(path.read_text(encoding="utf-8", errors="replace"))
            if max_chars > 0:
                content = content[:max_chars]
            return content, str(path)
    return "", ""


def collect_memory_items(
    data_roots: list[Path],
    sample_id: str,
    max_chars_per_uri: int,
    max_memory_files: int,
) -> list[dict[str, str]]:
    memory_root = find_memory_root(data_roots, sample_id)
    if memory_root is None:
        return []

    items = []
    for path in sorted(memory_root.rglob("*.md")):
        if not path.is_file():
            continue
        content = strip_memory_metadata(path.read_text(encoding="utf-8", errors="replace"))
        if max_chars_per_uri > 0:
            content = content[:max_chars_per_uri]
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


def chunk_evidence_items(
    items: list[dict[str, str]],
    max_chars_per_batch: int,
) -> list[list[dict[str, str]]]:
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
        "sufficient": parse_bool(parsed.get("sufficient", False)),
        "reason": str(parsed.get("reason", "")),
        "supporting_uris": parsed.get("supporting_uris", []) or [],
    }


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "y", "1"}:
            return True
        if normalized in {"false", "no", "n", "0", ""}:
            return False
    if isinstance(value, (int, float)):
        return value != 0
    return False


def parse_correctness_response(content: str) -> dict[str, Any]:
    content = (content or "").strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {"correct": False, "reason": f"[PARSE ERROR] {content}"}
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {"correct": False, "reason": f"[PARSE ERROR] {content}"}
    return {
        "correct": parse_bool(parsed.get("correct", False)),
        "reason": str(parsed.get("reason", "")),
    }


def classify_wrong_answer_attribution(
    response_correct: bool,
    retrieved_sufficient: bool,
    memory_space_sufficient: bool,
) -> str:
    if response_correct:
        return "judge_model_error"
    if retrieved_sufficient:
        return "answer_model_error"
    if memory_space_sufficient:
        return "retrieval_miss"
    return "memory_missing"


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


async def judge_response_correctness(
    client,
    model: str,
    row: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    prompt = RESPONSE_CORRECTNESS_PROMPT.format(
        question=row.get("question", ""),
        answer=row.get("answer", ""),
        question_type=row.get("question_type", ""),
        question_time=row.get("question_time", ""),
        response=row.get("response", ""),
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            timeout=timeout,
        )
        content = resp.choices[0].message.content or ""
        result = parse_correctness_response(content)
        result["raw_response"] = content
        return result
    except Exception as exc:
        return {
            "correct": False,
            "reason": f"[API ERROR] {exc}",
            "raw_response": "",
        }


async def judge_memory_space(
    client,
    model: str,
    row: dict[str, str],
    memory_items: list[dict[str, str]],
    max_chars_per_batch: int,
    timeout: int,
) -> dict[str, Any]:
    if not memory_items:
        return {
            "sufficient": False,
            "reason": "No local memory files found for this sample.",
            "supporting_uris": [],
            "raw_response": "",
        }

    reasons = []
    for batch in chunk_evidence_items(memory_items, max_chars_per_batch):
        result = await judge_prefix(client, model, row, batch, timeout)
        reasons.append(result.get("reason", ""))
        if result.get("sufficient"):
            return result

    return {
        "sufficient": False,
        "reason": "No memory batch contained sufficient evidence. " + " | ".join(reasons[:3]),
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


def safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def load_stability_map(paths: list[str]) -> dict[str, dict[str, Any]]:
    if not paths:
        return {}

    expanded_paths: list[str] = []
    seen_paths = set()
    for path in paths:
        matches = glob.glob(path)
        for matched_path in matches or [path]:
            if matched_path not in seen_paths:
                expanded_paths.append(matched_path)
                seen_paths.add(matched_path)

    sample_map: dict[str, dict[str, Any]] = {}
    run_total = len(expanded_paths)
    for path in expanded_paths:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sample_id = row.get("sample_id", "")
                if not sample_id:
                    continue
                entry = sample_map.setdefault(
                    sample_id,
                    {
                        "correct_count": 0,
                        "wrong_count": 0,
                        "other_count": 0,
                        "run_total": run_total,
                    },
                )
                result = (row.get("result") or "").strip().upper()
                if result == "CORRECT":
                    entry["correct_count"] += 1
                elif result == "WRONG":
                    entry["wrong_count"] += 1
                else:
                    entry["other_count"] += 1

    for entry in sample_map.values():
        if entry["correct_count"] == run_total:
            stability = "stable_correct"
        elif entry["wrong_count"] == run_total:
            stability = "stable_wrong"
        else:
            stability = "flaky"
        if entry["other_count"] > 0:
            stability = f"{stability}_with_other"
        entry["stability"] = stability
    return sample_map


def format_pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.00%"
    return f"{numerator / denominator:.2%}"


def summarize_evidence(
    rows: list[dict[str, str]],
    stability_map: dict[str, dict[str, Any]] | None = None,
) -> str:
    stability_map = stability_map or {}
    total = len(rows)
    sufficient_rows = [
        row for row in rows if (row.get("evidence_sufficient") or "").strip().upper() == "YES"
    ]
    topks = [safe_int(row.get("evidence_topk")) for row in sufficient_rows]
    topks = [topk for topk in topks if topk is not None]

    lines: list[str] = []
    lines.append("=== Evidence Summary ===")
    lines.append(f"Samples: {total}")
    lines.append(f"Evidence sufficient: {len(sufficient_rows)} ({format_pct(len(sufficient_rows), total)})")

    lines.append("")
    lines.append("=== Top-K Coverage ===")
    for threshold in (1, 3, 5, 10, 20):
        covered = sum(1 for topk in topks if topk <= threshold)
        lines.append(
            f"top{threshold}: {covered}/{total} ({format_pct(covered, total)}), "
            f"among sufficient: {covered}/{len(sufficient_rows)} "
            f"({format_pct(covered, len(sufficient_rows))})"
        )
    missing = total - len(sufficient_rows)
    lines.append(f"not covered within judged top-k: {missing}/{total} ({format_pct(missing, total)})")

    lines.append("")
    lines.append("=== Top-K Distribution By Question Type ===")
    buckets = [
        ("top1", lambda k: k == 1),
        ("top2-3", lambda k: k is not None and 2 <= k <= 3),
        ("top4-5", lambda k: k is not None and 4 <= k <= 5),
        ("top6-10", lambda k: k is not None and 6 <= k <= 10),
        ("top11-20", lambda k: k is not None and 11 <= k <= 20),
        ("not_sufficient", lambda k: k is None),
    ]
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    type_totals: Counter[str] = Counter()
    for row in rows:
        question_type = row.get("question_type") or "<missing>"
        type_totals[question_type] += 1
        topk = safe_int(row.get("evidence_topk"))
        if (row.get("evidence_sufficient") or "").strip().upper() != "YES":
            topk = None
        for name, predicate in buckets:
            if predicate(topk):
                by_type[question_type][name] += 1
                break

    header = f"{'question_type':<28} {'n':>5} {'top1':>6} {'top2-3':>8} {'top4-5':>8} {'top6-10':>9} {'top11-20':>10} {'not_suff':>10}"
    lines.append(header)
    for question_type in sorted(type_totals):
        counter = by_type[question_type]
        lines.append(
            f"{question_type:<28} {type_totals[question_type]:>5} "
            f"{counter.get('top1', 0):>6} "
            f"{counter.get('top2-3', 0):>8} "
            f"{counter.get('top4-5', 0):>8} "
            f"{counter.get('top6-10', 0):>9} "
            f"{counter.get('top11-20', 0):>10} "
            f"{counter.get('not_sufficient', 0):>10}"
        )

    wrong_rows = [row for row in rows if (row.get("result") or "").strip().upper() == "WRONG"]
    if wrong_rows:
        wrong_yes = sum(
            1 for row in wrong_rows if (row.get("evidence_sufficient") or "").strip().upper() == "YES"
        )
        wrong_no = len(wrong_rows) - wrong_yes
        lines.append("")
        lines.append("=== Wrong Rows Evidence Sufficiency ===")
        lines.append(f"wrong total: {len(wrong_rows)}")
        lines.append(f"wrong + sufficient YES: {wrong_yes}/{len(wrong_rows)} ({format_pct(wrong_yes, len(wrong_rows))})")
        lines.append(f"wrong + sufficient NO: {wrong_no}/{len(wrong_rows)} ({format_pct(wrong_no, len(wrong_rows))})")

    if stability_map:
        lines.append("")
        lines.append("=== Evidence Top-K By Stability ===")
        stability_totals: Counter[str] = Counter()
        stability_buckets: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            sample_id = row.get("sample_id", "")
            stability = stability_map.get(sample_id, {}).get("stability", "missing_stability")
            stability_totals[stability] += 1
            topk = safe_int(row.get("evidence_topk"))
            if (row.get("evidence_sufficient") or "").strip().upper() != "YES":
                topk = None
            for name, predicate in buckets:
                if predicate(topk):
                    stability_buckets[stability][name] += 1
                    break

        lines.append(header.replace("question_type", "stability"))
        for stability in sorted(stability_totals):
            counter = stability_buckets[stability]
            lines.append(
                f"{stability:<28} {stability_totals[stability]:>5} "
                f"{counter.get('top1', 0):>6} "
                f"{counter.get('top2-3', 0):>8} "
                f"{counter.get('top4-5', 0):>8} "
                f"{counter.get('top6-10', 0):>9} "
                f"{counter.get('top11-20', 0):>10} "
                f"{counter.get('not_sufficient', 0):>10}"
            )
    return "\n".join(lines)


def summarize_attribution(rows: list[dict[str, str]]) -> str:
    wrong_rows = [row for row in rows if (row.get("result") or "").strip().upper() == "WRONG"]
    attributed_rows = [row for row in wrong_rows if row.get("attribution_category")]
    category_counter = Counter(row.get("attribution_category", "") for row in attributed_rows)
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    for row in attributed_rows:
        by_type[row.get("question_type") or "<missing>"][row.get("attribution_category", "")] += 1

    lines = [
        "=== Wrong Answer Attribution Summary ===",
        f"wrong rows: {len(wrong_rows)}",
        f"attributed rows: {len(attributed_rows)} ({format_pct(len(attributed_rows), len(wrong_rows))})",
        "",
        "=== Attribution Categories ===",
    ]
    for category in (
        "memory_missing",
        "retrieval_miss",
        "answer_model_error",
        "judge_model_error",
    ):
        count = category_counter.get(category, 0)
        lines.append(f"{category}: {count}/{len(attributed_rows)} ({format_pct(count, len(attributed_rows))})")

    if by_type:
        lines.extend(["", "=== Attribution By Question Type ==="])
        header = (
            f"{'question_type':<28} {'n':>5} {'memory_missing':>15} "
            f"{'retrieval_miss':>15} {'answer_error':>15} {'judge_error':>15}"
        )
        lines.append(header)
        for question_type in sorted(by_type):
            counter = by_type[question_type]
            total = sum(counter.values())
            lines.append(
                f"{question_type:<28} {total:>5} "
                f"{counter.get('memory_missing', 0):>15} "
                f"{counter.get('retrieval_miss', 0):>15} "
                f"{counter.get('answer_model_error', 0):>15} "
                f"{counter.get('judge_model_error', 0):>15}"
            )
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(
        description="Judge whether retrieved URI contents contain sufficient evidence and report minimal top-k."
    )
    parser.add_argument("--input", required=True, help="Eval CSV with retrieved_uris_by_iteration")
    parser.add_argument("--output", required=True, help="Output CSV")
    parser.add_argument("--data-root", default=None, help="OpenViking data root, e.g. ~/.openviking/20260428-data")
    parser.add_argument("--max-topk", type=int, default=30, help="Max retrieved top-k prefix to judge")
    parser.add_argument("--max-chars-per-uri", type=int, default=4000)
    parser.add_argument("--max-chars-per-batch", type=int, default=35000)
    parser.add_argument("--max-memory-files", type=int, default=0, help="0 means no file count limit")
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--include-unread", action="store_true", help="Also judge search hits that were not read")
    parser.add_argument(
        "--attribution",
        action="store_true",
        help=(
            "Attribute WRONG rows into memory_missing, retrieval_miss, "
            "answer_model_error, or judge_model_error."
        ),
    )
    parser.add_argument("--force", action="store_true", help="Rejudge rows with existing evidence result")
    parser.add_argument("--base-url", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"))
    parser.add_argument("--provider", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_PROVIDER", "openai"), choices=("openai", "azure"))
    parser.add_argument("--token", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_API_KEY", os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", ""))))
    parser.add_argument("--api-version", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_API_VERSION", DEFAULT_AZURE_API_VERSION))
    parser.add_argument("--model", default=os.getenv("LONGMEMEVAL_EVIDENCE_JUDGE_MODEL", "doubao-seed-2-0-pro-260215"))
    parser.add_argument(
        "--summary-output",
        default=None,
        help="Optional path for the text summary. Defaults to <output>.summary.txt.",
    )
    parser.add_argument(
        "--stability-inputs",
        nargs="*",
        default=[],
        help=(
            "Optional repeat-eval CSVs, or quoted glob patterns, used to compare "
            "stable-correct, stable-wrong, and flaky samples."
        ),
    )
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
        "attribution_category",
        "attribution_reason",
        "response_correct",
        "response_correct_reason",
        "response_correct_raw_response",
        "memory_space_sufficient",
        "memory_space_supporting_uris",
        "memory_space_reason",
        "memory_space_raw_response",
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
        is_wrong = (row.get("result") or "").strip().upper() == "WRONG"
        if args.attribution and not is_wrong:
            return
        if args.attribution:
            if row.get("attribution_category") and not args.force:
                return
        elif row.get("evidence_sufficient") and not args.force:
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
        response_result = {
            "correct": False,
            "reason": "",
            "raw_response": "",
        }
        memory_result = {
            "sufficient": False,
            "reason": "",
            "supporting_uris": [],
            "raw_response": "",
        }
        checked_k = 0
        topk = ""
        async with semaphore:
            if args.attribution:
                response_result = await judge_response_correctness(
                    client,
                    args.model,
                    row,
                    args.timeout,
                )

            if not args.attribution or not response_result.get("correct"):
                existing_evidence = (row.get("evidence_sufficient") or "").strip().upper()
                if args.attribution and existing_evidence in {"YES", "NO"} and not args.force:
                    checked_k = safe_int(row.get("evidence_checked_k")) or 0
                    topk = row.get("evidence_topk", "")
                    try:
                        supporting_uris = json.loads(row.get("evidence_supporting_uris") or "[]")
                    except json.JSONDecodeError:
                        supporting_uris = []
                    final_result = {
                        "sufficient": existing_evidence == "YES",
                        "reason": row.get("evidence_reason", ""),
                        "supporting_uris": supporting_uris,
                        "raw_response": row.get("evidence_raw_response", ""),
                    }
                else:
                    for k in range(1, len(evidence_items) + 1):
                        checked_k = k
                        result = await judge_prefix(client, args.model, row, evidence_items[:k], args.timeout)
                        final_result = result
                        if result.get("sufficient"):
                            topk = str(k)
                            break

            if args.attribution and not response_result.get("correct") and not final_result.get("sufficient"):
                memory_items = collect_memory_items(
                    data_roots=data_roots,
                    sample_id=row.get("sample_id", ""),
                    max_chars_per_uri=args.max_chars_per_uri,
                    max_memory_files=args.max_memory_files,
                )
                memory_result = await judge_memory_space(
                    client=client,
                    model=args.model,
                    row=row,
                    memory_items=memory_items,
                    max_chars_per_batch=args.max_chars_per_batch,
                    timeout=args.timeout,
                )

        response_correct = bool(response_result.get("correct"))
        if args.attribution and response_correct:
            row["evidence_sufficient"] = ""
            row["evidence_topk"] = ""
            row["evidence_checked_k"] = "0"
            row["evidence_uris"] = "[]"
            row["evidence_supporting_uris"] = "[]"
            row["evidence_reason"] = "Skipped because the model response was judged correct."
            row["evidence_raw_response"] = ""
        else:
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

        if args.attribution:
            retrieved_sufficient = bool(final_result.get("sufficient"))
            memory_space_sufficient = bool(memory_result.get("sufficient"))
            category = classify_wrong_answer_attribution(
                response_correct=response_correct,
                retrieved_sufficient=retrieved_sufficient,
                memory_space_sufficient=memory_space_sufficient,
            )
            row["attribution_category"] = category
            row["response_correct"] = "YES" if response_correct else "NO"
            row["response_correct_reason"] = response_result.get("reason", "")
            row["response_correct_raw_response"] = response_result.get("raw_response", "")
            if category == "judge_model_error":
                row["memory_space_sufficient"] = ""
                row["memory_space_supporting_uris"] = "[]"
                row["memory_space_reason"] = "Skipped because the model response was judged correct."
                row["memory_space_raw_response"] = ""
                row["attribution_reason"] = row["response_correct_reason"]
            elif category == "answer_model_error":
                row["memory_space_sufficient"] = ""
                row["memory_space_supporting_uris"] = "[]"
                row["memory_space_reason"] = "Skipped because retrieved evidence was sufficient."
                row["memory_space_raw_response"] = ""
                row["attribution_reason"] = row["evidence_reason"]
            elif category == "retrieval_miss":
                row["memory_space_sufficient"] = "YES"
                row["memory_space_supporting_uris"] = json.dumps(
                    memory_result.get("supporting_uris", []), ensure_ascii=False
                )
                row["memory_space_reason"] = memory_result.get("reason", "")
                row["memory_space_raw_response"] = memory_result.get("raw_response", "")
                row["attribution_reason"] = row["memory_space_reason"]
            else:
                row["memory_space_sufficient"] = "NO"
                row["memory_space_supporting_uris"] = json.dumps(
                    memory_result.get("supporting_uris", []), ensure_ascii=False
                )
                row["memory_space_reason"] = memory_result.get("reason", "")
                row["memory_space_raw_response"] = memory_result.get("raw_response", "")
                row["attribution_reason"] = row["memory_space_reason"] or row["evidence_reason"]

        await persist()
        if args.attribution:
            print(
                f"[{idx + 1}/{len(rows)}] {row.get('sample_id','')} "
                f"category={row['attribution_category']} "
                f"retrieved={row['evidence_sufficient']} "
                f"memory={row['memory_space_sufficient'] or '-'}"
            )
        else:
            print(
                f"[{idx + 1}/{len(rows)}] {row.get('sample_id','')} "
                f"sufficient={row['evidence_sufficient']} topk={row['evidence_topk'] or '-'}"
            )

    await persist()
    await asyncio.gather(*(process_row(i) for i in range(len(rows))))

    stability_map = load_stability_map(args.stability_inputs)
    summary = summarize_attribution(rows) if args.attribution else summarize_evidence(rows, stability_map)
    print("\n" + summary)

    summary_path = Path(args.summary_output) if args.summary_output else Path(output_path).with_suffix(".summary.txt")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary + "\n", encoding="utf-8")
    print(f"\nSaved evidence summary to {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
