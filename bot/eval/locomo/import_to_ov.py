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
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

import openviking as ov
from openviking.message.part import TextPart


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


def format_locomo_message(msg: Dict[str, Any]) -> str:
    """Format a single LoCoMo message into a natural chat-style string.

    Output format:
        Speaker: text here
        image_url: caption
    """
    speaker = msg.get("speaker", "unknown")
    text = msg.get("text", "")
    line = f"{speaker}: {text}"

    img_urls = msg.get("img_url", [])
    if isinstance(img_urls, str):
        img_urls = [img_urls]
    blip = msg.get("blip_caption", "")

    if img_urls:
        for url in img_urls:
            caption = f": {blip}" if blip else ""
            line += f"\n{url}{caption}"
    elif blip:
        line += f"\n({blip})"

    return line


def load_locomo_data(
    path: str,
    sample_index: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Load LoCoMo JSON and optionally filter to one sample."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            raise ValueError(f"Sample index {sample_index} out of range (0-{len(data)-1})")
        return [data[sample_index]]
    return data


def build_session_messages(
    item: Dict[str, Any],
    session_range: Optional[Tuple[int, int]] = None,
) -> List[Dict[str, Any]]:
    """Build bundled session messages for one LoCoMo sample.

    Returns list of dicts with keys: message, meta.
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

        parts = [f"[group chat conversation: {date_time}]"]
        for msg in conv[sk]:
            parts.append(format_locomo_message(msg))
        combined = "\n\n".join(parts)

        sessions.append({
            "message": combined,
            "meta": {
                "sample_id": item["sample_id"],
                "session_key": sk,
                "date_time": date_time,
                "speakers": speakers,
            },
        })

    return sessions


# ---------------------------------------------------------------------------
# Ingest record helpers (avoid duplicate ingestion)
# ---------------------------------------------------------------------------

def load_success_csv(csv_path: str = "import_success.csv") -> set:
    """加载成功导入的CSV记录，返回已成功的键集合"""
    success_keys = set()
    if Path(csv_path).exists():
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = f"viking:{row['sample_id']}:{row['session']}"
                success_keys.add(key)
    return success_keys


def write_success_record(record: Dict[str, Any], csv_path: str = "import_success.csv") -> None:
    """写入成功记录到CSV文件"""
    file_exists = Path(csv_path).exists()
    fieldnames = ["timestamp", "sample_id", "session", "date_time", "speakers",
                 "embedding_tokens", "vlm_tokens", "llm_input_tokens",
                 "llm_output_tokens", "total_tokens"]

    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "timestamp": record["timestamp"],
            "sample_id": record["sample_id"],
            "session": record["session"],
            "date_time": record.get("meta", {}).get("date_time", ""),
            "speakers": record.get("meta", {}).get("speakers", ""),
            "embedding_tokens": record["token_usage"].get("embedding", 0),
            "vlm_tokens": record["token_usage"].get("vlm", 0),
            "llm_input_tokens": record["token_usage"].get("llm_input", 0),
            "llm_output_tokens": record["token_usage"].get("llm_output", 0),
            "total_tokens": record["token_usage"].get("total", 0)
        })


def write_error_record(record: Dict[str, Any], error_path: str = "import_errors.log") -> None:
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


def save_ingest_record(record: Dict[str, Any], record_path: str = "./result/.ingest_record.json") -> None:
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
def _parse_token_usage(token_data: Dict[str, Any]) -> Dict[str, int]:
    """解析Token使用数据（仅支持新版token_usage格式）"""
    usage = token_data["token_usage"]
    return {
        "embedding": usage["embedding"]["total_tokens"],
        "vlm": usage["llm"]["total_tokens"],
        "llm_input": usage["llm"]["prompt_tokens"],
        "llm_output": usage["llm"]["completion_tokens"],
        "total": usage["total"]["total_tokens"]
    }


async def viking_ingest(msg: str, openviking_url: str, semaphore: asyncio.Semaphore) -> Dict[str, int]:
    """Save a message to OpenViking via OpenViking SDK client.
    Returns token usage dict with embedding and vlm token counts.
    """
    # 使用信号量控制并发
    async with semaphore:
        # Create client
        client = ov.AsyncHTTPClient(url=openviking_url)
        await client.initialize()

        try:
            # Create session
            create_res = await client.create_session()
            session_id = create_res["session_id"]
            session = client.session(session_id)

            # Add message
            await session.add_message(
                role="user",
                parts=[TextPart(text=msg)]
            )

            # Commit
            result = await session.commit(telemetry=True)

            if not (result.get("status") == "accepted" and result.get("task_id")):
                raise RuntimeError(f"Commit failed: {result}")

            # 轮询等待异步任务完成
            task_id = result["task_id"]
            max_wait = 1200  # 最多等待20分钟
            waited = 0

            while waited < max_wait:
                task = await client.get_task(task_id)
                if task["status"] == "completed":
                    token_usage = _parse_token_usage(task["result"])
                    break
                elif task["status"] == "failed":
                    raise RuntimeError(f"Commit failed: {task.get('error', 'Unknown error')}")

                # 指数退避策略，避免频繁请求
                await asyncio.sleep(min(1 << (waited // 10), 60))
                waited += 1
            else:
                raise RuntimeError(f"Commit timed out after {max_wait} seconds")

            return token_usage

        finally:
            await client.close()


def sync_viking_ingest(msg: str, openviking_url: str) -> Dict[str, int]:
    """Synchronous wrapper for viking_ingest to maintain existing API."""
    semaphore = asyncio.Semaphore(1)  # 同步调用时使用信号量为1
    return asyncio.run(viking_ingest(msg, openviking_url, semaphore))


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
    msg: str,
    sample_id: str | int,
    session_key: str,
    meta: Dict[str, Any],
    run_time: str,
    ingest_record: Dict[str, Any],
    args: argparse.Namespace,
    semaphore: asyncio.Semaphore
) -> Dict[str, Any]:
    """处理单个会话的导入任务"""
    try:
        token_usage = await viking_ingest(msg, args.openviking_url, semaphore)
        print(f"    -> [SUCCESS] [{sample_id}/{session_key}] imported to OpenViking", file=sys.stderr)

        # Extract token counts
        embedding_tokens = token_usage.get("embedding", 0)
        vlm_tokens = token_usage.get("vlm", 0)
        print(f"    -> [USAGE] [{sample_id}/{session_key}] Embedding tokens: {embedding_tokens}, VLM tokens: {vlm_tokens}", file=sys.stderr)

        # Write success record
        result = {
            "timestamp": run_time,
            "sample_id": sample_id,
            "session": session_key,
            "status": "success",
            "meta": meta,
            "token_usage": token_usage,
            "embedding_tokens": embedding_tokens,
            "vlm_tokens": vlm_tokens
        }

        # 写入成功CSV
        write_success_record(result, args.success_csv)

        # Mark as successfully ingested
        mark_ingested(sample_id, session_key, ingest_record, meta)
        save_ingest_record(ingest_record)  # Save immediately after success

        return result

    except Exception as e:
        print(f"    -> [ERROR] [{sample_id}/{session_key}] {e}", file=sys.stderr)

        # Write error record
        result = {
            "timestamp": run_time,
            "sample_id": sample_id,
            "session": session_key,
            "status": "error",
            "error": str(e)
        }

        # 写入错误日志
        write_error_record(result, args.error_log)

        return result


async def run_import(args: argparse.Namespace) -> None:
    # 初始化信号量控制并发
    semaphore = asyncio.Semaphore(args.parallel)

    session_range = parse_session_range(args.sessions) if args.sessions else None

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
        print(f"[INFO] Loaded {len(success_keys)} existing success records from {args.success_csv}", file=sys.stderr)

    # Write run header
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    skipped_count = 0
    success_count = 0
    error_count = 0
    total_embedding_tokens = 0
    total_vlm_tokens = 0
    tasks: List[asyncio.Task] = []

    if args.input.endswith(".json"):
        # LoCoMo JSON format
        samples = load_locomo_data(args.input, args.sample)

        for item in samples:
            sample_id = item["sample_id"]
            sessions = build_session_messages(item, session_range)

            print(f"\n=== Sample {sample_id} ===", file=sys.stderr)
            print(f"    {len(sessions)} session(s) to import", file=sys.stderr)

            for sess in sessions:
                meta = sess["meta"]
                msg = sess["message"]
                session_key = meta["session_key"]
                label = f"{session_key} ({meta['date_time']})"

                # Skip already ingested sessions unless force-ingest is enabled
                if not args.force_ingest and is_already_ingested(sample_id, session_key, ingest_record, success_keys):
                    print(f"  [{label}] [SKIP] already imported (use --force-ingest to reprocess)", file=sys.stderr)
                    skipped_count += 1
                    continue

                preview = msg.replace("\n", " | ")[:80]
                print(f"  [{label}] {preview}...", file=sys.stderr)

                # 创建异步任务
                task = asyncio.create_task(
                    process_single_session(
                        msg=msg,
                        sample_id=sample_id,
                        session_key=session_key,
                        meta=meta,
                        run_time=run_time,
                        ingest_record=ingest_record,
                        args=args,
                        semaphore=semaphore
                    )
                )
                tasks.append(task)

    else:
        # Plain text format
        sessions = parse_test_file(args.input)
        print(f"Found {len(sessions)} session(s) in text file", file=sys.stderr)

        for idx, session in enumerate(sessions, start=1):
            session_key = f"txt-session-{idx}"
            print(f"\n=== Text Session {idx} ===", file=sys.stderr)

            # Skip already ingested sessions unless force-ingest is enabled
            if not args.force_ingest and is_already_ingested("txt", session_key, ingest_record, success_keys):
                print(f"  [SKIP] already imported (use --force-ingest to reprocess)", file=sys.stderr)
                skipped_count += 1
                continue

            combined_msg = "\n\n".join(session["messages"])
            preview = combined_msg.replace("\n", " | ")[:80]
            print(f"  {preview}...", file=sys.stderr)

            # 创建异步任务
            task = asyncio.create_task(
                process_single_session(
                    msg=combined_msg,
                    sample_id="txt",
                    session_key=session_key,
                    meta={"session_index": idx},
                    run_time=run_time,
                    ingest_record=ingest_record,
                    args=args,
                    semaphore=semaphore
                )
            )
            tasks.append(task)

    # 等待所有任务完成
    print(f"\n[INFO] Starting import with {args.parallel} concurrent workers, {len(tasks)} tasks to process", file=sys.stderr)
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 统计结果
    for result in results:
        if isinstance(result, Exception):
            error_count += 1
            print(f"[UNEXPECTED ERROR] Task failed with exception: {result}", file=sys.stderr)
            continue

        if result["status"] == "success":
            success_count += 1
            total_embedding_tokens += result["embedding_tokens"]
            total_vlm_tokens += result["vlm_tokens"]
        elif result["status"] == "error":
            error_count += 1

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
        print(f"Average Embedding per session: {total_embedding_tokens // success_count}", file=sys.stderr)
        print(f"Average VLM per session: {total_vlm_tokens // success_count}", file=sys.stderr)
    print(f"\nResults saved to:", file=sys.stderr)
    print(f"  - Success records: {args.success_csv}", file=sys.stderr)
    print(f"  - Error logs: {args.error_log}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import conversations into OpenViking")
    parser.add_argument(
        "--input",
        default="./test_data/locomo10.json",
        help="Path to input file (.txt or LoCoMo .json)"
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