import argparse
import json
import subprocess
import time
import csv
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


def get_evidence_text(evidence_list: list, sample: dict) -> list[str]:
    """根据 evidence 列表获取原始对话文本

    evidence 格式: ['D1:3', 'D2:5'] -> session_1 第3条, session_2 第5条
    """
    if not evidence_list:
        return []

    conv = sample.get("conversation", {})
    results = []

    for ev in evidence_list:
        # 解析 D1:3 -> session_1, index 2
        try:
            parts = ev.split(":")
            session_num = int(parts[0][1:])  # D1 -> 1
            msg_index = int(parts[1]) - 1  # 3 -> index 2

            session_key = f"session_{session_num}"
            session_messages = conv.get(session_key, [])

            if msg_index < len(session_messages):
                msg = session_messages[msg_index]
                text = msg.get("text", "")
                speaker = msg.get("speaker", "")
                results.append(f"{speaker}: {text}")
            else:
                results.append(f"[{ev}: out of range]")
        except (ValueError, IndexError):
            results.append(f"[{ev}: invalid format]")

    return results


def parse_locomo_datetime(date_str: str) -> datetime | None:
    """解析 LoCoMo 时间格式，如 '1:56 pm on 8 May, 2023'"""
    try:
        # 移除时间部分，只保留日期 "8 May, 2023"
        if " on " in date_str:
            date_part = date_str.split(" on ")[-1]
            return datetime.strptime(date_part.strip(), "%d %B, %Y")
    except ValueError:
        pass
    return None


def get_sample_question_time(sample: dict) -> str | None:
    """从 sample 的 conversation 中提取最后一个有内容 session 的时间，返回 ISO 格式日期"""
    conversation = sample.get("conversation", {})

    # 找所有 session_N 字段（非 date_time）
    session_keys = [
        k for k in conversation.keys() if k.startswith("session_") and "date_time" not in k
    ]
    if not session_keys:
        return None

    # 按 session 编号排序，找到最后一个有内容的
    def get_session_num(key):
        try:
            return int(key.replace("session_", ""))
        except ValueError:
            return 0

    session_keys.sort(key=get_session_num, reverse=True)

    for session_key in session_keys:
        if conversation.get(session_key):  # 有内容
            # 找到对应的 date_time
            session_num = get_session_num(session_key)
            dt_key = f"session_{session_num}_date_time"
            date_str = conversation.get(dt_key)
            if date_str:
                dt = parse_locomo_datetime(date_str)
                if dt:
                    return dt.strftime("%Y-%m-%d")

    return None


def load_csv_qa(
    input_path: str, count: int | None = None, default_time: str | None = None
) -> list[dict]:
    """从CSV文件加载QA数据，取sample_id和question字段"""
    qa_list = []
    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qa_list.append(
                {
                    "sample_id": row.get("sample_id", ""),
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                    "category": "",
                    "evidence": [],
                    "question_time": default_time,
                }
            )

    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


