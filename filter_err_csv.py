import csv
import os

input_file = "result/locomo_result_multi_read_all.csv"
output_file = "result/locomo_result_multi_read_all_err.csv"

# 检查输入文件是否存在
if not os.path.exists(input_file):
    print(f"错误：文件 {input_file} 不存在")
    exit(1)

# 读取输入文件并筛选错误行
err_rows = []
with open(input_file, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row.get("result") == "WRONG":
            err_rows.append(row)

# 写入输出文件
with open(output_file, "w", encoding="utf-8", newline="") as f:
    if err_rows:
        writer = csv.DictWriter(f, fieldnames=err_rows[0].keys())
        writer.writeheader()
        writer.writerows(err_rows)

print(f"成功生成错误结果文件：{output_file}")
print(f"错误行数：{len(err_rows)}")
