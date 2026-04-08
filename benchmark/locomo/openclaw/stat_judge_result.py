import argparse
import csv
import os


def main():
    parser = argparse.ArgumentParser(description="Statistics for judge result csv")
    parser.add_argument(
        "--input",
        default="./result/qa_results_sample0.csv",
        help="Path to judge result csv file, default: ./result/qa_results_sample0.csv",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        exit(1)

    # 统计所有题目 (排除 category=5)
    correct = 0
    wrong = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    valid_rows = 0

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 检查 category 是否为 5，跳过
            category = row.get("category", "")
            if category == "5":
                continue

            valid_rows += 1

            # 统计结果
            result = row.get("result", "").strip().upper()
            if result == "CORRECT":
                correct += 1
            elif result == "WRONG":
                wrong += 1

            # 统计token
            try:
                total_input_tokens += int(row.get("input_tokens", 0))
                total_output_tokens += int(row.get("output_tokens", 0))
                total_tokens += int(row.get("total_tokens", 0))
            except (ValueError, TypeError):
                pass

    total_graded = correct + wrong
    accuracy = correct / total_graded if total_graded > 0 else 0.0

    # 平均 token 消耗
    avg_input_tokens = total_input_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_output_tokens = total_output_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_total_tokens = total_tokens / valid_rows if valid_rows > 0 else 0.0

    output_lines = [
        "=== Judge Result Statistics (excluding category=5) ===",
        f"Total rows: {valid_rows}",
        f"Graded rows: {total_graded}",
        f"Correct: {correct}",
        f"Wrong: {wrong}",
        f"Accuracy: {accuracy:.2%}",
        f"\nToken usage:",
        f"  Total input tokens: {total_input_tokens}",
        f"  Total output tokens: {total_output_tokens}",
        f"  Total tokens: {total_tokens}",
        f"  Avg input tokens: {avg_input_tokens:.2f}",
        f"  Avg output tokens: {avg_output_tokens:.2f}",
        f"  Avg total tokens: {avg_total_tokens:.2f}",
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
