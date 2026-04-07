"""
OpenViking data import tool.

Import conversations from LoCoMo JSON or plain text files into OpenViking memory.

Usage:
    # Import LoCoMo JSON conversations
    uv run python import_to_ov.py locomo10.json --sample 0 --sessions 1-4

    # Import plain text conversations
    uv run python import_to_ov.py example.txt
"""

import argparse
import asyncio
import csv
import json
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

import openviking as ov


def _get_session_number(session_key: str) -> int:
    """Extract session number from session key."""
    return int(session_key.split("_")[1])


def parse_test_file(path: str) -> List[Dict[str, Any]]:
    """Parse txt test file into sessions.

    Each session is a dict with:
        - messages: list of user message strings
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    raw_sessions = content.split("---\n")
    sessions = []

    for raw in raw_sessions:
        lines = [line for line in raw.strip().splitlines() if line.strip()]
        if not lines:
            continue

        messages = []
        for line in lines:
            if not line.startswith("eval:"):  # Skip eval lines
                messages.append(line)

        if messages:
            sessions.append({"messages": messages})

    return sessions


def load_locomo_data(
    path: str,
    sample_index: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Load LoCoMo JSON and optionally filter to one sample."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            raise ValueError(f"Sample index {sample_index} out of range (0-{len(data) - 1})")
        return [data[sample_index]]
    return data


def build_session_messages(
    item: Dict[str, Any],
    session_range: Optional[Tuple[int, int]] = None,
) -> List[Dict[str, Any]]:
    """Build session messages for one LoCoMo sample.

    Returns list of dicts with keys: messages, meta.
    Each dict represents a session with multiple messages (user/assistant role).
    """
    conv = item["conversation"]
    speakers = f"{conv['speaker_a']} & {conv['speaker_b']}"

    session_keys = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("_date_time")],
        key=_get_session_number,
    )

    sessions = []
    for sk in session_keys:
        sess_num = _get_session_number(sk)
        if session_range:
            lo, hi = session_range
            if sess_num < lo or sess_num > hi:
                continue

        dt_key = f"{sk}_date_time"
        date_time = conv.get(dt_key, "")

        # Extract messages with all as user role, including speaker in content
        messages = []
        for idx, msg in enumerate(conv[sk]):
            speaker = msg.get("speaker", "unknown")
            text = msg.get("text", "")
            messages.append(
                {"role": "user", "text": f"[{speaker}]: {text}", "speaker": speaker, "index": idx}
            )

        sessions.append(
            {
                "messages": messages,
                "meta": {
                    "sample_id": item["sample_id"],
                    "session_key": sk,
                    "date_time": date_time,
                    "speakers": speakers,
                },
            }
        )

    return sessions


# ---------------------------------------------------------------------------
# Ingest record helpers (avoid duplicate ingestion)
# ---------------------------------------------------------------------------


def load_success_csv(csv_path: str = "./result/import_success.csv") -> set:
    """加载成功导入的CSV记录，返回已成功的键集合"""
    success_keys = set()
    if Path(csv_path).exists():
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = f"viking:{row['sample_id']}:{row['session']}"
                success_keys.add(key)
    return success_keys


def write_success_record(
    record: Dict[str, Any], csv_path: str = "./result/import_success.csv"
) -> None:
    """写入成功记录到CSV文件"""
    file_exists = Path(csv_path).exists()
    fieldnames = [
        "timestamp",
        "sample_id",
        "session",
        "date_time",
        "speakers",
        "embedding_tokens",
        "vlm_tokens",
        "llm_input_tokens",
        "llm_output_tokens",
        "total_tokens",
    ]

    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                "timestamp": record["timestamp"],
                "sample_id": record["sample_id"],
                "session": record["session"],
                "date_time": record.get("meta", {}).get("date_time", ""),
                "speakers": record.get("meta", {}).get("speakers", ""),
                "embedding_tokens": record["token_usage"].get("embedding", 0),
                "vlm_tokens": record["token_usage"].get("vlm", 0),
                "llm_input_tokens": record["token_usage"].get("llm_input", 0),
                "llm_output_tokens": record["token_usage"].get("llm_output", 0),
                "total_tokens": record["token_usage"].get("total", 0),
            }
        )


def write_error_record(
    record: Dict[str, Any], error_path: str = "./result/import_errors.log"
) -> None:
    """写入错误记录到日志文件"""
    with open(error_path, "a", encoding="utf-8") as f:
        timestamp = record["timestamp"]
        sample_id = record["sample_id"]
        session = record["session"]
        error = record["error"]
        f.write(f"[{timestamp}] ERROR [{sample_id}/{session}]: {error}\n")


def load_ingest_record(record_path: str = "./result/.ingest_record.json") -> Dict[str, Any]:
    """Load existing ingest record file, return empty dict if not exists."""
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_ingest_record(
    record: Dict[str, Any], record_path: str = "./result/.ingest_record.json"
) -> None:
    """Save ingest record to file."""
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)


def is_already_ingested(
    sample_id: str | int,
    session_key: str,
    record: Dict[str, Any],
    success_keys: Optional[set] = None,
) -> bool:
    """Check if a specific session has already been successfully ingested."""
    key = f"viking:{sample_id}:{session_key}"
    if success_keys is not None and key in success_keys:
        return True
    return key in record and record[key].get("success", False)


def mark_ingested(
    sample_id: str | int,
    session_key: str,
    record: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Mark a session as successfully ingested."""
    key = f"viking:{sample_id}:{session_key}"
    record[key] = {
        "success": True,
        "timestamp": int(time.time()),
        "meta": meta or {},
    }


