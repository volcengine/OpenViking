import argparse
import csv
import json
import os
import sys
from collections import defaultdict

CATEGORY_NAMES = {
    "1": "multi-hop",
    "2": "temporal",
    "3": "open-domain",
    "4": "single-hop",
    "5": "adversarial",
}

csv.field_size_limit(sys.maxsize)


def category_label(category: str) -> str:
    category = str(category or "").strip()
    name = CATEGORY_NAMES.get(category)
    if name:
        return f"{category}-{name}"
    return category or "<missing>"


def main():
    parser = argparse.ArgumentParser(description="Statistics for judge result csv")
    parser.add_argument(
        "--input",
        default="./result/locomo_qa_result_only_sys_memory.csv",
        help="Path to judge result csv file, default: ./result/judge_result.csv",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        exit(1)

    # 统计所有题目 (排除 category=5)
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
    by_category: dict[str, dict[str, int]] = defaultdict(
        lambda: {"CORRECT": 0, "WRONG": 0, "OTHER": 0}
    )

    # 统计 is_valid=True 的题目 (排除 category=5)
    valid_only_correct = 0
    valid_only_wrong = 0
    valid_only_total_time = 0.0
    valid_only_total_prompt_tokens = 0
    valid_only_total_memory_prompt_tokens = 0
    valid_only_total_memory_chars = 0
    valid_only_total_completion_tokens = 0
    valid_only_total_tokens = 0
    valid_only_rows = 0
    valid_only_total_iteration = 0

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 检查 category 是否为 5，跳过
            category = row.get("category", "")
            if category == "5":
                continue

            valid_rows += 1

            # 检查是否是无效题目
            is_invalid = row.get("is_invalid", "").lower() == "true"
            is_valid = not is_invalid

            # 统计结果
            result = row.get("result", "").strip().upper()
            category_key = category_label(category)
            if result == "CORRECT":
                correct += 1
                by_category[category_key]["CORRECT"] += 1
                if is_valid:
                    valid_only_correct += 1
            elif result == "WRONG":
                wrong += 1
                by_category[category_key]["WRONG"] += 1
                if is_valid:
                    valid_only_wrong += 1
            else:
                by_category[category_key]["OTHER"] += 1

            total_iteration += int(row.get("iteration", "0"))
            if is_valid:
                valid_only_total_iteration += int(row.get("iteration", "0"))

            # 统计耗时
            time_cost = row.get("time_cost", "")
            if time_cost:
                try:
                    time_val = float(time_cost)
                    total_time += time_val
                    if is_valid:
                        valid_only_total_time += time_val
                except (ValueError, TypeError):
                    pass

            # 统计token
            token_usage = row.get("token_usage", "")
            if token_usage and token_usage.strip():
                try:
                    token_data = json.loads(token_usage)
                    total_prompt_tokens += token_data.get("prompt_tokens", 0)
                    total_memory_prompt_tokens += token_data.get("memory_prompt_tokens", 0)
                    total_memory_chars += token_data.get("memory_chars", 0)
                    total_completion_tokens += token_data.get("completion_tokens", 0)
                    total_tokens += token_data.get("total_tokens", 0)

                    if is_valid:
                        valid_only_total_prompt_tokens += token_data.get("prompt_tokens", 0)
                        valid_only_total_memory_prompt_tokens += token_data.get(
                            "memory_prompt_tokens", 0
                        )
                        valid_only_total_memory_chars += token_data.get("memory_chars", 0)
                        valid_only_total_completion_tokens += token_data.get("completion_tokens", 0)
                        valid_only_total_tokens += token_data.get("total_tokens", 0)
                except json.JSONDecodeError:
                    pass

            if is_valid:
                valid_only_rows += 1

    total_graded = correct + wrong
    accuracy = correct / total_graded if total_graded > 0 else 0.0
    avg_time = total_time / valid_rows if valid_rows > 0 else 0.0

    # is_valid=True 题目的统计 (排除 category=5)
    valid_only_total_graded = valid_only_correct + valid_only_wrong
    valid_only_accuracy = (
        valid_only_correct / valid_only_total_graded if valid_only_total_graded > 0 else 0.0
    )
    valid_only_avg_time = valid_only_total_time / valid_only_rows if valid_only_rows > 0 else 0.0

    # 平均 token 消耗
    avg_prompt_tokens = total_prompt_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_memory_prompt_tokens = total_memory_prompt_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_memory_chars = total_memory_chars / valid_rows if valid_rows > 0 else 0.0
    avg_completion_tokens = total_completion_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_total_tokens = total_tokens / valid_rows if valid_rows > 0 else 0.0

    valid_only_avg_prompt_tokens = (
        valid_only_total_prompt_tokens / valid_only_rows if valid_only_rows > 0 else 0.0
    )
    valid_only_avg_memory_prompt_tokens = (
        valid_only_total_memory_prompt_tokens / valid_only_rows if valid_only_rows > 0 else 0.0
    )
    valid_only_avg_memory_chars = (
        valid_only_total_memory_chars / valid_only_rows if valid_only_rows > 0 else 0.0
    )
    valid_only_avg_completion_tokens = (
        valid_only_total_completion_tokens / valid_only_rows if valid_only_rows > 0 else 0.0
    )
    valid_only_avg_total_tokens = (
        valid_only_total_tokens / valid_only_rows if valid_only_rows > 0 else 0.0
    )

    output_lines = [
        "=== Judge Result Statistics (excluding category=5) ===",
        f"Total rows: {valid_rows}",
        f"Graded rows: {total_graded}",
        f"Correct: {correct}",
        f"Wrong: {wrong}",
        f"Accuracy: {accuracy:.2%}",
        f"\nAverage time cost: {avg_time:.2f}s",
        f"\nAverage iteration: {total_iteration / valid_rows if valid_rows > 0 else 0.0:.2f}",
        "\nToken usage:",
        f"  Total prompt tokens: {total_prompt_tokens}",
        f"  Total memory prompt tokens: {total_memory_prompt_tokens}",
        f"  Total memory chars: {total_memory_chars}",
        f"  Total completion tokens: {total_completion_tokens}",
        f"  Total tokens: {total_tokens}",
        f"  Avg prompt tokens: {avg_prompt_tokens:.2f}",
        f"  Avg memory prompt tokens: {avg_memory_prompt_tokens:.2f}",
        f"  Avg memory chars: {avg_memory_chars:.2f}",
        f"  Avg completion tokens: {avg_completion_tokens:.2f}",
        f"  Avg total tokens: {avg_total_tokens:.2f}",
        "",
        "By category:",
        f"{'category':<28} {'correct':>8} {'wrong':>8} {'other':>8} {'graded':>8} {'total':>8} {'accuracy':>10}",
    ]

    for category in sorted(by_category):
        category_correct = by_category[category]["CORRECT"]
        category_wrong = by_category[category]["WRONG"]
        category_other = by_category[category]["OTHER"]
        category_graded = category_correct + category_wrong
        category_total = category_graded + category_other
        category_accuracy = category_correct / category_graded if category_graded > 0 else 0.0
        output_lines.append(
            f"{category:<28} {category_correct:>8} {category_wrong:>8} {category_other:>8} "
            f"{category_graded:>8} {category_total:>8} {category_accuracy:>9.2%}"
        )

    valid_only_matches_overall = (
        valid_only_rows == valid_rows
        and valid_only_total_graded == total_graded
        and valid_only_correct == correct
        and valid_only_wrong == wrong
        and valid_only_total_time == total_time
        and valid_only_total_iteration == total_iteration
        and valid_only_total_prompt_tokens == total_prompt_tokens
        and valid_only_total_memory_prompt_tokens == total_memory_prompt_tokens
        and valid_only_total_memory_chars == total_memory_chars
        and valid_only_total_completion_tokens == total_completion_tokens
        and valid_only_total_tokens == total_tokens
    )
    if not valid_only_matches_overall:
        output_lines.extend(
            [
                "",
                "=== Valid Questions Only (is_valid=True, excluding category=5) ===",
                f"Valid rows: {valid_only_rows}",
                f"Valid graded rows: {valid_only_total_graded}",
                f"Valid correct: {valid_only_correct}",
                f"Valid wrong: {valid_only_wrong}",
                f"Valid accuracy: {valid_only_accuracy:.2%}",
                f"\nAverage time cost: {valid_only_avg_time:.2f}s",
                f"\nAverage iteration: {valid_only_total_iteration / valid_only_rows if valid_only_rows > 0 else 0.0:.2f}",
                "\nToken usage:",
                f"  Total prompt tokens: {valid_only_total_prompt_tokens}",
                f"  Total memory prompt tokens: {valid_only_total_memory_prompt_tokens}",
                f"  Total memory chars: {valid_only_total_memory_chars}",
                f"  Total completion tokens: {valid_only_total_completion_tokens}",
                f"  Total tokens: {valid_only_total_tokens}",
                f"  Avg prompt tokens: {valid_only_avg_prompt_tokens:.2f}",
                f"  Avg memory prompt tokens: {valid_only_avg_memory_prompt_tokens:.2f}",
                f"  Avg memory chars: {valid_only_avg_memory_chars:.2f}",
                f"  Avg completion tokens: {valid_only_avg_completion_tokens:.2f}",
                f"  Avg total tokens: {valid_only_avg_total_tokens:.2f}",
            ]
        )

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
