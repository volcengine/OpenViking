import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncAzureOpenAI, AsyncOpenAI

try:
    from benchmark.locomo.openviking.locomo_prompts import (
        JUDGE_SYSTEM_PROMPT,
        get_judge_prompt,
        get_judge_prompt_with_evidence,
        get_strict_judge_prompt,
        get_strict_judge_prompt_with_evidence,
        preprocess_answer,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from locomo_prompts import (  # type: ignore
        JUDGE_SYSTEM_PROMPT,
        get_judge_prompt,
        get_judge_prompt_with_evidence,
        get_strict_judge_prompt,
        get_strict_judge_prompt_with_evidence,
        preprocess_answer,
    )

env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)
csv.field_size_limit(sys.maxsize)

DEFAULT_AZURE_API_VERSION = "2025-01-01-preview"


def parse_judge_result(content: str) -> tuple[bool | None, str]:
    stripped = content.strip()
    if not stripped:
        return None, "[PARSE ERROR] Empty response"

    start_idx = stripped.find("{")
    end_idx = stripped.rfind("}")
    if start_idx != -1 and end_idx != -1:
        try:
            result = json.loads(stripped[start_idx : end_idx + 1].strip())
            label = str(result.get("label", "")).strip().upper()
            reasoning = str(result.get("reasoning", "")).strip()
            if label == "CORRECT":
                return True, reasoning or stripped
            if label == "WRONG":
                return False, reasoning or stripped
        except json.JSONDecodeError:
            pass
    return None, f"[PARSE ERROR] Invalid response: {stripped}"


def _parse_evidence_text(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(parsed, list):
        return "\n".join(str(item) for item in parsed if str(item).strip())
    if isinstance(parsed, str):
        return parsed
    return ""


async def grade_answer(
    llm_client,
    model: str,
    category: int,
    question: str,
    gold_answer: str,
    response: str,
    evidence_text: str = "",
    temperature: float | None = None,
    strict_prompt: bool = False,
) -> tuple[bool, str]:
    processed_answer = preprocess_answer(category, gold_answer)
    if strict_prompt and evidence_text:
        accuracy_prompt = get_strict_judge_prompt_with_evidence(
            category,
            question,
            processed_answer,
            response,
            evidence_text,
        )
    elif strict_prompt:
        accuracy_prompt = get_strict_judge_prompt(
            category,
            question,
            processed_answer,
            response,
        )
    elif evidence_text:
        accuracy_prompt = get_judge_prompt_with_evidence(
            category,
            question,
            processed_answer,
            response,
            evidence_text,
        )
    else:
        accuracy_prompt = get_judge_prompt(
            category,
            question,
            processed_answer,
            response,
        )

    try:
        request_kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": accuracy_prompt},
            ],
            "timeout": 60,
        }
        if temperature is not None:
            request_kwargs["temperature"] = temperature

        resp = await llm_client.chat.completions.create(**request_kwargs)
        content = resp.choices[0].message.content.strip()
        verdict, reasoning = parse_judge_result(content)
        if verdict is not None:
            return verdict, reasoning
        return False, reasoning
    except Exception as e:
        return False, f"[API ERROR] {str(e)}"


def load_answers(input_path: str) -> tuple[list[dict], list[str]]:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames.copy()
        if "reasoning" not in fieldnames:
            fieldnames.append("reasoning")
        rows = list(reader)
    return rows, fieldnames


def get_ungraded_rows(rows: list[dict], force: bool = False) -> list[int]:
    """Return row indexes to judge, excluding adversarial rows without normal answers."""
    indexes = []
    for i, row in enumerate(rows):
        if str(row.get("category", "")).strip() == "5":
            continue
        if force:
            row["result"] = ""
            row["reasoning"] = ""
            indexes.append(i)
        elif not row.get("result"):
            indexes.append(i)
    return indexes


def create_llm_client(
    provider: str,
    *,
    base_url: str,
    token: str,
    api_version: str | None = None,
):
    if provider == "azure":
        return AsyncAzureOpenAI(
            api_key=token,
            azure_endpoint=base_url,
            api_version=api_version or DEFAULT_AZURE_API_VERSION,
        )
    return AsyncOpenAI(base_url=base_url, api_key=token)


async def main():
    parser = argparse.ArgumentParser(
        description="VikingBot QA judge script, same logic as openclaw evaluation"
    )
    parser.add_argument(
        "--input",
        default="./result/locomo_qa_result_only_sys_memory.csv",
        help="Path to QA result csv file, default: ./result/locomo_qa_result.csv",
    )
    parser.add_argument(
        "--base-url",
        default="https://ark.cn-beijing.volces.com/api/v3",
        help="Judge model base URL",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("LOCOMO_JUDGE_PROVIDER", "openai"),
        choices=("openai", "azure"),
        help="Judge provider type, default: openai",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        help="Judge API token",
    )
    parser.add_argument(
        "--api-version",
        default=os.getenv("LOCOMO_JUDGE_API_VERSION"),
        help=f"Azure API version, default: {DEFAULT_AZURE_API_VERSION} for provider=azure",
    )
    parser.add_argument(
        "--model",
        default="doubao-seed-2-0-pro-260215",
        help="Judge model name",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional judge temperature. Omitted by default for models that only support their default value.",
    )
    parser.add_argument(
        "--parallel", type=int, default=5, help="Parallel request count, default: 5"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-grade all non-adversarial rows even if result already exists",
    )
    parser.add_argument(
        "--strict-prompt",
        action="store_true",
        help="Use the strict LoCoMo judge prompt instead of the default lenient prompt",
    )
    args = parser.parse_args()

    if not args.token:
        print("Error: API token is required")
        raise SystemExit(1)

    rows, fieldnames = load_answers(args.input)
    total = len(rows)
    ungraded = get_ungraded_rows(rows, force=args.force)
    print(f"Total answers: {total}, ungraded: {len(ungraded)}")

    if not ungraded:
        print("All answers already graded, exit")
        return

    client = create_llm_client(
        args.provider,
        base_url=args.base_url,
        token=args.token,
        api_version=args.api_version,
    )
    semaphore = asyncio.Semaphore(args.parallel)
    file_lock = asyncio.Lock()

    async def save_results():
        async with file_lock:
            temp_file = f"{args.input}.tmp"
            with open(temp_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temp_file, args.input)

    async def process_row(idx):
        async with semaphore:
            row = rows[idx]
            question = row["question"]
            gold = row["answer"]
            response = row["response"]
            category = int(str(row.get("category", "0") or "0"))
            evidence_text = _parse_evidence_text(row.get("evidence_text", ""))
            print(f"Grading {idx + 1}/{total}: {question[:60]}...")
            is_correct, reasoning = await grade_answer(
                client,
                args.model,
                category,
                question,
                gold,
                response,
                evidence_text,
                temperature=args.temperature,
                strict_prompt=args.strict_prompt,
            )
            row["result"] = "CORRECT" if is_correct else "WRONG"
            row["reasoning"] = reasoning

            await save_results()
            print(f"Saved result for {idx + 1}/{total}: {row['result']}")

            return idx, row

    tasks = [process_row(idx) for idx in ungraded]
    await asyncio.gather(*tasks)

    correct = sum(1 for row in rows if row.get("result") == "CORRECT")
    total_graded = sum(1 for row in rows if row.get("result"))
    accuracy = correct / total_graded if total_graded > 0 else 0.0
    print(f"\nGrading completed: {correct}/{total_graded} correct, accuracy: {accuracy:.2%}")
    print(f"All results saved to {args.input}")


if __name__ == "__main__":
    asyncio.run(main())
