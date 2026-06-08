#!/usr/bin/env python3
"""
OpenViking 记忆演示脚本 — 用户: 小王（5类工作方式及对应技能）
"""

import argparse
import time
from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import openviking as ov

# ── 常量 ───────────────────────────────────────────────────────────────────

DISPLAY_NAME = "小王"
DEFAULT_URL = "http://localhost:1934"
PANEL_WIDTH = 78
DEFAULT_API_KEY = "1cf407c39990e5dc874ccc697942da4892208a86a44c4781396dfdc57aa5c98d"
DEFAULT_SESSION_ID = "xiaowang-demo"


console = Console()

# ── 对话数据 ──────────────────────────────────────────────────────────────

CONVERSATION = [
    {
        "user": "小王的5类工作方式及对应技能：1. 小王内容账号复盘技能：B站复盘需结合数据+多维度分析+可复用选题经验，重点是数字+解释+可改建议，不混淆其他场景；2. 小王LLM早学技能：早7点左右30分钟学习，少术语多例子，含小练习和小产物，陪练语气、短学习、递进练习，非学院派；3. 小王家庭消费决策技能：数码购买分人群匹配需求，优先官方可靠渠道，重点说明适配性、原因、风险，不替下单；4. 小王建材门店宣传技能：短视频脚本朴实实用，核对门店真实信息，15秒内节奏，产品图与名称对应；5. 小王家庭健康轻量打卡技能：记录3-5项易填内容，温和趋势提醒，非诊断口吻，不编造医疗相关内容",
        "assistant": "好的，我已经记录了小王的5类工作方式及对应技能：内容账号复盘、LLM早学、家庭消费决策、建材门店宣传、家庭健康轻量打卡。每项技能的特点和注意事项都记下了。",
    },
]

# ── 验证查询 ──────────────────────────────────────────────────────────────

VERIFY_QUERIES = [
    {
        "query": "小王有哪些工作方式或技能",
        "expected_keywords": ["复盘", "LLM", "消费决策", "门店宣传", "打卡"],
    },
    {
        "query": "小王的LLM早学技能具体是什么",
        "expected_keywords": ["早7点", "少术语", "陪练", "递进"],
    },
    {
        "query": "小王做建材门店宣传时要注意什么",
        "expected_keywords": ["短视频", "15秒", "产品图"],
    },
]


# ── Phase 1: 写入对话并提交 ────────────────────────────────────────────────


def run_ingest(client: ov.SyncHTTPClient, session_id: str, wait_seconds: float):
    console.print()
    console.rule(f"[bold]Phase 1: 写入对话 — {DISPLAY_NAME} ({len(CONVERSATION)} 轮)[/bold]")

    # 获取 session；若不存在则由服务端按 session_id 自动创建
    session = client.create_session()
    session_id = session.get("session_id")
    print(f"session_id={session_id}")
    console.print(f"  Session: [bold cyan]{session_id}[/bold cyan]")
    console.print()

    # 设置一个测试用的会话时间
    session_time = datetime(2026, 5, 6, 3, 21)
    session_time_str = session_time.isoformat()

    # 逐轮添加消息
    total = len(CONVERSATION)
    for i, turn in enumerate(CONVERSATION, 1):
        console.print(f"  [dim][{i}/{total}][/dim] 添加 user + assistant 消息...")
        client.add_message(
            session_id,
            role="user",
            parts=[{"type": "text", "text": turn["user"]}],
            created_at=session_time_str,
        )
        client.add_message(
            session_id,
            role="assistant",
            parts=[{"type": "text", "text": turn["assistant"]}],
            created_at=session_time_str,
        )

    console.print()
    console.print(f"  共添加 [bold]{total * 2}[/bold] 条消息")

    # 提交 session — 触发记忆抽取
    console.print()
    console.print("  [yellow]提交 Session（触发记忆抽取）...[/yellow]")
    commit_result = client.commit_session(session_id)
    task_id = commit_result.get("task_id")
    trace_id = commit_result.get("trace_id")
    console.print(f"  [bold cyan]trace_id: {trace_id}[/bold cyan]")
    console.print(f"  Commit 结果: {commit_result}")

    # 轮询后台任务直到完成
    if task_id:
        now = time.time()
        console.print(f"  [yellow]等待记忆提取完成 (task_id={task_id})...[/yellow]")
        while True:
            task = client.get_task(task_id)
            if not task or task.get("status") in ("completed", "failed"):
                break
            time.sleep(1)
        elapsed = time.time() - now
        status = task.get("status", "unknown") if task else "not found"
        console.print(f"  [green]任务 {status}，耗时 {elapsed:.2f}s[/green]")
        console.print(f"  Task 详情: {task}")

    # 等待向量化队列处理完成
    console.print("  [yellow]等待向量化完成...[/yellow]")
    client.wait_processed()

    if wait_seconds > 0:
        console.print(f"  [dim]额外等待 {wait_seconds:.0f}s...[/dim]")
        time.sleep(wait_seconds)

    session_info = client.get_session(session_id)
    console.print(f"  Session 详情: {session_info}")

    return session_id


