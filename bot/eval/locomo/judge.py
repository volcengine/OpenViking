import argparse
import csv
import json
import os
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()


async def grade_answer(
    llm_client, model: str, question: str, gold_answer: str, response: str
) -> tuple[bool, str]:
    system_prompt = """
        You are an expert grader that determines if answers to questions match a gold standard answer
        """

    ACCURACY_PROMPT = f"""
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
                {"role": "user", "content": ACCURACY_PROMPT},
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
            is_correct = result.get("is_correct", "WRONG").strip().upper() == "CORRECT"
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
        fieldnames = list(reader.fieldnames) if reader.fieldnames is not None else []
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
        default="./result/locomo_qa_result.csv",
        help="Path to QA result csv file, default: ./result/locomo_qa_result.csv",
    )
    parser.add_argument(
        "--output",
        default="./result/judge_result.csv",
        help="Path to output judge result csv file, default: ./result/judge_result.csv",
    )
    parser.add_argument(
        "--base-url",
        default="https://ark.cn-beijing.volces.com/api/v3",
        help="Volcengine API base URL, default: https://ark.cn-beijing.volces.com/api/v3",
    )
    parser.add_argument(
        "--token",
        required=True,
        help="Volcengine API key, required parameter",
    )
    parser.add_argument(
        "--model",
        default="ep-20260224000522-sxrg5",
        help="Judge model name, default: doubao-seed-2-0-pro-260215",
    )
    parser.add_argument(
        "--parallel", type=int, default=5, help="Parallel request count, default: 5"
    )
    args = parser.parse_args()

    if not args.token:
        print("Error: API token is required, set ARK_API_KEY env var or pass via --token")
        exit(1)

    # 加载输入数据
    input_rows, fieldnames = load_answers(args.input)
    # 新增result列如果不存在
    if "result" not in fieldnames:
        fieldnames.insert(len(fieldnames) - 1, "result")

    # 加载已有输出结果（如果存在）
    existing_rows = {}
    if os.path.exists(args.output):
        with open(args.output, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = f"{row['sample_id']}||{row['question']}"
                existing_rows[key] = row

    # 合并数据：已有结果的用已有，没有的用输入数据
    rows = []
    for input_row in input_rows:
        key = f"{input_row['sample_id']}||{input_row['question']}"
        if key in existing_rows:
            rows.append(existing_rows[key])
        else:
            rows.append(input_row)

    total = len(rows)
    # 筛选未评分的行：同时检查result是否为空，以及sample_id+question是否已处理
    ungraded = []
    processed_keys = set(existing_rows.keys())
    for i, row in enumerate(rows):
        key = f"{row['sample_id']}||{row['question']}"
        if key not in processed_keys or not row.get("result"):
            ungraded.append(i)
    print(f"Total answers: {total}, ungraded: {len(ungraded)}")

    if not ungraded:
        # 统计结果
        correct = sum(1 for row in rows if row.get("result") == "CORRECT")
        total_graded = sum(1 for row in rows if row.get("result"))
        accuracy = correct / total_graded if total_graded > 0 else 0.0
        print(
            f"All answers already graded: {correct}/{total_graded} correct, accuracy: {accuracy:.2%}"
        )
        return

    # 初始化OpenAI客户端
    client = AsyncOpenAI(base_url=args.base_url, api_key=args.token)

    # 并发处理
    semaphore = asyncio.Semaphore(args.parallel)
    write_lock = asyncio.Lock()

    async def process_row(idx):
        async with semaphore:
            row = rows[idx]
            question = row["question"]
            gold = row["answer"]
            response = row["response"]
            print(f"Grading {idx + 1}/{total}: {question[:60]}...")
            is_correct, reasoning = await grade_answer(client, args.model, question, gold, response)
            row["result"] = "CORRECT" if is_correct else "WRONG"
            row["reasoning"] = reasoning

            # 每处理完一行就写入文件
            async with write_lock:
                with open(args.output, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                print(f"Saved progress to {args.output} after processing row {idx + 1}")
            return idx, row

    tasks = [process_row(idx) for idx in ungraded]
    await asyncio.gather(*tasks)

    # 统计最终结果
    correct = sum(1 for row in rows if row.get("result") == "CORRECT")
    total_graded = sum(1 for row in rows if row.get("result"))
    accuracy = correct / total_graded if total_graded > 0 else 0.0
    print(f"\nGrading completed: {correct}/{total_graded} correct, accuracy: {accuracy:.2%}")
    print(f"Final results saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
