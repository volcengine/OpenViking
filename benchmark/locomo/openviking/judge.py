import argparse
import csv
import json
import os
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv
from pathlib import Path

try:
    from benchmark.locomo.openviking.locomo_prompts import (
        JUDGE_SYSTEM_PROMPT,
        get_judge_prompt,
        get_judge_prompt_with_evidence,
        preprocess_answer,
    )
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from locomo_prompts import (  # type: ignore
        JUDGE_SYSTEM_PROMPT,
        get_judge_prompt,
        get_judge_prompt_with_evidence,
        preprocess_answer,
    )

# 加载本地环境变量文件
env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)


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
) -> tuple[bool, str]:
    processed_answer = preprocess_answer(category, gold_answer)
    if evidence_text:
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
        resp = await llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": accuracy_prompt},
            ],
            temperature=0,
            timeout=60,
        )
        content = resp.choices[0].message.content.strip()
        # 提取JSON内容
        start_idx = content.find("{")
        end_idx = content.rfind("}")
        if start_idx != -1 and end_idx != -1:
            json_str = content[start_idx : end_idx + 1].strip()
            result = json.loads(json_str)
            is_correct = result.get("label", "WRONG").strip().upper() == "CORRECT"
            reasoning = result.get("reasoning", "")
            return is_correct, reasoning
        return False, f"[PARSE ERROR] Invalid response: {content}"
    except Exception as e:
        return False, f"[API ERROR] {str(e)}"


def load_answers(input_path: str) -> tuple[list[dict], list[str]]:
    """加载待评分的回答，返回所有行和表头"""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames.copy()
        # 新增reasoning列如果不存在
        if "reasoning" not in fieldnames:
            fieldnames.append("reasoning")
        rows = list(reader)
    return rows, fieldnames


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
        help="Volcengine API base URL, default: https://ark.cn-beijing.volces.com/api/v3",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        help="Volcengine API token, default from ARK_API_KEY or OPENAI_API_KEY env var",
    )
    parser.add_argument(
        "--model",
        default="doubao-seed-2-0-pro-260215",
        help="Judge model name, default: doubao-seed-2-0-pro-260215",
    )
    parser.add_argument(
        "--parallel", type=int, default=5, help="Parallel request count, default: 5"
    )
    args = parser.parse_args()

    if not args.token:
        print("Error: API token is required")
        print("\n请通过以下方式设置 API key:")
        print("  1. 创建 ~/.openviking_benchmark_env 文件，内容如下:")
        print("     ARK_API_KEY=你的key")
        print("  2. 或者通过 --token 参数传入")
        print("  3. 或者设置环境变量: export ARK_API_KEY=你的key")
        exit(1)

    # 加载数据
    rows, fieldnames = load_answers(args.input)
    total = len(rows)
    # 筛选未评分的行
    ungraded = [
        i
        for i, row in enumerate(rows)
        if not row.get("result") and str(row.get("category", "")).strip() != "5"
    ]
    print(f"Total answers: {total}, ungraded: {len(ungraded)}")

    if not ungraded:
        print("All answers already graded, exit")
        return

    # 初始化OpenAI客户端
    client = AsyncOpenAI(base_url=args.base_url, api_key=args.token)

    # 并发处理
    semaphore = asyncio.Semaphore(args.parallel)
    file_lock = asyncio.Lock()  # 用于同步文件写入

    async def save_results():
        """保存当前所有结果到CSV文件，使用临时文件+原子替换避免文件损坏"""
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
            )
            row["result"] = "CORRECT" if is_correct else "WRONG"
            row["reasoning"] = reasoning

            # 处理完一条就立即保存结果
            await save_results()
            print(f"Saved result for {idx + 1}/{total}: {row['result']}")

            return idx, row

    tasks = [process_row(idx) for idx in ungraded]
    await asyncio.gather(*tasks)

    # 统计结果
    correct = sum(1 for row in rows if row.get("result") == "CORRECT")
    total_graded = sum(1 for row in rows if row.get("result"))
    accuracy = correct / total_graded if total_graded > 0 else 0.0
    print(f"\nGrading completed: {correct}/{total_graded} correct, accuracy: {accuracy:.2%}")
    print(f"All results saved to {args.input}")


if __name__ == "__main__":
    asyncio.run(main())
