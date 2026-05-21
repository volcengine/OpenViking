import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

from openai import AsyncOpenAI

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(*_args, **_kwargs):
        return False


env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)
DEFAULT_JUDGE_ERROR_RETRIES = int(os.getenv("JUDGE_ERROR_RETRIES", "2"))


def is_retryable_judge_error(reasoning: str) -> bool:
    return reasoning.startswith("[API ERROR]") or reasoning.startswith("[PARSE ERROR]")


def require_suite(value: str) -> str:
    if value not in {"baseline", "e2e", "preingest"}:
        raise argparse.ArgumentTypeError("suite must be one of: baseline, e2e, preingest")
    return value


def default_input_path(script_dir: Path, suite: str) -> str:
    if suite == "baseline":
        return str(script_dir / "result_baseline" / "qa_results.csv")
    if suite == "e2e":
        return str(script_dir / "result_e2e" / "qa_results.csv")
    return str(script_dir / "result_preingest" / "qa_results.csv")


async def grade_answer(
    llm_client, model: str, question: str, gold_answer: str, response: str
) -> tuple[bool, str]:
    system_prompt = """
        You are an expert grader that determines if answers to questions match a gold standard answer
        """

    accuracy_prompt = f"""
    Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
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


async def main():
    script_dir = Path(__file__).parent.resolve()
    parser = argparse.ArgumentParser(description="Shared QA judge for Hermes LoCoMo benchmark")
    parser.add_argument("--suite", type=require_suite, default="baseline", help="Benchmark suite")
    parser.add_argument("--input", default=None, help="Path to QA result csv file")
    parser.add_argument(
        "--base-url", default="https://ark.cn-beijing.volces.com/api/v3", help="LLM API base URL"
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        help="LLM API token",
    )
    parser.add_argument("--model", default="doubao-seed-2-0-pro-260215", help="Judge model name")
    parser.add_argument("--parallel", type=int, default=5, help="Parallel request count")
    parser.add_argument(
        "--error-retries",
        type=int,
        default=DEFAULT_JUDGE_ERROR_RETRIES,
        help="Retry judge API/parse failures this many times",
    )
    parser.add_argument(
        "--retry-delay-sec",
        type=float,
        default=2.0,
        help="Seconds to wait between judge retry attempts",
    )
    args = parser.parse_args()

    if args.input is None:
        args.input = default_input_path(script_dir, args.suite)

    if not args.token:
        print("Error: API token is required")
        print("\nSet API key via:")
        print("  1. Create ~/.openviking_benchmark_env with: ARK_API_KEY=your_key")
        print("  2. Or pass --token your_key")
        print("  3. Or set env var: export ARK_API_KEY=your_key")
        raise SystemExit(1)

    rows, fieldnames = load_answers(args.input)

    valid_rows = []
    ungraded = []
    for idx, row in enumerate(rows):
        if row.get("category", "") == "5":
            continue
        valid_rows.append(idx)
        if not row.get("result"):
            ungraded.append(idx)

    total = len(rows)
    valid_total = len(valid_rows)
    print(
        f"Total answers: {total}, valid (category != 5): {valid_total}, ungraded: {len(ungraded)}"
    )

    if not ungraded:
        print("All valid answers already graded, exit")
        return

    client = AsyncOpenAI(base_url=args.base_url, api_key=args.token)
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

    async def process_row(idx: int):
        async with semaphore:
            row = rows[idx]
            question = row["question"]
            gold = row.get("expected") or row.get("answer")
            response = row["response"]
            print(f"Grading {idx + 1}/{total}: {question[:60]}...")
            max_attempts = max(0, args.error_retries) + 1
            is_correct = False
            reasoning = ""
            for attempt in range(1, max_attempts + 1):
                is_correct, reasoning = await grade_answer(
                    client, args.model, question, gold, response
                )
                if not is_retryable_judge_error(reasoning):
                    row["result"] = "CORRECT" if is_correct else "WRONG"
                    break
                if attempt < max_attempts:
                    print(
                        f"Judge retry {attempt}/{max_attempts} for row {idx + 1}: {reasoning}",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(args.retry_delay_sec)
            else:
                row["result"] = ""
            if is_retryable_judge_error(reasoning):
                row["result"] = ""
            row["reasoning"] = reasoning
            await save_results()
            print(f"Saved result for {idx + 1}/{total}: {row['result'] or 'UNJUDGED'}")
            return idx, row

    await asyncio.gather(*[process_row(idx) for idx in ungraded])

    correct = sum(
        1 for row in rows if row.get("category", "") != "5" and row.get("result") == "CORRECT"
    )
    total_graded = sum(1 for row in rows if row.get("category", "") != "5" and row.get("result"))
    remaining_ungraded = sum(
        1 for row in rows if row.get("category", "") != "5" and not row.get("result")
    )
    accuracy = correct / total_graded if total_graded > 0 else 0.0
    print(f"\nGrading completed: {correct}/{total_graded} correct, accuracy: {accuracy:.2%}")
    print(f"All results saved to {args.input}")
    if remaining_ungraded:
        print(
            f"Judge left {remaining_ungraded} valid row(s) ungraded after retries", file=sys.stderr
        )
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
