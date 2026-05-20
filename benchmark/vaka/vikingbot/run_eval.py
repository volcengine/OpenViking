from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import time
from pathlib import Path

_RUN_ID = int(time.time())

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_OUTPUT = str(SCRIPT_DIR / "result" / "vaka_qa_result.csv")

# 1. 默认 user/account，与 import_to_ov.py 保持一致
DEFAULT_USER_ID = "default"
DEFAULT_AGENT_ID = "default"
DEFAULT_ACCOUNT = "default"
DEFAULT_OPENVIKING_URL = "http://localhost:1933"

FIELDNAMES = [
    "question_index",
    "question",
    "standard_answer",
    "reference_answer",
    "response",
    "response_input_tokens",
    "response_output_tokens",
    "time_cost",
    "result",
    "reasoning",
    "retrieved_memories_json",
    "retrieved_memories_text",
]


# 2. 从 CSV 加载 query 和 standard_answer
def load_qa_from_csv(input_path: str, count: int | None = None) -> list[dict]:
    """从 CSV 文件加载 QA 数据，取 query 和 standard_answer 字段"""
    qa_list = []
    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            question = (row.get("query") or row.get("question") or "").strip()
            if not question:
                continue
            qa_list.append(
                {
                    "question": question,
                    "standard_answer": (row.get("standard_answer") or "").strip(),
                    "reference_answer": (row.get("reference_answer") or "").strip(),
                }
            )
    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


# 3. 调用 OpenViking /bot/v1/chat 生成回答
async def chat_with_bot(
    question: str,
    *,
    client,
    openviking_url: str,
    session_id: str = "default",
    user_id: str | None = None,
    account: str | None = None,
    api_key: str | None = None,
    disable_memory_commit: bool = True,
) -> tuple[dict, float]:
    """调用 OpenViking /bot/v1/chat 端点生成回答，返回 (完整响应dict, 耗时秒)"""
    url = f"{openviking_url.rstrip('/')}/bot/v1/chat"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    if account:
        headers["X-OpenViking-Account"] = account
    if user_id:
        headers["X-OpenViking-User"] = user_id

    body = {
        "message": question,
        "session_id": session_id,
        "stream": False,
    }
    if disable_memory_commit:
        body["disabled_tools"] = ["openviking_memory_commit"]
    if user_id:
        body["user_id"] = user_id

    start_time = time.time()
    resp = await client.post(url, json=body, headers=headers)

    time_cost = time.time() - start_time

    if resp.status_code != 200:
        return {
            "message": f"[HTTP ERROR] status={resp.status_code}, body={resp.text[:200]}",
            "relevant_memories": "",
        }, time_cost

    try:
        data = resp.json()
        if not isinstance(data, dict):
            return {
                "message": f"[INVALID RESPONSE] {str(data)[:200]}",
                "relevant_memories": "",
            }, time_cost
        return data, time_cost
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "message": f"[PARSE ERROR] {str(exc)}: {resp.text[:200]}",
            "relevant_memories": "",
        }, time_cost


# 6. 提取召回记忆
def _extract_memories_from_payload(payload: object) -> list[dict]:
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        memories = payload.get("memories")
        if isinstance(memories, list):
            return [item for item in memories if isinstance(item, dict)]
    return []


def _extract_multi_read_memories(data: str) -> list[dict]:
    """Parse openviking_multi_read tool result string into structured memories."""
    import re

    memories = []
    block_pattern = re.compile(
        r"^--- START OF (?P<uri>.+?) ---\s*\n"
        r"(?P<content>.*?)"
        r"\n^--- END OF (?P=uri) ---\s*$",
        re.MULTILINE | re.DOTALL,
    )

    for match in block_pattern.finditer(data):
        uri = match.group("uri").strip()
        content = match.group("content").strip()
        if content.startswith("ERROR:"):
            continue
        if uri and content:
            memories.append(
                {
                    "uri": uri,
                    "score": None,
                    "abstract": content[:200],
                    "content": content,
                    "source": "multi_read",
                }
            )
    return memories


