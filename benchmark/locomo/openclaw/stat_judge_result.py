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
    parser.add_argument(
        "--import-csv",
        default="./result/import_success.csv",
        help="Path to import_success.csv file for OpenViking token stats, default: ./result/import_success.csv",
    )
    args = parser.parse_args()

    output_lines = []

    # 统计 QA 结果
    if os.path.exists(args.input):
        qa_stats = process_qa_results(args.input)
        output_lines.extend(qa_stats)
    else:
        output_lines.append(f"Warning: QA result file not found: {args.input}")

    # 统计 Import token
    if os.path.exists(args.import_csv):
        if output_lines:
            output_lines.append("")
        import_stats = process_import_csv(args.import_csv)
        output_lines.extend(import_stats)
    else:
        output_lines.append(f"Warning: Import CSV file not found: {args.import_csv}")

    # 打印到控制台
    for line in output_lines:
        print(line)

    # 写入summary.txt
    if args.input:
        summary_path = os.path.join(os.path.dirname(args.input), "summary.txt")
    elif args.import_csv:
        summary_path = os.path.join(os.path.dirname(args.import_csv), "summary.txt")
    else:
        summary_path = "./result/summary.txt"

    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")
    print(f"\nSummary saved to {summary_path}")


def process_qa_results(input_path: str) -> list[str]:
    """处理 QA 结果 CSV"""
    # 统计所有题目 (排除 category=5)
    correct = 0
    wrong = 0
    total_no_cache_tokens = 0  # input_tokens
    total_cache_read_tokens = 0  # cacheRead
    total_output_tokens = 0  # output_tokens
    total_input_tokens = 0  # no_cache + cacheRead
    total_elapsed_seconds = 0.0
    min_elapsed_seconds = None
    max_elapsed_seconds = None
    elapsed_rows = 0
    valid_rows = 0

    with open(input_path, "r", encoding="utf-8", newline="") as f:
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
                no_cache = int(row.get("input_tokens", 0))
                cache_read = int(row.get("cacheRead", 0))
                output = int(row.get("output_tokens", 0))

                total_no_cache_tokens += no_cache
                total_cache_read_tokens += cache_read
                total_output_tokens += output
                total_input_tokens += no_cache + cache_read
            except (ValueError, TypeError):
                pass

            # 统计耗时（仅在字段存在且可解析时计入，避免缺失值按 0 拉低均值）
            try:
                elapsed_raw = row.get("elapsed_seconds")
                if elapsed_raw is None:
                    continue
                elapsed_text = str(elapsed_raw).strip()
                if not elapsed_text:
                    continue

                elapsed_seconds = float(elapsed_text)
                total_elapsed_seconds += elapsed_seconds
                min_elapsed_seconds = (
                    elapsed_seconds
                    if min_elapsed_seconds is None
                    else min(min_elapsed_seconds, elapsed_seconds)
                )
                max_elapsed_seconds = (
                    elapsed_seconds
                    if max_elapsed_seconds is None
                    else max(max_elapsed_seconds, elapsed_seconds)
                )
                elapsed_rows += 1
            except (ValueError, TypeError):
                pass

    total_graded = correct + wrong
    accuracy = correct / total_graded if total_graded > 0 else 0.0

    # 平均 token 消耗
    avg_no_cache = total_no_cache_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_cache_read = total_cache_read_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_output = total_output_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_total_input = total_input_tokens / valid_rows if valid_rows > 0 else 0.0
    avg_elapsed_seconds = total_elapsed_seconds / elapsed_rows if elapsed_rows > 0 else 0.0

    return [
        "=== Judge Result Statistics (excluding category=5) ===",
        f"Total rows: {valid_rows:,}",
        f"Graded rows: {total_graded:,}",
        f"Correct: {correct:,}",
        f"Wrong: {wrong:,}",
        f"Accuracy: {accuracy:.2%}",
        "\nElapsed time (QA):",
        f"  Total elapsed seconds: {total_elapsed_seconds:,.3f}",
        f"  Avg elapsed seconds: {avg_elapsed_seconds:,.3f}",
        f"  Min elapsed seconds: {(min_elapsed_seconds or 0.0):,.3f}",
        f"  Max elapsed seconds: {(max_elapsed_seconds or 0.0):,.3f}",
        "\nToken usage (QA):",
        f"  Total no-cache tokens (input_tokens): {total_no_cache_tokens:,}",
        f"  Total cacheRead tokens: {total_cache_read_tokens:,}",
        f"  Total output tokens: {total_output_tokens:,}",
        f"  Total input tokens (no-cache + cacheRead): {total_input_tokens:,}",
        f"  Avg no-cache tokens: {avg_no_cache:,.2f}",
        f"  Avg cacheRead tokens: {avg_cache_read:,.2f}",
        f"  Avg output tokens: {avg_output:,.2f}",
        f"  Avg total input tokens: {avg_total_input:,.2f}",
    ]


def process_import_csv(input_path: str) -> list[str]:
    """处理 import_success.csv 的 token 统计"""
    total_embedding = 0
    total_vlm = 0
    total_total = 0
    valid_rows = 0

    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            valid_rows += 1
            try:
                total_embedding += int(row.get("embedding_tokens", 0))
                total_vlm += int(row.get("vlm_tokens", 0))
                total_total += int(row.get("total_tokens", 0))
            except (ValueError, TypeError):
                pass

    avg_embedding = total_embedding / valid_rows if valid_rows > 0 else 0.0
    avg_vlm = total_vlm / valid_rows if valid_rows > 0 else 0.0
    avg_total = total_total / valid_rows if valid_rows > 0 else 0.0

    return [
        "=== OpenViking Import Token Statistics ===",
        f"Total sessions: {valid_rows:,}",
        "\nToken usage (Import):",
        f"  Total embedding tokens: {total_embedding:,}",
        f"  Total VLM tokens: {total_vlm:,}",
        f"  Total tokens: {total_total:,}",
        f"  Avg embedding tokens: {avg_embedding:,.2f}",
        f"  Avg VLM tokens: {avg_vlm:,.2f}",
        f"  Avg total tokens: {avg_total:,.2f}",
    ]


if __name__ == "__main__":
    main()
