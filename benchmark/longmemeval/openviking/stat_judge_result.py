import argparse
import csv
import json
import os
import sys
from collections import defaultdict

csv.field_size_limit(sys.maxsize)


def main():
    parser = argparse.ArgumentParser(description="Statistics for LongMemEval judge result csv")
    parser.add_argument(
        "--input",
        default="./result/longmemeval_qa_result.csv",
        help="Path to judge result csv file",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        raise SystemExit(1)

    correct = 0
    wrong = 0
    total_time = 0.0
    total_prompt_tokens = 0
    total_memory_prompt_tokens = 0
    total_memory_chars = 0
    total_completion_tokens = 0
    total_tokens = 0
    valid_rows = 0
    total_iteration = 0
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"CORRECT": 0, "WRONG": 0, "OTHER": 0})

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            valid_rows += 1
            result = row.get("result", "").strip().upper()
            question_type = row.get("question_type", "").strip() or "<missing>"
            if result == "CORRECT":
                correct += 1
                by_type[question_type]["CORRECT"] += 1
            elif result == "WRONG":
                wrong += 1
                by_type[question_type]["WRONG"] += 1
            else:
                by_type[question_type]["OTHER"] += 1

            total_iteration += int(row.get("iteration", "0"))
            time_cost = row.get("time_cost", "")
            if time_cost:
                try:
                    total_time += float(time_cost)
                except (ValueError, TypeError):
                    pass

            token_usage = row.get("token_usage", "")
            if token_usage and token_usage.strip():
                try:
                    token_data = json.loads(token_usage)
                    total_prompt_tokens += token_data.get("prompt_tokens", 0)
                    total_memory_prompt_tokens += token_data.get("memory_prompt_tokens", 0)
                    total_memory_chars += token_data.get("memory_chars", 0)
                    total_completion_tokens += token_data.get("completion_tokens", 0)
                    total_tokens += token_data.get("total_tokens", 0)
                except json.JSONDecodeError:
                    pass

    total_graded = correct + wrong
    accuracy = correct / total_graded if total_graded > 0 else 0.0
    avg_time = total_time / valid_rows if valid_rows > 0 else 0.0
    avg_prompt_tokens = total_prompt_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_memory_prompt_tokens = total_memory_prompt_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_memory_chars = total_memory_chars / valid_rows if valid_rows > 0 else 0.0
    avg_completion_tokens = total_completion_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_total_tokens = total_tokens / valid_rows if valid_rows > 0 else 0.0

    output_lines = [
        "=== Judge Result Statistics ===",
        f"Total rows: {valid_rows}",
        f"Graded rows: {total_graded}",
        f"Correct: {correct}",
        f"Wrong: {wrong}",
        f"Accuracy: {accuracy:.2%}",
        "",
        f"Average time cost: {avg_time:.2f}s",
        "",
        f"Average iteration: {total_iteration / valid_rows if valid_rows > 0 else 0.0:.2f}",
        "",
        "Token usage:",
        f"  Total prompt tokens: {total_prompt_tokens}",
        f"  Total memory prompt tokens: {total_memory_prompt_tokens}",
        f"  Total memory chars: {total_memory_chars}",
        f"  Total completion tokens: {total_completion_tokens}",
        f"  Total tokens: {total_tokens}",
        f"  Average prompt tokens/row: {avg_prompt_tokens:.1f}",
        f"  Average memory prompt tokens/row: {avg_memory_prompt_tokens:.1f}",
        f"  Average memory chars/row: {avg_memory_chars:.1f}",
        f"  Average completion tokens/row: {avg_completion_tokens:.1f}",
        f"  Average total tokens/row: {avg_total_tokens:.1f}",
        "",
        "By question type:",
        f"{'question_type':<28} {'correct':>8} {'wrong':>8} {'other':>8} {'graded':>8} {'total':>8} {'accuracy':>10}",
    ]

    for question_type in sorted(by_type):
        type_correct = by_type[question_type]["CORRECT"]
        type_wrong = by_type[question_type]["WRONG"]
        type_other = by_type[question_type]["OTHER"]
        type_graded = type_correct + type_wrong
        type_total = type_graded + type_other
        type_accuracy = type_correct / type_graded if type_graded > 0 else 0.0
        output_lines.append(
            f"{question_type:<28} {type_correct:>8} {type_wrong:>8} {type_other:>8} "
            f"{type_graded:>8} {type_total:>8} {type_accuracy:>9.2%}"
        )

    for line in output_lines:
        print(line)

    summary_path = os.path.join(os.path.dirname(args.input), "longmemeval_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")
    print(f"\nSummary saved to {summary_path}")


if __name__ == "__main__":
    main()
