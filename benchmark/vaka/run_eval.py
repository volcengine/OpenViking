#!/usr/bin/env python3
"""
VAKA 记忆评测脚本
从飞书文档读取数据
"""

import argparse
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

import openviking as ov

DEFAULT_URL = "http://localhost:1934"
DEFAULT_API_KEY = "1cf407c39990e5dc874ccc697942da4892208a86a44c4781396dfdc57aa5c98d"
DEFAULT_AGENT_ID = "test"

# 飞书文档配置
SPREADSHEET_TOKEN = "MCKSsuLW8h3LhotVzkelZw0ogyb"
SAMPLES_SHEET_ID = "0e2366"
QUERIES_SHEET_ID = "kH4bXO"


def load_sample_from_sheets() -> list[dict]:
    """从飞书表格读取 sample 数据"""
    import subprocess

    # 使用 lark-cli 读取 sheets
    cmd = [
        "lark-cli", "sheets", "+read",
        "--spreadsheet-token", SPREADSHEET_TOKEN,
        "--sheet-id", SAMPLES_SHEET_ID,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    samples = []
    values = data.get("data", {}).get("valueRange", {}).get("values", [])

    # 第一行是 header
    if not values:
        return samples

    # 解析数据行
    for row in values[1:]:  # 跳过 header
        if len(row) >= 3:
            samples.append({
                "session_group": row[0],
                "turn_id": row[1],
                "conversation": row[2],
            })

    return samples


def load_query_from_sheets() -> list[dict]:
    """从飞书表格读取 query 数据"""
    import subprocess

    cmd = [
        "lark-cli", "sheets", "+read",
        "--spreadsheet-token", SPREADSHEET_TOKEN,
        "--sheet-id", QUERIES_SHEET_ID,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    queries = []
    values = data.get("data", {}).get("valueRange", {}).get("values", [])

    if not values:
        return queries

    for row in values[1:]:  # 跳过 header
        if len(row) >= 4:
            queries.append({
                "question_id": row[0],
                "question": row[1],
                "ground_truth": row[2],
                "session_group": row[3],
            })

    return queries


def group_samples_by_session(samples: list[dict]) -> dict[str, list[dict]]:
    """按 turn_id 前缀分组对话（session_group_turn）"""
    groups = {}
    for sample in samples:
        # turn_id 格式: 1_001_1 -> session_1
        turn_id = sample["turn_id"]
        session_key = f"session_{turn_id.split('_')[0]}"
        if session_key not in groups:
            groups[session_key] = []
        groups[session_key].append(sample)
    return groups


def run_ingest(client: ov.SyncHTTPClient, samples: list[dict]) -> dict[str, str]:
    """写入对话并提交，返回 session_id -> trace_id"""
    print("\n" + "=" * 60)
    print("Phase 1: Ingest 对话数据")
    print("=" * 60)

    # 按 session 分组
    session_groups = group_samples_by_session(samples)
    trace_ids = {}

    for session_id, session_samples in session_groups.items():
        print(f"\n[Ingest] Session: {session_id}")

        # 创建 session
        result = client.create_session()
        actual_session_id = result.get("session_id", session_id)
        print(f"  Created session: {actual_session_id}")

        # 获取最早时间作为 session 时间
        conv = json.loads(session_samples[0]["conversation"])
        first_msg = conv["messages"][0]
        session_time = first_msg.get("created_at", datetime.now().isoformat())

        # 写入对话
        total = len(session_samples)
        for i, sample in enumerate(session_samples, 1):
            conv = json.loads(sample["conversation"])
            messages = conv["messages"]
            for msg in messages:
                role = "user" if msg["speaker"] == "用户" else "assistant"
                client.add_message(
                    actual_session_id,
                    role=role,
                    content=msg["content"],
                    created_at=msg.get("created_at", session_time),
                )
            print(f"  [{i}/{total}] Turn {sample['turn_id']} added")

        # 提交 session
        print(f"  [Commit] 提交 session {actual_session_id}...")
        commit_result = client.commit_session(actual_session_id)
        task_id = commit_result.get("task_id")
        trace_id = commit_result.get("trace_id")
        trace_ids[actual_session_id] = trace_id
        print(f"  Trace ID: {trace_id}")

        # 等待后台任务完成
        if task_id:
            print(f"  [Wait] 等待记忆抽取完成 (task_id={task_id})...")
            while True:
                task = client.get_task(task_id)
                if not task or task.get("status") in ("completed", "failed"):
                    break
                time.sleep(1)
            print(f"  [Done] Task status: {task.get('status') if task else 'not found'}")

    # 等待向量化完成
    print("\n[Wait] 等待向量化完成...")
    client.wait_processed()

    return trace_ids


def run_query(client: ov.SyncHTTPClient, queries: list[dict], output_path: str):
    """执行查询并保存结果"""
    print("\n" + "=" * 60)
    print("Phase 2: Query 记忆召回")
    print("=" * 60)

    fieldnames = [
        "question_id",
        "question",
        "ground_truth",
        "session_group",
        "response",
        "recall_count",
        "recalled_texts",
    ]

    results = []
    total = len(queries)

    for i, q in enumerate(queries, 1):
        question_id = q["question_id"]
        question = q["question"]
        ground_truth = q["ground_truth"]
        session_group = q["session_group"]

        print(f"\n[{i}/{total}] Query: {question[:50]}...")

        # 执行召回
        find_result = client.find(question, limit=5)

        recalled_texts = []
        recall_count = 0

        # 收集召回的记忆
        if hasattr(find_result, "memories") and find_result.memories:
            for m in find_result.memories:
                text = getattr(m, "content", "") or getattr(m, "text", "") or str(m)
                recalled_texts.append(text)
                recall_count += 1

        if hasattr(find_result, "resources") and find_result.resources:
            for r in find_result.resources:
                text = getattr(r, "content", "") or getattr(r, "text", "") or str(r)
                recalled_texts.append(text)
                recall_count += 1

        # 构建响应（召回的记忆内容）
        response = "\n---\n".join(recalled_texts) if recalled_texts else "[No recall]"

        print(f"  召回 {recall_count} 条记忆")

        results.append({
            "question_id": question_id,
            "question": question,
            "ground_truth": ground_truth,
            "session_group": session_group,
            "response": response,
            "recall_count": recall_count,
            "recalled_texts": json.dumps(recalled_texts, ensure_ascii=False),
        })

    # 保存结果
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n[Done] 结果已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="VAKA 记忆评测脚本")
    parser.add_argument("--output", default="result/eval_result.csv", help="输出文件路径")
    parser.add_argument("--url", default=DEFAULT_URL, help="OpenViking Server URL")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API Key")
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID, help="Agent ID")
    parser.add_argument(
        "--phase",
        choices=["all", "ingest", "query"],
        default="all",
        help="all=全部, ingest=仅写入, query=仅查询",
    )
    args = parser.parse_args()

    # 计算默认路径
    script_dir = Path(__file__).parent.resolve()
    output_path = args.output if os.path.isabs(args.output) else str(script_dir / args.output)

    print("=" * 60)
    print("VAKA 记忆评测")
    print("=" * 60)
    print(f"Spreadsheet: {SPREADSHEET_TOKEN}")
    print(f"Sample Sheet: {SAMPLES_SHEET_ID}")
    print(f"Query Sheet: {QUERIES_SHEET_ID}")
    print(f"Output: {output_path}")

    # 从飞书加载数据
    print("\n[Load] 从飞书加载数据...")
    samples = load_sample_from_sheets()
    queries = load_query_from_sheets()
    print(f"  Samples: {len(samples)} 条对话")
    print(f"  Queries: {len(queries)} 个问题")

    # 初始化客户端
    client = ov.SyncHTTPClient(
        url=args.url,
        api_key=args.api_key,
        agent_id=args.agent_id,
        timeout=180,
    )
    client.initialize()

    try:
        if args.phase in ("all", "ingest"):
            run_ingest(client, samples)

        if args.phase in ("all", "query"):
            run_query(client, queries, output_path)

    finally:
        client.close()

    print("\n" + "=" * 60)
    print("评测完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()