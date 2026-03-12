import argparse
import json
import subprocess
import time
import csv
import os
import re


def load_locomo_qa(
    input_path: str, sample_index: int | None = None, count: int | None = None
) -> list[dict]:
    """加载LoCoMo数据集的QA部分，逻辑同原eval.py"""
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    qa_list = []
    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            raise ValueError(f"sample index {sample_index} out of range (0-{len(data) - 1})")
        samples = [data[sample_index]]
    else:
        samples = data

    for sample in samples:
        sample_id = sample.get("sample_id", "")
        for qa in sample.get("qa", []):
            qa_list.append(
                {
                    "sample_id": sample_id,
                    "question": qa["question"],
                    "answer": qa["answer"],
                    "category": qa.get("category", ""),
                    "evidence": qa.get("evidence", []),
                }
            )

    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


def run_vikingbot_chat(question: str) -> tuple[str, dict, float]:
    """执行vikingbot chat命令，返回回答、token使用情况、耗时（秒）"""
    input = f"Answer the question directly: {question}"
    cmd = ["vikingbot", "chat", "-m", input, "-e"]
    start_time = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
        end_time = time.time()
        time_cost = end_time - start_time

        output = result.stdout.strip()
        # 解析返回的json结果，处理换行、多余前缀等特殊情况
        try:
            resp_json = json.loads(output, strict=False)
            response = resp_json.get("text", "")
            token_usage = resp_json.get(
                "token_usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            )
            time_cost = resp_json.get("time_cost", time_cost)
        except (json.JSONDecodeError, ValueError) as e:
            response = f"[PARSE ERROR] {output}"
            token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return response, token_usage, time_cost
    except subprocess.CalledProcessError as e:
        return (
            f"[CMD ERROR] {e.stderr}",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            0,
        )
    except subprocess.TimeoutExpired:
        time_cost = 0
        return (
            "[TIMEOUT]",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            time_cost,
        )


def load_processed_questions(output_path: str) -> set:
    """加载已处理的问题集合，避免重复执行"""
    processed = set()
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                processed.add(row["question"])
    return processed


def main():
    parser = argparse.ArgumentParser(description="VikingBot QA evaluation script")
    parser.add_argument(
        "input",
        nargs="?",
        default="./test_data/locomo10.json",
        help="Path to locomo10.json file, default: ./test_data/locomo10.json",
    )
    parser.add_argument(
        "--output",
        default="./result/locomo_qa_result.csv",
        help="Path to output csv file, default: ./result/locomo_qa_result.csv",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="LoCoMo sample index (0-based), default all samples",
    )
    parser.add_argument(
        "--count", type=int, default=None, help="Number of QA questions to run, default all"
    )
    args = parser.parse_args()

    # 确保输出目录存在
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    # 加载QA数据
    qa_list = load_locomo_qa(args.input, args.sample, args.count)
    total = len(qa_list)

    # 加载已处理的问题
    processed_questions = load_processed_questions(args.output)
    remaining = total - len(processed_questions)
    print(
        f"Loaded {total} QA questions, {len(processed_questions)} already processed, {remaining} remaining"
    )

    fieldnames = [
        "sample_id",
        "question",
        "answer",
        "response",
        "token_usage",
        "time_cost",
        "result",
    ]
    # 打开CSV文件，不存在则创建写表头，存在则追加
    file_exists = os.path.exists(args.output)
    with open(args.output, "a+", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
            f.flush()

        processed_count = len(processed_questions)
        for idx, qa_item in enumerate(qa_list, 1):
            question = qa_item["question"]
            if question in processed_questions:
                print(f"Skipping {idx}/{total}: already processed")
                continue

            answer = qa_item["answer"]
            print(f"Processing {idx}/{total}: {question[:60]}...")
            response, token_usage, time_cost = run_vikingbot_chat(question)

            row = {
                "sample_id": qa_item["sample_id"],
                "question": question,
                "answer": answer,
                "response": response,
                "token_usage": json.dumps(token_usage, ensure_ascii=False),
                "time_cost": round(time_cost, 2),
                "result": "",
            }
            writer.writerow(row)
            f.flush()
            processed_questions.add(question)
            processed_count += 1
            print(f"Completed {processed_count}/{total}, time cost: {round(time_cost, 2)}s")

    print(f"Evaluation completed, results saved to {args.output}")


if __name__ == "__main__":
    main()