# ---------------------------------------------------------------------------
# OpenViking import
# ---------------------------------------------------------------------------
def _parse_token_usage(commit_result: Dict[str, Any]) -> Dict[str, int]:
    """解析Token使用数据（从commit返回的telemetry或task result中提取）"""
    # 尝试从 task result 中提取（task 完成后包含完整 token_usage）
    if "result" in commit_result:
        result = commit_result["result"]
        if "token_usage" in result:
            tu = result["token_usage"]
            embedding = tu.get("embedding", {})
            llm = tu.get("llm", {})
            # embedding 格式可能是 {"total": N} 或 {"total_tokens": N}
            embed_total = embedding.get("total", embedding.get("total_tokens", 0))
            llm_total = llm.get("total", llm.get("total_tokens", 0))
            return {
                "embedding": embed_total,
                "vlm": llm_total,
                "llm_input": llm.get("input", 0),
                "llm_output": llm.get("output", 0),
                "total": tu.get("total", {}).get("total_tokens", embed_total + llm_total),
            }

    # 从 commit 响应的 telemetry 中提取
    telemetry = commit_result.get("telemetry", {}).get("summary", {})
    tokens = telemetry.get("tokens", {})
    return {
        "embedding": tokens.get("embedding", {}).get("total", 0),
        "vlm": tokens.get("llm", {}).get("total", 0),
        "llm_input": tokens.get("llm", {}).get("input", 0),
        "llm_output": tokens.get("llm", {}).get("output", 0),
        "total": tokens.get("total", 0),
    }


