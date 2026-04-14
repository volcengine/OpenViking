#!/usr/bin/env python3
"""
VAKA 记忆评测 - LLM 评分脚本
参考 benchmark/locomo/vikingbot/judge.py
"""

import argparse
import asyncio
import csv
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

# 加载本地环境变量文件
env_file = Path.home() / ".openviking_benchmark_env"
load_dotenv(env_file)


async def grade_answer(
    llm_client,
    model: str,
    question: str,
    gold_answer: str,
    response: str,
) -> tuple[bool, str]:
    """使用 LLM 判断回答是否正确"""
    system_prompt = """
你是一个专家评分员，负责判断 AI 的回答是否正确匹配标准答案。

你需要判断回答是否涵盖了标准答案中的关键信息。
评分标准：
- 如果回答正确包含了标准答案的核心信息，返回 CORRECT
- 如果回答遗漏了重要信息或理解错误，返回 WRONG

请基于上下文理解来判断，不要过度严格。
"""

    grading_prompt = f"""
Question: {question}

Gold Answer (标准答案): {gold_answer}

Generated Answer (待评分回答): {response}

请判断 Generated Answer 是否正确。
- 如果正确涵盖了 Gold Answer 的核心信息，返回 CORRECT
- 如果遗漏了重要信息或理解错误，返回 WRONG

首先提供简短的推理说明，然后给出最终判断。

响应格式（JSON）：
{{"is_correct": "CORRECT" 或 "WRONG", "reasoning": "你的推理说明"}}
"""

    try:
        resp = await llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": grading_prompt},
            ],
            temperature=0,
            timeout=60,
        )
        content = resp.choices[0].message.content.strip()

        # 提取 JSON 内容
        start_idx = content.find("{")
        end_idx = content.rfind("}")
        if start_idx != -1 and end_idx != -1:
            json_str = content[start_idx : end_idx + 1].strip()
            result = json.loads(json_str)
            is_correct = result.get("is_correct", "WRONG").strip().upper() == "CORRECT"
            reasoning = result.get("reasoning", "")
            return is_correct, reasoning
        return False, f"[PARSE ERROR] Invalid response: {content}"
    except Exception as e:
        return False, f"[API ERROR] {str(e)}"


def load_answers(input_path: str) -> tuple[list[dict], list[str]]:
    """加载待评分的回答"""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        # 新增 reasoning 列如果不存在
        if "reasoning" not in fieldnames:
            fieldnames.append("reasoning")
        if "result" not in fieldnames:
            fieldnames.append("result")
        rows = list(reader)
    return rows, fieldnames


async def main():
    parser = argparse.ArgumentParser(description="VAKA 记忆评测 - LLM 评分")
    parser.add_argument(
        "--input",
        default="result/eval_result.csv",
        help="评测结果 CSV 文件路径",
    )
    parser.add_argument(
        "--output",
        default="result/judged_result.csv",
        help="评分结果输出路径",
    )
    parser.add_argument(
        "--base-url",
        default="https://ark.cn-beijing.volces.com/api/v3",
        help="Volcengine API base URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        help="API token",
    )
    parser.add_argument(
        "--model",
        default="doubao-seed-2-0-pro-260215",
        help="Judge model name",
    )
    parser.add_argument(
        "--parallel", type=int, default=5, help="并发请求数",
    )
    args = parser.parse_args()

    # 计算默认路径
    script_dir = Path(__file__).parent.resolve()
    input_path = args.input if os.path.isabs(args.input) else str(script_dir / args.input)
    output_path = args.output if os.path.isabs(args.output) else str(script_dir / args.output)

    if not args.token:
        print("Error: API token is required")
        print("\n请通过以下方式设置 API key:")
        print("  1. 创建 ~/.openviking_benchmark_env 文件，内容如下:")
        print("     ARK_API_KEY=你的key")
        print("  2. 或者通过 --token 参数传入")
        print("  3. 或者设置环境变量: export ARK_API_KEY=你的key")
        exit(1)

    # 加载数据
    print(f"[Load] 加载评测结果: {input_path}")
    rows, fieldnames = load_answers(input_path)
    total = len(rows)

    # 筛选未评分的行
    ungraded = [i for i, row in enumerate(rows) if not row.get("result")]
    print(f"Total answers: {total}, ungraded: {len(ungraded)}")

    if not ungraded:
        print("All answers already graded, exit")
        return

    # 初始化 OpenAI 客户端
    client = AsyncOpenAI(base_url=args.base_url, api_key=args.token)

    # 并发处理
    semaphore = asyncio.Semaphore(args.parallel)
    file_lock = asyncio.Lock()

    async def save_results():
        """保存结果到 CSV 文件"""
        async with file_lock:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            temp_file = f"{output_path}.tmp"
            with open(temp_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temp_file, output_path)

    async def process_row(idx):
        async with semaphore:
            row = rows[idx]
            question = row["question"]
            gold = row["ground_truth"]
            response = row["response"]
            print(f"Grading [{idx + 1}/{total}]: {question[:50]}...")

            is_correct, reasoning = await grade_answer(
                client, args.model, question, gold, response
            )
            row["result"] = "CORRECT" if is_correct else "WRONG"
            row["reasoning"] = reasoning

            # 每处理完一条就保存
            await save_results()
            print(f"  Result: {row['result']}")

            return idx, row

    # 并发执行
    tasks = [process_row(idx) for idx in ungraded]
    await asyncio.gather(*tasks)

    # 统计结果
    correct = sum(1 for row in rows if row.get("result") == "CORRECT")
    total_graded = sum(1 for row in rows if row.get("result"))
    accuracy = correct / total_graded if total_graded > 0 else 0.0

    print(f"\n{'=' * 60}")
    print(f"评分完成: {correct}/{total_graded} 正确, 准确率: {accuracy:.2%}")
    print(f"结果已保存到: {output_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())