def extract_retrieved_memories(data: dict) -> tuple[str, list[dict]]:
    """Extract retrieved memories from bot response. Returns (query_memory_text, llm_memories_list)."""
    query_memory = ""
    val = data.get("relevant_memories")
    if isinstance(val, str):
        query_memory = val

    llm_memories: list[dict] = []
    events = data.get("events")
    if isinstance(events, list):
        # Pair tool_call + tool_result by order
        pending_tool_name: str | None = None
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            event_data = event.get("data", "")

            if event_type == "tool_call":
                # tool_call data format: "tool_name({args})"
                if isinstance(event_data, str):
                    pending_tool_name = event_data.split("(", 1)[0].strip()
                else:
                    pending_tool_name = None

            elif event_type == "tool_result":
                if pending_tool_name == "openviking_multi_read" and isinstance(event_data, str):
                    llm_memories.extend(_extract_multi_read_memories(event_data))
                else:
                    llm_memories.extend(_extract_memories_from_payload(event_data))
                pending_tool_name = None

    deduped_by_key: dict[str, dict] = {}
    key_order: list[str] = []
    for memory in llm_memories:
        key = memory.get("uri")
        if not isinstance(key, str) or not key:
            key = json.dumps(memory, ensure_ascii=False, sort_keys=True)
        existing = deduped_by_key.get(key)
        if existing is None:
            key_order.append(key)
            deduped_by_key[key] = memory
            continue

        if memory.get("source") == "multi_read" and existing.get("source") != "multi_read":
            deduped_by_key[key] = memory

    deduped = [deduped_by_key[key] for key in key_order]
    return query_memory, deduped


def build_memories_text(query_memory: str, llm_memories: list[dict]) -> str:
    parts: list[str] = []
    if query_memory.strip():
        parts.append(f"[viking_search]\n{query_memory.strip()}")

    # Split llm_memories by source
    search_memories = [m for m in llm_memories if m.get("source") != "multi_read"]
    multi_read_memories = [m for m in llm_memories if m.get("source") == "multi_read"]

    if search_memories:
        lines: list[str] = []
        for memory in search_memories:
            uri = str(memory.get("uri") or "")
            score = memory.get("score")
            score_text = f"{float(score):.6f}" if isinstance(score, (int, float)) else ""
            abstract = str(memory.get("abstract") or "").replace("\n", " ").strip()
            entry_parts = []
            if score_text:
                entry_parts.append(f"[{score_text}]")
            if uri:
                entry_parts.append(uri)
            if abstract:
                entry_parts.append(abstract)
            if entry_parts:
                lines.append(" | ".join(entry_parts))
        if lines:
            parts.append(f"[tool_search]\n" + "\n".join(lines))

    if multi_read_memories:
        lines: list[str] = []
        for memory in multi_read_memories:
            uri = str(memory.get("uri") or "")
            abstract = str(memory.get("abstract") or "").replace("\n", " ").strip()
            entry_parts = []
            if uri:
                entry_parts.append(uri)
            if abstract:
                entry_parts.append(abstract)
            if entry_parts:
                lines.append(" | ".join(entry_parts))
        if lines:
            parts.append(f"[multi_read]\n" + "\n".join(lines))

    return "\n\n".join(parts)


# 7. 单个 QA 处理
async def process_single_qa(
    qa_item: dict,
    orig_idx: int,
    total_count: int,
    *,
    client,
    openviking_url: str,
    user_id: str | None,
    account: str | None,
    api_key: str | None,
    disable_memory_commit: bool,
) -> dict:
    """处理单个 QA：调用 /bot/v1/chat 生成回答"""
    question = qa_item["question"]
    # question_for_bot = (
    #     question
    #     + "\n\n请基于已召回的 OpenViking 记忆直接回答。"
    #     + "优先给结论，再给必要依据；不要泛泛展开无关原则。"
    # )
    standard_answer = qa_item.get("standard_answer", "")
    reference_answer = qa_item.get("reference_answer", "")
    print(f"Processing {orig_idx}/{total_count}: {question[:60]}...")

    # 8. 每个问题使用独立 session，避免上下文干扰
    session_id = f"vaka_eval_{_RUN_ID}_{orig_idx}"
    data, time_cost = await chat_with_bot(
        question,
        client=client,
        openviking_url=openviking_url,
        session_id=session_id,
        user_id=user_id,
        account=account,
        api_key=api_key,
        disable_memory_commit=disable_memory_commit,
    )
    response = data.get("message", "")
    token_usage = data.get("token_usage") or {}
    print(f"Completed {orig_idx}/{total_count}, time cost: {round(time_cost, 2)}s, tokens: {token_usage}")

    # 提取召回记忆
    query_memory, llm_memories = extract_retrieved_memories(data)
    memories_json = json.dumps(
        {"query_memory": query_memory, "llm_memory": llm_memories},
        ensure_ascii=False,
    )
    memories_text = build_memories_text(query_memory, llm_memories)

    return {
        "question_index": orig_idx,
        "question": question,
        "standard_answer": standard_answer,
        "reference_answer": reference_answer,
        "response": response,
        "time_cost": round(time_cost, 2),
        "result": "",
        "reasoning": "",
        "retrieved_memories_json": memories_json,
        "retrieved_memories_text": memories_text,
        "response_input_tokens": token_usage.get("prompt_tokens", ""),
        "response_output_tokens": token_usage.get("completion_tokens", ""),
    }