async def viking_ingest(
    messages: List[Dict[str, Any]],
    openviking_url: str,
    session_time: Optional[str] = None,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, int]:
    """Save messages to OpenViking via OpenViking SDK client.
    Returns token usage dict with embedding and vlm token counts.

    Args:
        messages: List of message dicts with role and text
        openviking_url: OpenViking service URL
        session_time: Session time string (e.g., "9:36 am on 2 April, 2023")
        user_id: User identifier for separate userspace (e.g., "conv-26")
        agent_id: Agent identifier for separate agentspace (e.g., "conv-26")
    """
    # 解析 session_time - 为每条消息计算递增的时间戳
    base_datetime = None
    if session_time:
        try:
            base_datetime = datetime.strptime(session_time, "%I:%M %p on %d %B, %Y")
        except ValueError:
            print(f"Warning: Failed to parse session_time: {session_time}", file=sys.stderr)

    # Create client
    client = ov.AsyncHTTPClient(
        url=openviking_url,
        user=user_id,
        agent_id=agent_id,
    )
    await client.initialize()

    try:
        # Create session
        create_res = await client.create_session()
        session_id = create_res["session_id"]

        # Add messages one by one with created_at
        for idx, msg in enumerate(messages):
            msg_created_at = None
            if base_datetime:
                # 每条消息递增1秒，确保时间顺序
                msg_dt = base_datetime + timedelta(seconds=idx)
                msg_created_at = msg_dt.isoformat()

            await client.add_message(
                session_id=session_id,
                role=msg["role"],
                parts=[{"type": "text", "text": msg["text"]}],
                created_at=msg_created_at,
            )

        # Commit
        result = await client.commit_session(session_id, telemetry=True)

        # Accept both "committed" and "accepted" as success - accepted means the session was archived
        if result.get("status") not in ("committed", "accepted"):
            raise RuntimeError(f"Commit failed: {result}")

        # 等待 task 完成以获取准确 token 消耗
        task_id = result.get("task_id")
        if task_id:
            # 轮询任务状态直到完成
            max_attempts = 1200  # 最多等待20分钟
            for attempt in range(max_attempts):
                task = await client.get_task(task_id)
                status = task.get("status") if task else "unknown"
                if status == "completed":
                    token_usage = _parse_token_usage(task)
                    break
                elif status in ("failed", "cancelled", "unknown"):
                    raise RuntimeError(f"Task {task_id} {status}: {task}")
                await asyncio.sleep(1)
            else:
                raise RuntimeError(f"Task {task_id} timed out after {max_attempts} attempts")
        else:
            token_usage = {"embedding": 0, "vlm": 0, "total": 0}

        # Get trace_id from commit result
        trace_id = result.get("trace_id", "")
        return {"token_usage": token_usage, "task_id": task_id, "trace_id": trace_id}

    finally:
        await client.close()


def sync_viking_ingest(
    messages: List[Dict[str, Any]], openviking_url: str, session_time: Optional[str] = None
) -> Dict[str, int]:
    """Synchronous wrapper for viking_ingest to maintain existing API."""
    return asyncio.run(viking_ingest(messages, openviking_url, session_time))

# ---------------------------------------------------------------------------
# Main import logic
# ---------------------------------------------------------------------------


def parse_session_range(s: str) -> Tuple[int, int]:
    """Parse '1-4' or '3' into (lo, hi) inclusive tuple."""
    if "-" in s:
        lo, hi = s.split("-", 1)
        return int(lo), int(hi)
    n = int(s)
    return n, n


