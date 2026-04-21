import argparse
import csv
import hashlib
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


LONGMEMEVAL_TIME_FORMAT = "%Y/%m/%d (%a) %H:%M"


def parse_longmemeval_datetime(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(date_str.strip(), LONGMEMEVAL_TIME_FORMAT)
    except ValueError:
        return None


def build_sample_agent_id(sample_id: str | int, mode: str) -> str:
    """Return the agent_id used for one sample eval."""
    if mode == "shared":
        return "default"
    digest = hashlib.md5(str(sample_id).encode("utf-8")).hexdigest()[:12]
    return f"lm_{digest}"


def build_sample_user_id(sample_id: str | int, mode: str) -> str:
    """Return the user_id used for one sample eval."""
    if mode == "shared":
        return "default"
    digest = hashlib.md5(f"user:{sample_id}".encode("utf-8")).hexdigest()[:12]
    return f"lm_user_{digest}"


def load_csv_qa(
    input_path: str, count: int | None = None, default_time: str | None = None
) -> list[dict]:
    qa_list = []
    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qa_list.append(
                {
                    "sample_id": row.get("sample_id", row.get("question_id", "")),
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                    "question_type": row.get("question_type", ""),
                    "evidence": [],
                    "question_time": default_time,
                }
            )
    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


def load_longmemeval_qa(
    input_path: str,
    sample_index: int | None = None,
    count: int | None = None,
    default_time: str | None = None,
) -> list[dict]:
    if input_path.lower().endswith(".csv"):
        return load_csv_qa(input_path, count, default_time)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            raise ValueError(f"sample index {sample_index} out of range (0-{len(data) - 1})")
        samples = [data[sample_index]]
    else:
        samples = data

    qa_list = []
    for sample in samples:
        question_dt = parse_longmemeval_datetime(sample.get("question_date", ""))
        question_time = question_dt.strftime("%Y-%m-%d") if question_dt else default_time
        qa_list.append(
            {
                "sample_id": sample.get("question_id", ""),
                "question": sample.get("question", ""),
                "answer": sample.get("answer", ""),
                "question_type": sample.get("question_type", ""),
                "evidence": [],
                "question_time": question_time,
            }
        )

    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


def build_vikingbot_chat_cmd(
    question: str,
    question_time: str | None = None,
    sender_id: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    if question_time:
        input_text = f"Current date: {question_time}. Answer the question directly: {question}"
    else:
        input_text = f"Answer the question directly: {question}"
    cmd = ["vikingbot", "chat", "-m", input_text]
    if sender_id:
        cmd.extend(["--sender", sender_id])
    if session_id:
        cmd.extend(["--session", session_id])
    cmd.append("-e")
    return cmd


def run_vikingbot_chat(
    question: str,
    question_time: str | None = None,
    sender_id: str | None = None,
    session_id: str | None = None,
    timeout: int = 300,
) -> tuple[str, dict, float, int, list]:
    cmd = build_vikingbot_chat_cmd(
        question=question,
        question_time=question_time,
        sender_id=sender_id,
        session_id=session_id,
    )
    start_time = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
        elapsed = time.time() - start_time
        output = result.stdout.strip()
        try:
            resp_json = json.loads(output, strict=False)
            response = resp_json.get("text", "")
            token_usage = resp_json.get(
                "token_usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            )
            time_cost = resp_json.get("time_cost", elapsed)
            iteration = resp_json.get("iteration", 0)
            tools_used_names = resp_json.get("tools_used_names", [])
        except (json.JSONDecodeError, ValueError):
            response = f"[PARSE ERROR] {output}"
            token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            time_cost = elapsed
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
        return (
            "[TIMEOUT]",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            0,
            0,
            [],
        )


def load_processed_questions(output_path: str) -> set:
    return set()


def main():
    parser = argparse.ArgumentParser(description="VikingBot LongMemEval evaluation script")
    parser.add_argument(
        "input",
        nargs="?",
        default="/Users/bytedance/mempalace/data/longmemeval-data/longmemeval_s_cleaned.json",
        help="Path to LongMemEval JSON file",
    )
    parser.add_argument(
        "--output",
        default="./result/longmemeval_qa_result.csv",
        help="Path to output csv file",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="LongMemEval sample index (0-based), default all samples",
    )
    parser.add_argument(
        "--count", type=int, default=None, help="Number of QA questions to run, default all"
    )
    parser.add_argument(
        "--threads", type=int, default=5, help="Number of concurrent threads, default: 5"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-question timeout in seconds for the vikingbot subprocess, default: 300",
    )
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    qa_list = load_longmemeval_qa(args.input, args.sample, args.count)
    total = len(qa_list)
    processed_questions = load_processed_questions(args.output)
    remaining = total - len(processed_questions)
    print(
        f"Loaded {total} QA questions, {len(processed_questions)} already processed, {remaining} remaining"
    )

    fieldnames = [
        "sample_id",
        "question",
        "answer",
        "question_type",
        "question_time",
        "response",
        "token_usage",
        "time_cost",
        "iteration",
        "tools_used_names",
        "result",
    ]
    file_exists = os.path.exists(args.output)
    if file_exists:
        with open(args.output, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            first_row = next(reader)
            if "question_time" not in first_row or "question_type" not in first_row:
                os.remove(args.output)
                file_exists = False

    write_lock = threading.Lock()
    with open(args.output, "a+", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
            f.flush()

        processed_count = len(processed_questions)
        remaining_qa = [qa for qa in qa_list if qa["question"] not in processed_questions]
        remaining_count = len(remaining_qa)
        print(
            f"Starting evaluation with {args.threads} concurrent threads, {remaining_count} questions to process"
        )

        def process_qa(qa_item, idx, total_count):
            nonlocal processed_count
            question = qa_item["question"]
            answer = qa_item["answer"]
            sample_id = qa_item["sample_id"]
            question_time = qa_item.get("question_time")
            print(f"Processing {idx}/{total_count}: {question[:60]}...")
            if question_time:
                print(f"  [time context: {question_time}]")

            sender_id = build_sample_user_id(sample_id, "per-sample")
            session_id = build_sample_agent_id(sample_id, "per-sample")
            response, token_usage, time_cost, iteration, tools_used_names = run_vikingbot_chat(
                question,
                question_time,
                sender_id=sender_id,
                session_id=session_id,
                timeout=args.timeout,
            )
            row = {
                "sample_id": sample_id,
                "question": question,
                "answer": answer,
                "question_type": qa_item.get("question_type", ""),
                "question_time": question_time or "",
                "response": response,
                "token_usage": json.dumps(token_usage, ensure_ascii=False),
                "time_cost": round(time_cost, 2),
                "iteration": iteration,
                "tools_used_names": json.dumps(tools_used_names, ensure_ascii=False),
                "result": "",
            }

            with write_lock:
                writer.writerow(row)
                f.flush()
                processed_questions.add(question)
                processed_count += 1
                print(f"Completed {processed_count}/{total}, time cost: {round(time_cost, 2)}s")
            return True

        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futures = []
            for idx, qa_item in enumerate(remaining_qa, 1):
                futures.append(executor.submit(process_qa, qa_item, idx, remaining_count))

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"Error processing QA item: {str(e)}")

    print(f"Evaluation completed, results saved to {args.output}")


if __name__ == "__main__":
    main()
