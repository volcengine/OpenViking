import argparse
import csv
import json
import os


def make_table(title: str, rows: list[tuple[str, str]]) -> list[str]:
    metric_width = max(len("Metric"), *(len(metric) for metric, _ in rows))
    value_width = max(len("Value"), *(len(value) for _, value in rows))
    border = f"+-{'-' * (metric_width + 2)}-+-{'-' * (value_width + 2)}-+"

    lines = [title, border]
    lines.append(f"| {'Metric'.center(metric_width)} | {'Value'.center(value_width)} |")
    lines.append(border)
    for metric, value in rows:
        lines.append(f"| {metric.ljust(metric_width)} | {value.rjust(value_width)} |")
    lines.append(border)
    return lines


def format_int(value: int) -> str:
    return f"{value:,}"


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

    correct = 0
    wrong = 0
    total_time = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    valid_rows = 0
    total_iteration = 0

    valid_only_correct = 0
    valid_only_wrong = 0
    valid_only_total_time = 0.0
    valid_only_total_prompt_tokens = 0
    valid_only_total_completion_tokens = 0
    valid_only_total_tokens = 0
    valid_only_rows = 0
    valid_only_total_iteration = 0

    with open(args.input, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            category = row.get("category", "")
            if category == "5":
                continue

            valid_rows += 1

            is_invalid = row.get("is_invalid", "").lower() == "true"
            is_valid = not is_invalid

            result = row.get("result", "").strip().upper()
            if result == "CORRECT":
                correct += 1
                if is_valid:
                    valid_only_correct += 1
            elif result == "WRONG":
                wrong += 1
                if is_valid:
                    valid_only_wrong += 1

            total_iteration += int(row.get("iteration", "0"))
            if is_valid:
                valid_only_total_iteration += int(row.get("iteration", "0"))

            time_cost = row.get("time_cost", "")
            if time_cost:
                try:
                    time_val = float(time_cost)
                    total_time += time_val
                    if is_valid:
                        valid_only_total_time += time_val
                except (ValueError, TypeError):
                    pass

            token_usage = row.get("token_usage", "")
            if token_usage and token_usage.strip():
                try:
                    token_data = json.loads(token_usage)
                    total_prompt_tokens += token_data.get("prompt_tokens", 0)
                    total_completion_tokens += token_data.get("completion_tokens", 0)
                    total_tokens += token_data.get("total_tokens", 0)

                    if is_valid:
                        valid_only_total_prompt_tokens += token_data.get("prompt_tokens", 0)
                        valid_only_total_completion_tokens += token_data.get("completion_tokens", 0)
                        valid_only_total_tokens += token_data.get("total_tokens", 0)
                except json.JSONDecodeError:
                    pass

            if is_valid:
                valid_only_rows += 1

    total_graded = correct + wrong
    accuracy = correct / total_graded if total_graded > 0 else 0.0
    avg_time = total_time / valid_rows if valid_rows > 0 else 0.0

    valid_only_total_graded = valid_only_correct + valid_only_wrong
    valid_only_accuracy = (
        valid_only_correct / valid_only_total_graded if valid_only_total_graded > 0 else 0.0
    )
    valid_only_avg_time = valid_only_total_time / valid_only_rows if valid_only_rows > 0 else 0.0

    avg_prompt_tokens = total_prompt_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_completion_tokens = total_completion_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_total_tokens = total_tokens / valid_rows if valid_rows > 0 else 0.0

    valid_only_avg_prompt_tokens = (
        valid_only_total_prompt_tokens / valid_only_rows if valid_only_rows > 0 else 0.0
    )
    valid_only_avg_completion_tokens = (
        valid_only_total_completion_tokens / valid_only_rows if valid_only_rows > 0 else 0.0
    )
    valid_only_avg_total_tokens = (
        valid_only_total_tokens / valid_only_rows if valid_only_rows > 0 else 0.0
    )

    all_rows = [
        ("Total rows", format_int(valid_rows)),
        ("Graded rows", format_int(total_graded)),
        ("Correct", format_int(correct)),
        ("Wrong", format_int(wrong)),
        ("Accuracy", f"{accuracy:.2%}"),
        ("Avg time cost", f"{avg_time:.2f}s"),
        ("Avg iteration", f"{total_iteration / valid_rows if valid_rows > 0 else 0.0:.2f}"),
        ("Total prompt tokens", format_int(total_prompt_tokens)),
        ("Total completion tokens", format_int(total_completion_tokens)),
        ("Total tokens", format_int(total_tokens)),
        ("Avg prompt tokens", f"{avg_prompt_tokens:.2f}"),
        ("Avg completion tokens", f"{avg_completion_tokens:.2f}"),
        ("Avg total tokens", f"{avg_total_tokens:.2f}"),
    ]

    valid_rows_table = [
        ("Valid rows", format_int(valid_only_rows)),
        ("Valid graded rows", format_int(valid_only_total_graded)),
        ("Valid correct", format_int(valid_only_correct)),
        ("Valid wrong", format_int(valid_only_wrong)),
        ("Valid accuracy", f"{valid_only_accuracy:.2%}"),
        ("Avg time cost", f"{valid_only_avg_time:.2f}s"),
        (
            "Avg iteration",
            f"{valid_only_total_iteration / valid_only_rows if valid_only_rows > 0 else 0.0:.2f}",
        ),
        ("Total prompt tokens", format_int(valid_only_total_prompt_tokens)),
        ("Total completion tokens", format_int(valid_only_total_completion_tokens)),
        ("Total tokens", format_int(valid_only_total_tokens)),
        ("Avg prompt tokens", f"{valid_only_avg_prompt_tokens:.2f}"),
        ("Avg completion tokens", f"{valid_only_avg_completion_tokens:.2f}"),
        ("Avg total tokens", f"{valid_only_avg_total_tokens:.2f}"),
    ]

    output_lines = [
        *make_table("=== Judge Result Statistics (excluding category=5) ===", all_rows),
        "",
        *make_table("=== Valid Questions Only (is_valid=True, excluding category=5) ===", valid_rows_table),
    ]

    for line in output_lines:
        print(line)

    summary_path = os.path.join(os.path.dirname(args.input), "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")
    print(f"\nSummary saved to {summary_path}")


if __name__ == "__main__":
    main()