async def process_single_session(
    messages: List[Dict[str, Any]],
    sample_id: str | int,
    session_key: str,
    meta: Dict[str, Any],
    run_time: str,
    ingest_record: Dict[str, Any],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    """处理单个会话的导入任务"""
    try:
        # 使用 sample_id 作为 user_id 和 agent_id，实现独立的 userspace/agentspace
        result = await viking_ingest(
            messages,
            args.openviking_url,
            meta.get("date_time"),
            user_id=str(sample_id),
            agent_id=str(sample_id),
        )
        token_usage = result["token_usage"]
        task_id = result.get("task_id")
        trace_id = result.get("trace_id", "")
        embedding_tokens = token_usage.get("embedding", 0)
        vlm_tokens = token_usage.get("vlm", 0)
        print(
            f"    -> [COMPLETED] [{sample_id}/{session_key}] embed={embedding_tokens}, vlm={vlm_tokens}, task_id={task_id}, trace_id={trace_id}",
            file=sys.stderr,
        )

        # Write success record
        result = {
            "timestamp": run_time,
            "sample_id": sample_id,
            "session": session_key,
            "status": "success",
            "meta": meta,
            "token_usage": token_usage,
            "embedding_tokens": embedding_tokens,
            "vlm_tokens": vlm_tokens,
            "task_id": task_id,
            "trace_id": trace_id,
        }

        # 写入成功CSV
        write_success_record(result, args.success_csv)

        # Mark as successfully ingested
        mark_ingested(sample_id, session_key, ingest_record, meta)
        save_ingest_record(ingest_record)  # Save immediately after success

        return result

    except Exception as e:
        print(f"    -> [ERROR] [{sample_id}/{session_key}] {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

        # Write error record
        result = {
            "timestamp": run_time,
            "sample_id": sample_id,
            "session": session_key,
            "status": "error",
            "error": str(e),
        }

        # 写入错误日志
        write_error_record(result, args.error_log)

        return result


async def run_import(args: argparse.Namespace) -> None:
    session_range = parse_session_range(args.sessions) if args.sessions else None

    # 如果指定了 question-index，自动从 evidence 推断需要的 session
    if args.question_index is not None and not args.sessions:
        # 加载数据获取 question 的 evidence
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 获取 sample
        sample_idx = args.sample if args.sample is not None else 0
        if sample_idx < 0 or sample_idx >= len(data):
            raise ValueError(f"sample index {sample_idx} out of range")
        sample = data[sample_idx]

        # 获取 question 的 evidence
        qa_items = sample.get("qa", [])
        if args.question_index < 0 or args.question_index >= len(qa_items):
            raise ValueError(f"question index {args.question_index} out of range")
        qa = qa_items[args.question_index]
        evidence_list = qa.get("evidence", [])

        # 从 evidence 提取 session 号 (D1:3 -> session 1)
        session_nums = set()
        for ev in evidence_list:
            try:
                # D1:3 -> session 1
                sess_num = int(ev.split(":")[0][1:])
                session_nums.add(sess_num)
            except (ValueError, IndexError):
                pass

        if session_nums:
            min_sess = min(session_nums)
            max_sess = max(session_nums)
            session_range = (min_sess, max_sess)
            print(
                f"[INFO] Auto-detected sessions from evidence: {min_sess}-{max_sess}",
                file=sys.stderr,
            )

    # Handle ingest record operations
    if args.clear_ingest_record:
        ingest_record = {}
        save_ingest_record(ingest_record)
        print(f"[INFO] All existing ingest records cleared", file=sys.stderr)
    else:
        ingest_record = load_ingest_record()

    # 加载成功CSV记录用于去重
    success_keys = set()
    if not args.force_ingest:
        success_keys = load_success_csv(args.success_csv)
        print(
            f"[INFO] Loaded {len(success_keys)} existing success records from {args.success_csv}",
            file=sys.stderr,
        )

    # Write run header
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    skipped_count = 0
    success_count = 0
    error_count = 0
    total_embedding_tokens = 0
    total_vlm_tokens = 0

    if args.input.endswith(".json"):
        # LoCoMo JSON format
        samples = load_locomo_data(args.input, args.sample)

        # 为每个 sample 创建独立的处理协程
        async def process_sample(item):
            sample_id = item["sample_id"]
            sessions = build_session_messages(item, session_range)

            print(f"\n=== Sample {sample_id} ===", file=sys.stderr)
            print(f"    {len(sessions)} session(s) to import", file=sys.stderr)

            # 同一 sample 内串行处理所有 sessions
            for sess in sessions:
                meta = sess["meta"]
                messages = sess["messages"]
                session_key = meta["session_key"]
                label = f"{session_key} ({meta['date_time']})"

                # Skip already ingested sessions unless force-ingest is enabled
                if not args.force_ingest and is_already_ingested(
                    sample_id, session_key, ingest_record, success_keys
                ):
                    print(
                        f"  [{label}] [SKIP] already imported (use --force-ingest to reprocess)",
                        file=sys.stderr,
                    )
                    continue

                # Preview messages
                preview = " | ".join(
                    [f"{msg['role']}: {msg['text'][:30]}..." for msg in messages[:3]]
                )
                print(f"  [{label}] {preview}", file=sys.stderr)

                # 串行执行（等待完成后再处理下一个 session）
                await process_single_session(
                    messages=messages,
                    sample_id=sample_id,
                    session_key=session_key,
                    meta=meta,
                    run_time=run_time,
                    ingest_record=ingest_record,
                    args=args,
                )

        # 不同 sample 之间并行执行
        tasks = [asyncio.create_task(process_sample(item)) for item in samples]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    else:
        # Plain text format
        sessions = parse_test_file(args.input)
        print(f"Found {len(sessions)} session(s) in text file", file=sys.stderr)

        for idx, session in enumerate(sessions, start=1):
            session_key = f"txt-session-{idx}"
            print(f"\n=== Text Session {idx} ===", file=sys.stderr)

            # Skip already ingested sessions unless force-ingest is enabled
            if not args.force_ingest and is_already_ingested(
                "txt", session_key, ingest_record, success_keys
            ):
                print(
                    f"  [SKIP] already imported (use --force-ingest to reprocess)", file=sys.stderr
                )
                skipped_count += 1
                continue

            # For plain text, all messages as user role
            messages = []
            for i, text in enumerate(session["messages"]):
                messages.append(
                    {"role": "user", "text": text.strip(), "speaker": "user", "index": i}
                )

            preview = " | ".join([f"{msg['role']}: {msg['text'][:30]}..." for msg in messages[:3]])
            print(f"  {preview}", file=sys.stderr)

            # 创建异步任务
            task = asyncio.create_task(
                process_single_session(
                    messages=messages,
                    sample_id="txt",
                    session_key=session_key,
                    meta={"session_index": idx},
                    run_time=run_time,
                    ingest_record=ingest_record,
                    args=args,
                )
            )
            tasks.append(task)

    # 等待所有 sample 处理完成
    print(
        f"\n[INFO] Starting import with {args.parallel} concurrent workers, {len(tasks)} tasks to process",
        file=sys.stderr,
    )
    await asyncio.gather(*tasks, return_exceptions=True)

    # 从成功 CSV 统计结果
    if Path(args.success_csv).exists():
        with open(args.success_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                success_count += 1
                total_embedding_tokens += int(row.get("embedding_tokens", 0) or 0)
                total_vlm_tokens += int(row.get("vlm_tokens", 0) or 0)

    # Final summary
    total_processed = success_count + error_count + skipped_count
    print(f"\n=== Import summary ===", file=sys.stderr)
    print(f"Total sessions: {total_processed}", file=sys.stderr)
    print(f"Successfully imported: {success_count}", file=sys.stderr)
    print(f"Failed: {error_count}", file=sys.stderr)
    print(f"Skipped (already imported): {skipped_count}", file=sys.stderr)
    print(f"\n=== Token usage summary ===", file=sys.stderr)
    print(f"Total Embedding tokens: {total_embedding_tokens}", file=sys.stderr)
    print(f"Total VLM tokens: {total_vlm_tokens}", file=sys.stderr)
    if success_count > 0:
        print(
            f"Average Embedding per session: {total_embedding_tokens // success_count}",
            file=sys.stderr,
        )
        print(f"Average VLM per session: {total_vlm_tokens // success_count}", file=sys.stderr)
    print(f"\nResults saved to:", file=sys.stderr)
    print(f"  - Success records: {args.success_csv}", file=sys.stderr)
    print(f"  - Error logs: {args.error_log}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    # 基于脚本所在目录计算默认数据文件路径
    script_dir = Path(__file__).parent.resolve()
    default_input = str(script_dir / ".." / "data" / "locomo10.json")

    parser = argparse.ArgumentParser(description="Import conversations into OpenViking")
    parser.add_argument(
        "--input",
        default=default_input,
        help="Path to input file (.txt or LoCoMo .json)",
    )
    parser.add_argument(
        "--success-csv",
        default="./result/import_success.csv",
        help="Path to success records CSV file (default: import_success.csv)",
    )
    parser.add_argument(
        "--error-log",
        default="./result/import_errors.log",
        help="Path to error log file (default: import_errors.log)",
    )
    parser.add_argument(
        "--openviking-url",
        default="http://localhost:1933",
        help="OpenViking service URL (default: http://localhost:1933)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        help="Number of concurrent import workers (default: 5)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="LoCoMo JSON: sample index (0-based). Default: all samples.",
    )
    parser.add_argument(
        "--sessions",
        default=None,
        help="LoCoMo JSON: session range, e.g. '1-4' or '3'. Default: all sessions.",
    )
    parser.add_argument(
        "--question-index",
        type=int,
        default=None,
        help="LoCoMo JSON: question index (0-based). When specified, auto-detect required sessions from question's evidence.",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        default=False,
        help="Force re-import even if already recorded as completed",
    )
    parser.add_argument(
        "--clear-ingest-record",
        action="store_true",
        default=False,
        help="Clear all existing ingest records before running",
    )
    args = parser.parse_args()

    # 确保输出目录存在
    Path(args.success_csv).parent.mkdir(parents=True, exist_ok=True)
    Path(args.error_log).parent.mkdir(parents=True, exist_ok=True)

    try:
        asyncio.run(run_import(args))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