def load_locomo_qa(
    input_path: str,
    sample_index: int | None = None,
    count: int | None = None,
    default_time: str | None = None,
    question_index: int | None = None,
    invalid_questions: set | None = None,
) -> list[dict]:
    """加载LoCoMo数据集的QA部分，支持JSON和CSV格式

    Args:
        invalid_questions: 无效题目问题内容集合，用于标记无效题目
    """
    if input_path.lower().endswith(".csv"):
        return load_csv_qa(input_path, count, default_time)

    # 原有JSON格式处理逻辑
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    qa_list = []
    # 支持数字索引或 sample_id (如 "conv-26")
    if sample_index is not None:
        # 尝试解析为数字索引
        try:
            idx = int(sample_index)
            if idx < 0 or idx >= len(data):
                raise ValueError(f"sample index {idx} out of range (0-{len(data) - 1})")
            samples = [data[idx]]
        except ValueError:
            # 尝试匹配 sample_id
            matched = [s for s in data if s.get("sample_id") == sample_index]
            if not matched:
                raise ValueError(f"sample_id '{sample_index}' not found")
            samples = matched
    else:
        samples = data

    for s_idx, sample in enumerate(samples):
        original_id = sample.get("sample_id", "")
        # Find the sample's index in the full dataset
        if sample_index is not None:
            try:
                data_idx = int(sample_index)
            except ValueError:
                data_idx = next(i for i, s in enumerate(data) if s.get("sample_id") == original_id)
        else:
            data_idx = next(i for i, s in enumerate(data) if s.get("sample_id") == original_id)
        sample_id = f"sample_{data_idx}"
        question_time = get_sample_question_time(sample)
        qa_items = sample.get("qa", [])

        # 获取对话中的所有说话者
        conv = sample.get("conversation", {})
        speakers = []
        if conv.get("speaker_a"):
            speakers.append(conv["speaker_a"])
        if conv.get("speaker_b"):
            speakers.append(conv["speaker_b"])

        # 如果指定了 question_index，只返回那一个问题
        if question_index is not None:
            if question_index < 0 or question_index >= len(qa_items):
                raise ValueError(
                    f"question index {question_index} out of range (0-{len(qa_items) - 1})"
                )
            qa = qa_items[question_index]
            evidence_list = qa.get("evidence", [])
            question_id = f"{sample_id}_qa{question_index}"
            qa_list.append(
                {
                    "sample_id": sample_id,
                    "question_id": question_id,
                    "question_index": question_index,
                    "question": qa["question"],
                    "answer": qa["answer"],
                    "category": qa.get("category", ""),
                    "evidence": evidence_list,
                    "evidence_text": get_evidence_text(evidence_list, sample),
                    "question_time": question_time,
                    "speakers": speakers,
                    "is_invalid": qa["question"] in invalid_questions
                    if invalid_questions
                    else False,
                }
            )
        else:
            for q_idx, qa in enumerate(qa_items):
                evidence_list = qa.get("evidence", [])
                question_id = f"{sample_id}_qa{q_idx}"
                qa_list.append(
                    {
                        "sample_id": sample_id,
                        "question_id": question_id,
                        "question_index": q_idx,
                        "question": qa["question"],
                        "answer": qa["answer"],
                        "category": qa.get("category", ""),
                        "evidence": evidence_list,
                        "evidence_text": get_evidence_text(evidence_list, sample),
                        "question_time": question_time,
                        "speakers": speakers,
                        "is_invalid": qa["question"] in invalid_questions
                        if invalid_questions
                        else False,
                    }
                )

    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


def run_vikingbot_chat(
    question: str,
    question_time: str | None = None,
    sample_id: str | None = None,
    question_id: str | None = None,
    memory_users: list[str] | None = None,
) -> tuple[str, dict, float, int, list]:
    """执行vikingbot chat命令，返回回答、token使用情况、耗时（秒）、迭代次数、使用的工具列表"""
    # 先执行 /new 命令清除会话
    if sample_id:
        new_cmd = [
            "vikingbot",
            "chat",
            "-m",
            "/new",
            "-e",
            "--sender",
            sample_id,
            "--session",
            question_id,
        ]
        # 添加 --memory-user 参数
        if memory_users:
            for user in memory_users:
                new_cmd.extend(["--memory-user", user])
        try:
            # print(f'new_cmd={new_cmd}')
            subprocess.run(new_cmd, capture_output=True, text=True, timeout=300)
        except Exception:
            # 忽略 /new 命令的错误
            pass

    # 如果有 question_time，注入到 prompt 中
    if question_time:
        input = f"Current date: {question_time}. Answer the question directly: {question}"
    else:
        input = f"Answer the question directly: {question}"

    cmd = ["vikingbot", "chat", "-m", input, "-e"]
    # 添加 --sender 作为 user_id，--session 作为 agent_id，实现访问独立 userspace
    if sample_id:
        cmd.extend(["--sender", sample_id, "--session", question_id])
    # 添加 --memory-user 参数，指定检索哪些用户的记忆
    if memory_users:
        for user in memory_users:
            cmd.extend(["--memory-user", user])
    start_time = time.time()
    try:
        # print(f'cmd={cmd}')
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
            iteration = resp_json.get("iteration", 0)
            tools_used_names = resp_json.get("tools_used_names", [])
        except (json.JSONDecodeError, ValueError) as e:
            response = f"[PARSE ERROR] {output}"
            token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            iteration = 0
            tools_used_names = []
        return response, token_usage, time_cost, iteration, tools_used_names
    except subprocess.CalledProcessError as e:
        return (
            f"[CMD ERROR] {e.stderr}",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            0,
            0,
            [],
        )
    except subprocess.TimeoutExpired:
        time_cost = 0
        return (
            "[TIMEOUT]",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            time_cost,
            0,
            [],
        )


