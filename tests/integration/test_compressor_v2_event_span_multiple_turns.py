#!/usr/bin/env python3
"""
OpenViking 记忆演示脚本 — 事件跨多个 turn 的测试
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

DISPLAY_NAME = "小明"
DEFAULT_URL = "http://localhost:1934"
PANEL_WIDTH = 78
DEFAULT_API_KEY = "1cf407c39990e5dc874ccc697942da4892208a86a44c4781396dfdc57aa5c98d"
DEFAULT_AGENT_ID = "test"
DEFAULT_SESSION_ID = "event-span-multiple-turns"


console = Console()

# ── 对话数据 (事件跨多个 turn) ─────────────────────────────────────────────
# 用户消息描述一个持续多轮的事件（如项目讨论、问题解决过程）
# 这里模拟一个产品需求讨论的事件，持续 4 个 user + assistant 轮次

CONVERSATION = [
    {
        "user": "我们公司要做一个新功能，是关于用户反馈系统的。我需要帮产品经理整理一下需求，你能帮我吗？",
        "assistant": "当然可以！你可以告诉我产品经理的具体需求，我会帮你记录和整理。",
    },
    {
        "user": "产品经理说这个反馈系统需要支持文字和图片上传，用户可以匿名提交，还需要有分类功能，比如分为 bug 反馈、功能建议、使用体验等。",
        "assistant": "好的，我已经记录了：支持文字和图片上传、匿名提交、分类功能（bug、建议、体验）。",
    },
    {
        "user": "还有，产品经理要求反馈系统要能实时通知，当用户提交反馈后，相关人员要能立即收到消息。另外，还需要有反馈处理进度的跟踪功能。",
        "assistant": "我补充了：实时通知功能、反馈处理进度跟踪。",
    },
    {
        "user": "最后，产品经理说要在下周之前完成需求文档的编写，然后开始开发。我现在需要把这些需求整理成一份清晰的文档。",
        "assistant": "明白了，你需要在下周前完成需求文档，然后开始开发。我会帮你记住这些关键点。",
    },
    {
        "user": "今天天气真好！我想下午去公园散步，顺便看看有没有好看的花。",
        "assistant": "天气好的时候去公园散步是个不错的选择。春天的公园应该有很多花盛开。",
    },
    {
        "user": "对了，我上周买的那本书还没看完。书名是《人类简史》，写得很有意思。我计划这个周末读完它。",
        "assistant": "《人类简史》确实是一本很有趣的书。周末读完应该是可行的。",
    },
    {
        "user": "我们项目的需求文档已经完成了，我昨天加班到很晚才写完。今天早上已经发给产品经理了，他说写得不错。",
        "assistant": "恭喜你完成了需求文档！产品经理认可你的工作，说明你写得很好。",
    },
    {
        "user": "产品经理说反馈系统的开发工作已经安排好了，下周一开始正式开发。我需要负责前端页面的设计和实现。",
        "assistant": "开发工作安排好了，下周一开始。你负责前端页面的设计和实现。",
    },
    {
        "user": "今天中午我和同事一起去吃了新开的那家日料店，味道很不错。刺身很新鲜，寿司也很好吃。",
        "assistant": "新开的日料店味道不错，刺身新鲜，寿司好吃。",
    },
    {
        "user": "反馈系统的前端页面已经设计好了，我昨天和设计师一起讨论了很久。现在需要开始写代码实现了。",
        "assistant": "前端页面设计完成，现在开始代码实现。",
    },
]

# ── 验证查询 ──────────────────────────────────────────────────────────────

VERIFY_QUERIES = [
    {
        "query": "反馈系统的功能需求",
        "expected_keywords": ["文字", "图片", "匿名", "分类", "通知", "进度", "需求文档"],
    },
    {
        "query": "反馈系统的开发计划",
        "expected_keywords": ["下周", "前端", "设计", "实现"],
    },
    {
        "query": "小明的其他活动",
        "expected_keywords": ["公园", "散步", "读书", "日料"],
    },
]

# ── 辅助函数 ──────────────────────────────────────────────────────────────


def run_ingest(client: ov.SyncHTTPClient, session_id: str, wait_seconds: float):
    """写入对话并提交"""
    console.print()
    console.rule(f"[bold]Phase 1: 写入对话 — {DISPLAY_NAME} ({len(CONVERSATION)} 轮)[/bold]")

    session = client.create_session()
    session_id = session.get('session_id')
    console.print(f"  Session: [bold cyan]{session_id}[/bold cyan]")
    console.print()

    # 设置一个测试用的会话时间（2023年4月2日）
    session_time = datetime(2023, 4, 2, 9, 36)
    session_time_str = session_time.isoformat()

    total = len(CONVERSATION)
    for i, turn in enumerate(CONVERSATION, 1):
        console.print(f"  [dim][{i}/{total}][/dim] 添加 user + assistant 消息...")
        client.add_message(session_id, role="user", parts=[{"type": "text", "text": turn["user"]}], created_at=session_time_str)
        client.add_message(session_id, role="assistant", parts=[{"type": "text", "text": turn["assistant"]}], created_at=session_time_str)

    console.print()
    console.print(f"  共添加 [bold]{total * 2}[/bold] 条消息")

    console.print()
    console.print("  [yellow]提交 Session（触发记忆抽取）...[/yellow]")
    commit_result = client.commit_session(session_id)
    task_id = commit_result.get("task_id")
    console.print(f"  Commit 结果: {commit_result}")

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

    console.print(f"  [yellow]等待向量化完成...[/yellow]")
    client.wait_processed()

    if wait_seconds > 0:
        console.print(f"  [dim]额外等待 {wait_seconds:.0f}s...[/dim]")
        time.sleep(wait_seconds)

    session_info = client.get_session(session_id)
    console.print(f"  Session 详情: {session_info}")

    return session_id


def run_verify(client: ov.SyncHTTPClient):
    """验证记忆召回"""
    console.print()
    console.rule(f"[bold]Phase 2: 验证记忆召回 — {DISPLAY_NAME} ({len(VERIFY_QUERIES)} 条查询)[/bold]")

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
                    console.print(f"    [dim]{text[:120]}...[/dim]" if len(text) > 120 else f"    [dim]{text}[/dim]")
                count += len(results.memories)

            if hasattr(results, "resources") and results.resources:
                for r in results.resources:
                    text = getattr(r, "content", "") or getattr(r, "text", "") or str(r)
                    print(f"  [DEBUG] resource text: {repr(text)}")
                    recall_texts.append(text)
                    console.print(
                        f"    [blue]Resource:[/blue] {r.uri} (score: {r.score:.4f})"
                    )
                count += len(results.resources)

            if hasattr(results, "skills") and results.skills:
                count += len(results.skills)

            all_text = " ".join(recall_texts)
            hits = [kw for kw in expected if kw in all_text]
            hit_str = ", ".join(hits) if hits else "[dim]无[/dim]"

            results_table.add_row(str(i), query, str(count), hit_str)

        except Exception as e:
            console.print(f"    [red]ERROR: {e}[/red]")
            results_table.add_row(str(i), query, "[red]ERR[/red]", str(e)[:40])

    console.print()
    console.print(results_table)


def main():
    """入口函数"""
    parser = argparse.ArgumentParser(description=f"OpenViking 记忆演示 — {DISPLAY_NAME}")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Server URL (默认: {DEFAULT_URL})")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key")
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID, help="Agent ID")
    parser.add_argument(
        "--phase",
        choices=["all", "ingest", "verify"],
        default="all",
        help="all=全部, ingest=仅写入, verify=仅验证 (默认: all)",
    )
    parser.add_argument(
        "--session-id", default=DEFAULT_SESSION_ID, help=f"Session ID (默认: {DEFAULT_SESSION_ID})"
    )
    parser.add_argument(
        "--wait", type=float, default=2, help="写入后等待秒数 (默认: 2)"
    )

    args = parser.parse_args()

    client = ov.SyncHTTPClient(
        url=args.url, api_key=args.api_key, agent_id=args.agent_id,
        timeout=180
    )

    try:
        client.initialize()
        console.print(f"  [green]已连接[/green] {args.url}")

        if args.phase in ("all", "ingest"):
            run_ingest(client, args.session_id, args.wait)

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
        console.print(
            Panel(f"[bold red]Error:[/bold red] {e}", style="red", width=PANEL_WIDTH)
        )


if __name__ == "__main__":
    main()