async def run_eval(args: argparse.Namespace) -> None:
    import httpx

    # 8. 加载 QA 数据
    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        raise SystemExit(1)

    qa_list = load_qa_from_csv(str(input_path), args.count)
    total = len(qa_list)
    print(f"Loaded {total} question(s) from {input_path}")
    print(f"OpenViking: {args.openviking_url}")
    print(f"User: {args.user_id}")

    # 按 question_index 过滤，保留原始 0-based 索引
    if args.question_index is not None:
        indices = [int(x) for x in args.question_index.split(",")]
        indices_set = set(indices)
        qa_list = [(i, qa) for i, qa in enumerate(qa_list) if i in indices_set]
        if not qa_list:
            print(f"No questions matched --question-index={args.question_index}")
            return
        print(f"Filtered to {len(qa_list)} question(s) by --question-index={args.question_index}")
    else:
        qa_list = list(enumerate(qa_list))

    output_path = Path(args.output).expanduser()
    os.makedirs(output_path.parent, exist_ok=True)

    # --force 时，先移除本次目标题目的旧结果，之后重新生成并追加新结果。
    force_indices = {orig_idx for orig_idx, _ in qa_list} if args.force else set()
    if force_indices and output_path.exists():
        kept_rows: list[dict] = []
        removed_count = 0
        with open(output_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                idx_str = row.get("question_index", "")
                try:
                    row_idx = int(idx_str)
                except ValueError:
                    row_idx = None
                if row_idx in force_indices:
                    removed_count += 1
                    continue
                kept_rows.append({field: row.get(field, "") for field in FIELDNAMES})

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(kept_rows)
        if removed_count:
            print(f"Force rerun: removed {removed_count} existing row(s) from {output_path}")

    # 读取已完成的 question_index，支持断点续跑
    completed_indices: set[int] = set()
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                idx_str = row.get("question_index", "")
                if idx_str != "":
                    try:
                        completed_indices.add(int(idx_str))
                    except ValueError:
                        pass
        if completed_indices:
            print(f"Resuming: {len(completed_indices)} question(s) already completed, skipping.")

    # 首次运行或文件为空时写入 CSV 表头
    if not output_path.exists() or output_path.stat().st_size == 0:
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()

    # 过滤掉已完成的题目
    pending = [(orig_idx, qa) for orig_idx, qa in qa_list if orig_idx not in completed_indices]
    if not pending:
        print("All questions already completed. Nothing to do.")
        return
    print(f"Processing {len(pending)} remaining question(s) out of {total}.")

    # 9. 并发处理，每题完成后立即写盘，中断不丢进度
    semaphore = asyncio.Semaphore(args.parallel)
    file_lock = asyncio.Lock()
    limits = httpx.Limits(
        max_connections=max(args.parallel, 1),
        max_keepalive_connections=max(args.parallel, 1),
    )

    async def process_and_save(client: httpx.AsyncClient, orig_idx: int, qa_item: dict) -> None:
        async with semaphore:
            row = await process_single_qa(
                qa_item,
                orig_idx,
                total,
                client=client,
                openviking_url=args.openviking_url,
                user_id=args.user_id,
                account=args.account,
                api_key=args.api_key,
                disable_memory_commit=not args.allow_memory_commit,
            )
        async with file_lock:
            with open(output_path, "a", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)

    async with httpx.AsyncClient(timeout=300.0, limits=limits) as client:
        await asyncio.gather(*[process_and_save(client, orig_idx, qa) for orig_idx, qa in pending])
    print(f"Evaluation completed, results saved to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Vaka QA evaluation using OpenViking /bot/v1/chat API"
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=str(Path("~/Downloads/vaka_judge.csv").expanduser()),
        help="Path to QA CSV file (with query/standard_answer columns)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Path to output result CSV, default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Maximum number of questions to evaluate, default: all",
    )
    parser.add_argument(
        "--question-index",
        default=None,
        help="Only run specific question indices, comma-separated (e.g. '0,3,7'). default: all",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        help="Number of concurrent requests, default: 5",
    )
    # OpenViking 配置
    parser.add_argument(
        "--openviking-url",
        default=DEFAULT_OPENVIKING_URL,
        help=f"OpenViking service URL, default: {DEFAULT_OPENVIKING_URL}",
    )
    parser.add_argument(
        "--user-id",
        default=DEFAULT_USER_ID,
        help=f"OpenViking user_id, default: {DEFAULT_USER_ID}",
    )
    parser.add_argument(
        "--account",
        default=DEFAULT_ACCOUNT,
        help=f"OpenViking account, default: {DEFAULT_ACCOUNT}",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="OpenViking API key (X-API-Key header), default: empty",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rerun selected questions and replace their existing rows in the output CSV.",
    )
    parser.add_argument(
        "--allow-memory-commit",
        action="store_true",
        help="Allow the bot to call openviking_memory_commit during eval. Default: disabled.",
    )
    args = parser.parse_args()

    asyncio.run(run_eval(args))


if __name__ == "__main__":
    main()