def load_processed_questions(output_path: str, skip_done: bool = False) -> set[str]:
    """加载已处理的问题集合。"""
    if not skip_done or not os.path.exists(output_path):
        return set()

    processed_questions = set()
    with open(output_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            question = row.get("question")
            if question:
                processed_questions.add(question)
    return processed_questions


def main():
    # 基于脚本所在目录计算默认数据文件路径
    script_dir = Path(__file__).parent.resolve()
    default_input = str(script_dir / ".." / "data" / "locomo10.json")
    default_errors = str(script_dir / ".." / "data" / "errors.json")

    parser = argparse.ArgumentParser(description="VikingBot QA evaluation script")
    parser.add_argument(
        "input",
        nargs="?",
        default=default_input,
        help="Path to locomo10.json file",
    )
    parser.add_argument(
        "--output",
        default="./result/locomo_qa_result.csv",
        help="Path to output csv file, default: ./result/locomo_qa_result.csv",
    )
    parser.add_argument(
        "--errors",
        default=default_errors,
        help="Path to invalid questions JSON file",
    )
    parser.add_argument(
        "--sample",
        type=str,
        default=None,
        help="LoCoMo sample index (0-based) or sample_id (e.g., conv-26)",
    )
    parser.add_argument(
        "--question-index",
        type=int,
        default=None,
        help="Question index (0-based) for single question testing",
    )
    parser.add_argument(
        "--count", type=int, default=None, help="Number of QA questions to run, default all"
    )
    parser.add_argument(
        "--threads", type=int, default=40, help="Number of concurrent threads, default: 40"
    )
    parser.add_argument(
        "--update-mode",
        action="store_true",
        help="Update mode: if output file exists, update matching question_index rows instead of overwriting",
    )
    parser.add_argument(
        "--group-chat",
        action="store_true",
        default=False,
        help="Group-chat mode: pass --memory-user to vikingbot chat. Default is group-chat mode.",
    )
    parser.add_argument(
        "--retry-wrong",
        default=None,
        help="Path to a judged result CSV. Only re-evaluate valid rows where result=WRONG.",
    )
    parser.add_argument(
        "--skip-done",
        action="store_true",
        help="Skip questions already present in the output file",
    )
    args = parser.parse_args()

    # 如果指定了 question-index，自动设置 count=1
    if args.question_index is not None and args.count is None:
        args.count = 1

    # 确保输出目录存在
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    # 加载无效题目集合（按问题内容匹配，因为 errors.json 索引可能与数据不匹配）
    invalid_questions = set()
    errors_path = os.path.expanduser(args.errors)
    if os.path.exists(errors_path):
        with open(errors_path, "r", encoding="utf-8") as f:
            errors_data = json.load(f)
        # 按问题内容建立集合
        if errors_data and isinstance(errors_data[0], dict):
            invalid_questions = {item["question"] for item in errors_data}
        else:
            invalid_questions = set(errors_data)
        print(f"Loaded {len(invalid_questions)} invalid questions from {errors_path}")
    else:
        print(f"No errors file found at {errors_path}, is_invalid will be False for all questions")

    # --retry-wrong: 从结果 CSV 中提取有效错题
    retry_wrong_questions = None
    if args.retry_wrong:
        retry_wrong_questions = set()
        retry_wrong_samples = set()
        with open(args.retry_wrong, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("is_invalid", "").lower() == "true":
                    continue
                if row.get("result") == "WRONG":
                    retry_wrong_questions.add(row["question"])
                    retry_wrong_samples.add(row["sample_id"])
        print(f"[retry-wrong] Found {len(retry_wrong_questions)} valid wrong questions from {args.retry_wrong}")
        if retry_wrong_samples:
            print(f"[retry-wrong] Affected samples: {', '.join(sorted(retry_wrong_samples))}")

    # 加载QA数据（所有题目，包括无效题目，只标记 is_invalid）
    qa_list = load_locomo_qa(
        args.input,
        args.sample,
        args.count,
        question_index=args.question_index,
        invalid_questions=invalid_questions,
    )
    total = len(qa_list)

    # --retry-wrong: 只保留错题
    if retry_wrong_questions is not None:
        before = len(qa_list)
        qa_list = [qa for qa in qa_list if qa["question"] in retry_wrong_questions]
        print(f"[retry-wrong] Filtered {before} -> {len(qa_list)} questions (only wrong ones)")

    # 过滤掉 category=5 的问题
    qa_list = [qa for qa in qa_list if str(qa.get("category")) != "5"]
    print(f"Filtered to {len(qa_list)} questions after removing category=5")

    # 加载已处理的问题
    processed_questions = load_processed_questions(args.output, skip_done=args.skip_done)
    remaining = total - len(processed_questions)
    print(
        f"Loaded {total} QA questions, {len(processed_questions)} already processed, {remaining} remaining"
    )

    fieldnames = [
        "sample_id",
        "question_index",
        "result",
        "is_invalid",
        "question",
        "answer",
        "category",
        "question_time",
        "evidence",
        "evidence_text",
        "response",
        "token_usage",
        "time_cost",
        "iteration",
        "tools_used_names",
    ]

    # 创建线程锁，确保多线程写文件安全
    write_lock = threading.Lock()

    # 存储处理后的新行
    new_rows = []
    processed_count = 0

    # 过滤掉已经处理过的问题
    remaining_qa = [qa for qa in qa_list if qa["question"] not in processed_questions]
    remaining_count = len(remaining_qa)
    print(
        f"Starting evaluation with {args.threads} concurrent threads, {remaining_count} questions to process"
    )

    def process_qa(qa_item, idx, total_count):
        """单个QA处理函数，供多线程调用"""
        question = qa_item["question"]
        answer = qa_item["answer"]
        question_time = qa_item.get("question_time")
        # 使用 question_id 作为 session_id，实现完全独立并行
        sample_id = qa_item.get("sample_id")
        question_id = qa_item.get("question_id")
        speakers = qa_item.get("speakers", [])
        print(f"Processing {idx}/{total_count}: {question[:60]}...")
        if question_time:
            print(f"  [time context: {question_time}]")
        if speakers:
            print(f"  [memory users: {speakers}]")

        response, token_usage, time_cost, iteration, tools_used_names = run_vikingbot_chat(
            question, question_time, sample_id, question_id,
            memory_users=None if not args.group_chat else speakers,
        )

        row = {
            "sample_id": qa_item["sample_id"],
            "question_index": qa_item.get("question_index", ""),
            "result": "",
            "question": question,
            "answer": answer,
            "category": qa_item.get("category", ""),
            "question_time": question_time or "",
            "evidence": json.dumps(qa_item.get("evidence", [])),
            "evidence_text": json.dumps(qa_item.get("evidence_text", [])),
            "response": response,
            "token_usage": json.dumps(token_usage, ensure_ascii=False),
            "time_cost": round(time_cost, 2),
            "iteration": iteration,
            "tools_used_names": json.dumps(tools_used_names, ensure_ascii=False),
            "is_invalid": qa_item.get("is_invalid", False),
        }

        # 线程安全的结果收集
        with write_lock:
            nonlocal processed_count
            new_rows.append(row)
            processed_questions.add(question)
            processed_count += 1
            print(f"Completed {processed_count}/{total_count}, time cost: {round(time_cost, 2)}s")
        return True

    # 使用线程池处理：全局并行，每个 question 独立 session
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        # 提交所有任务
        futures = []
        for idx, qa_item in enumerate(remaining_qa, 1):
            futures.append(executor.submit(process_qa, qa_item, idx, remaining_count))

        # 等待所有任务完成
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"Error processing QA item: {str(e)}")

    # 写文件逻辑
    if args.update_mode and os.path.exists(args.output):
        # 更新模式：读取现有文件，更新匹配行
        print(f"Update mode: updating existing file {args.output}")
        with open(args.output, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            existing_rows = list(reader)
            existing_fieldnames = reader.fieldnames or fieldnames

        # 更新匹配的行
        updated_count = 0
        for new_row in new_rows:
            q_idx = str(new_row.get("question_index", ""))
            found = False
            for row in existing_rows:
                if str(row.get("question_index", "")) == q_idx:
                    row.update(new_row)
                    found = True
                    updated_count += 1
                    break
            if not found:
                existing_rows.append(new_row)
                updated_count += 1

        # 写回文件
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=existing_fieldnames)
            writer.writeheader()
            writer.writerows(existing_rows)

        print(f"Updated {updated_count} rows in {args.output}")
    else:
        # 普通模式：覆盖写入
        if os.path.exists(args.output):
            os.remove(args.output)

        with open(args.output, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(new_rows)

        print(f"Evaluation completed, results saved to {args.output}")


if __name__ == "__main__":
    main()
