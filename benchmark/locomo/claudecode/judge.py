"""
LLM judge for Claude Code LoCoMo QA results.

Reuses the same grading logic as openclaw/vikingbot benchmarks.

Usage:
    python judge.py --input ./result/qa_results.csv
    python judge.py --input ./result/qa_results.csv --parallel 20
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)


async def grade_answer(
    llm_client, model: str, question: str, gold_answer: str, response: str
) -> tuple[bool, str]:
    system_prompt = (
        "You are an expert grader that determines if answers to questions "
        "match a gold standard answer"
    )

    accuracy_prompt = f"""Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {response}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Respond with JSON only: {{"is_correct": "CORRECT" or "WRONG", "reasoning": "your explanation"}}"""

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
            result = json.loads(content[start_idx : end_idx + 1])
            is_correct = result.get("is_correct", "WRONG").strip().upper() == "CORRECT"
            reasoning = result.get("reasoning", "")
            return is_correct, reasoning
        return False, f"[PARSE ERROR] {content}"
    except Exception as e:
        return False, f"[API ERROR] {e}"


async def main():
    parser = argparse.ArgumentParser(description="LLM judge for Claude Code QA results")
    parser.add_argument(
        "--input",
        default="./result/qa_results.csv",
        help="Path to QA result CSV (default: ./result/qa_results.csv)",
    )
    parser.add_argument(
        "--base-url",
        default="https://ark.cn-beijing.volces.com/api/v3",
        help="Judge API base URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        help="Judge API token (or ARK_API_KEY / OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--model",
        default="doubao-seed-2-0-pro-260215",
        help="Judge model name",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        help="Parallel grading requests (default: 5)",
    )
    args = parser.parse_args()

    if not args.token:
        print("Error: API token required.", file=sys.stderr)
        print(
            "  Set ARK_API_KEY env var, or use --token, or create ~/.openviking_benchmark_env",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.exists(args.input):
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        if "reasoning" not in fieldnames:
            fieldnames.append("reasoning")
        rows = list(reader)

    # Find ungraded rows (skip category=5)
    ungraded = [
        i for i, row in enumerate(rows) if row.get("category", "") != "5" and not row.get("result")
    ]
    total = len(rows)
    print(f"Total: {total}, ungraded: {len(ungraded)}", file=sys.stderr)

    if not ungraded:
        print("All valid answers already graded.", file=sys.stderr)
        return

    client = AsyncOpenAI(base_url=args.base_url, api_key=args.token)
    semaphore = asyncio.Semaphore(args.parallel)
    file_lock = asyncio.Lock()

    async def save_results():
        async with file_lock:
            tmp = f"{args.input}.tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp, args.input)

    async def process_row(idx):
        async with semaphore:
            row = rows[idx]
            question = row.get("question", "")
            gold = row.get("answer", "")
            response = row.get("response", "")
            print(f"Grading {idx + 1}/{total}: {question[:60]}...", file=sys.stderr)

            is_correct, reasoning = await grade_answer(client, args.model, question, gold, response)
            row["result"] = "CORRECT" if is_correct else "WRONG"
            row["reasoning"] = reasoning

            await save_results()
            print(f"  -> {row['result']}", file=sys.stderr)

    tasks = [process_row(idx) for idx in ungraded]
    await asyncio.gather(*tasks)

    correct = sum(
        1 for row in rows if row.get("category", "") != "5" and row.get("result") == "CORRECT"
    )
    graded = sum(
        1
        for row in rows
        if row.get("category", "") != "5" and row.get("result") in ("CORRECT", "WRONG")
    )
    accuracy = correct / graded if graded > 0 else 0.0
    print(f"\nAccuracy: {correct}/{graded} = {accuracy:.2%}", file=sys.stderr)
    print(f"Results saved to {args.input}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