# ── Phase 2: 验证记忆召回 ─────────────────────────────────────────────────


def run_verify(client: ov.SyncHTTPClient):
    console.print()
    console.rule(
        f"[bold]Phase 2: 验证记忆召回 — {DISPLAY_NAME} ({len(VERIFY_QUERIES)} 条查询)[/bold]"
    )

    results_table = Table(
        title=f"记忆召回验证 — {DISPLAY_NAME}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    results_table.add_column("#", style="bold", width=4)
    results_table.add_column("查询", style="cyan", max_width=30)
    results_table.add_column("召回数", justify="center", width=8)
    results_table.add_column("命中关键词", style="green")

    total = len(VERIFY_QUERIES)
    for i, item in enumerate(VERIFY_QUERIES, 1):
        query = item["query"]
        expected = item["expected_keywords"]

        console.print(f"\n  [dim][{i}/{total}][/dim] 搜索: [cyan]{query}[/cyan]")
        console.print(f"  [dim]期望关键词: {', '.join(expected)}[/dim]")

        try:
            results = client.find(query, limit=5)

            # 收集所有召回内容
            recall_texts = []
            count = 0
            if hasattr(results, "memories") and results.memories:
                for m in results.memories:
                    text = getattr(m, "content", "") or getattr(m, "text", "") or str(m)
                    print(f"  [DEBUG] memory text: {repr(text)}")
                    recall_texts.append(text)
                    uri = getattr(m, "uri", "")
                    score = getattr(m, "score", 0)
                    console.print(f"    [green]Memory:[/green] {uri} (score: {score:.4f})")
                    console.print(
                        f"    [dim]{text[:120]}...[/dim]"
                        if len(text) > 120
                        else f"    [dim]{text}[/dim]"
                    )
                count += len(results.memories)

            if hasattr(results, "resources") and results.resources:
                for r in results.resources:
                    text = getattr(r, "content", "") or getattr(r, "text", "") or str(r)
                    print(f"  [DEBUG] resource text: {repr(text)}")
                    recall_texts.append(text)
                    console.print(f"    [blue]Resource:[/blue] {r.uri} (score: {r.score:.4f})")
                count += len(results.resources)

            if hasattr(results, "skills") and results.skills:
                count += len(results.skills)

            # 检查关键词命中
            all_text = " ".join(recall_texts)
            hits = [kw for kw in expected if kw in all_text]
            hit_str = ", ".join(hits) if hits else "[dim]无[/dim]"

            results_table.add_row(str(i), query, str(count), hit_str)

        except Exception as e:
            console.print(f"    [red]ERROR: {e}[/red]")
            results_table.add_row(str(i), query, "[red]ERR[/red]", str(e)[:40])

    console.print()
    console.print(results_table)


# ── 入口 ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description=f"OpenViking 记忆演示 — {DISPLAY_NAME}")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Server URL (默认: {DEFAULT_URL})")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key")
    parser.add_argument(
        "--phase",
        choices=["all", "ingest", "verify"],
        default="all",
        help="all=全部, ingest=仅写入, verify=仅验证 (默认: all)",
    )
    parser.add_argument(
        "--session-id", default=DEFAULT_SESSION_ID, help=f"Session ID (默认: {DEFAULT_SESSION_ID})"
    )
    parser.add_argument("--wait", type=float, default=5.0, help="提交后额外等待秒数 (默认: 5)")
    args = parser.parse_args()

    console.print(
        Panel(
            f"[bold]OpenViking 记忆演示 — {DISPLAY_NAME}[/bold]\n"
            f"Server: {args.url}  |  Phase: {args.phase}",
            style="magenta",
            width=PANEL_WIDTH,
        )
    )

    client = ov.SyncHTTPClient(url=args.url, api_key=args.api_key, timeout=180)

    try:
        client.initialize()
        console.print(f"  [green]已连接[/green] {args.url}")

        if args.phase in ("all", "ingest"):
            run_ingest(client, session_id=args.session_id, wait_seconds=args.wait)

        if args.phase in ("all", "verify"):
            run_verify(client)

        console.print(
            Panel(
                "[bold green]演示完成[/bold green]",
                style="green",
                width=PANEL_WIDTH,
            )
        )

    except Exception as e:
        console.print(Panel(f"[bold red]Error:[/bold red] {e}", style="red", width=PANEL_WIDTH))
        import traceback

        traceback.print_exc()

    finally:
        client.close()


if __name__ == "__main__":
    main()
