import argparse
import csv
import json
import os


def main():
    parser = argparse.ArgumentParser(description="Statistics for judge result csv")
    parser.add_argument(
        "--input",
        default="./result/judge_result.csv",
        help="Path to judge result csv file, default: ./result/judge_result.csv",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        exit(1)

    correct = 0
    wrong = 0
    total_time = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    valid_rows = 0

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            valid_rows += 1
            # 统计结果
            result = row.get("result", "").strip().upper()
            if result == "CORRECT":
                correct += 1
            elif result == "WRONG":
                wrong += 1

            # 统计耗时
            time_cost = row.get("time_cost", "")
            if time_cost:
                try:
                    total_time += float(time_cost)
                except (ValueError, TypeError):
                    pass

            # 统计token
            token_usage = row.get("token_usage", "")
            if token_usage and token_usage.strip():
                try:
                    token_data = json.loads(token_usage)
                    total_prompt_tokens += token_data.get("prompt_tokens", 0)
                    total_completion_tokens += token_data.get("completion_tokens", 0)
                    total_tokens += token_data.get("total_tokens", 0)
                except json.JSONDecodeError:
                    pass

    total_graded = correct + wrong
    accuracy = correct / total_graded if total_graded > 0 else 0.0
    avg_time = total_time / valid_rows if valid_rows > 0 else 0.0

    output_lines = [
        "=== Judge Result Statistics ===",
        f"Total rows: {valid_rows}",
        f"Graded rows: {total_graded}",
        f"Correct: {correct}",
        f"Wrong: {wrong}",
        f"Accuracy: {accuracy:.2%}",
        f"\nAverage time cost: {avg_time:.2f}s",
        f"\nToken usage:",
        f"  Total prompt tokens: {total_prompt_tokens}",
        f"  Total completion tokens: {total_completion_tokens}",
        f"  Total tokens: {total_tokens}",
    ]

    # 打印到控制台
    for line in output_lines:
        print(line)

    # 写入summary.txt
    summary_path = os.path.join(os.path.dirname(args.input), "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")
    print(f"\nSummary saved to {summary_path}")


if __name__ == "__main__":
    main()
