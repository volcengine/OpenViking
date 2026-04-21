import argparse
import asyncio
import csv
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncAzureOpenAI, AsyncOpenAI

env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)


DEFAULT_AZURE_API_VERSION = "2025-01-01-preview"


async def grade_answer(
    llm_client, model: str, question: str, gold_answer: str, response: str
) -> tuple[bool, str]:
    system_prompt = """
        You are an expert grader that determines if answers to questions match a gold standard answer
        """

    accuracy_prompt = f"""
    Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given:
        (1) a question
        (2) a gold answer
        (3) a generated answer

    Grade generously on semantic equivalence, especially for concise factual answers and dates.

    Question: {question}
    Gold answer: {gold_answer}
    Generated answer: {response}

    First, provide a short explanation, then finish with CORRECT or WRONG.
    Respond with JSON only: {{"is_correct": "CORRECT" or "WRONG", "reasoning": "your explanation"}}
    """

    try:
        resp = await llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": accuracy_prompt},
            ],
            temperature=0,
            timeout=60,
        )
        content = resp.choices[0].message.content.strip()
        start_idx = content.find("{")
        end_idx = content.rfind("}")
        if start_idx != -1 and end_idx != -1:
            result = json.loads(content[start_idx : end_idx + 1].strip())
            is_correct = result.get("is_correct", "WRONG").strip().upper() == "CORRECT"
            reasoning = result.get("reasoning", "")
            return is_correct, reasoning
        return False, f"[PARSE ERROR] Invalid response: {content}"
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


def get_ungraded_rows(rows: list[dict], force: bool = False) -> list[int]:
    if force:
        for row in rows:
            row["result"] = ""
            row["reasoning"] = ""
        return list(range(len(rows)))
    return [i for i, row in enumerate(rows) if not row.get("result")]


async def main():
    parser = argparse.ArgumentParser(description="VikingBot LongMemEval judge script")
    parser.add_argument(
        "--input",
        default="./result/longmemeval_qa_result.csv",
        help="Path to QA result csv file",
    )
    parser.add_argument(
        "--base-url",
        default="https://ark.cn-beijing.volces.com/api/v3",
        help="Judge model base URL",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("LONGMEMEVAL_JUDGE_PROVIDER", "openai"),
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
        default=os.getenv("LONGMEMEVAL_JUDGE_API_VERSION"),
        help=f"Azure API version, default: {DEFAULT_AZURE_API_VERSION} for provider=azure",
    )
    parser.add_argument(
        "--model",
        default="doubao-seed-2-0-pro-260215",
        help="Judge model name",
    )
    parser.add_argument(
        "--parallel", type=int, default=5, help="Parallel request count, default: 5"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-grade all rows even if result already exists",
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
            is_correct, reasoning = await grade_answer(
                client, args.model, row["question"], row["answer"], row["response"]
            )
            row["result"] = "CORRECT" if is_correct else "WRONG"
            row["reasoning"] = reasoning
            await save_results()
            print(f"Saved result for {idx + 1}/{total}: {row['result']}")

    await asyncio.gather(*(process_row(idx) for idx in ungraded))


if __name__ == "__main__":
    asyncio.run(main())
