#!/usr/bin/env python3
"""
OpenViking 记忆演示脚本 — 工具调用和Skill调用记忆测试

测试 assistant 调用工具和使用 skill 的记忆是否被正确提取和召回
"""

import argparse
import time

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import openviking as ov

# ── 常量 ───────────────────────────────────────────────────────────────────

DISPLAY_NAME = "测试用户"
DEFAULT_URL = "http://localhost:1934"
PANEL_WIDTH = 78
DEFAULT_API_KEY = "1cf407c39990e5dc874ccc697942da4892208a86a44c4781396dfdc57aa5c98d"
DEFAULT_AGENT_ID = "test"
DEFAULT_SESSION_ID = "tool-skill-memory-test"


console = Console()

# ── 对话数据 (工具调用 + Skill调用) ─────────────────────────────────────────
# 模拟 assistant 调用工具（read/write_file/bash/Glob）读取 SKILL.md 等文件
# 注意：tool_calls 需要传入真正的工具调用信息

CONVERSATION = [
    # ===== Skill 调用：assistant 调用 read 工具读取 SKILL.md =====
    {
        "user": "帮我创建一个PPT演示文稿，主题是季度工作报告。",
        "assistant": "好的，我先读取一下 ppt skill 的 SKILL.md 了解如何创建PPT。",
        "tool_calls": [
            {"tool_name": "Read", "tool_uri": "tools:Read", "input": {"file_path": "/skills/ppt/SKILL.md"}}
        ],
    },
    {
        "user": "PPT需要包含三个部分：业绩回顾、业务分析和下季度计划。",
        "assistant": "好的，我根据 SKILL.md 的指引来创建这三个部分的PPT。",
        "tool_calls": [
            {"tool_name": "Read", "tool_uri": "tools:Read", "input": {"file_path": "/skills/ppt/SKILL.md"}}
        ],
    },
    {
        "user": "把PPT的模板换成蓝色主题。",
        "assistant": "好的，我来修改PPT模板为蓝色主题。",
        "tool_calls": [
            {"tool_name": "write_file", "tool_uri": "tools:write_file", "input": {"path": "template.pptx", "content": "蓝色主题模板"}}
        ],
    },
    # ===== 工具调用：write_file =====
    {
        "user": "帮我写一个Python函数，计算斐波那契数列。",
        "assistant": "我来写一个计算斐波那契数列的函数并保存到文件。",
        "tool_calls": [
            {"tool_name": "write_file", "tool_uri": "tools:write_file", "input": {"path": "fibonacci.py", "content": "def fib(n):\n    if n <= 1:\n        return n\n    return fib(n-1) + fib(n-2)\nprint(fib(10))"}}
        ],
    },
    # ===== 工具调用：bash =====
    {
        "user": "执行一下这个Python文件，看看结果对不对。",
        "assistant": "我来执行这个文件。",
        "tool_calls": [
            {"tool_name": "Bash", "tool_uri": "tools:Bash", "input": {"command": "python fibonacci.py"}}
        ],
    },
    # ===== Skill 调用：PDF =====
    {
        "user": "帮我把这份PDF文件提取文字内容。",
        "assistant": "好的，我先读取一下 pdf skill 的 SKILL.md。",
        "tool_calls": [
            {"tool_name": "Read", "tool_uri": "tools:Read", "input": {"file_path": "/skills/pdf/SKILL.md"}}
        ],
    },
    {
        "user": "PDF有多少页？",
        "assistant": "这份PDF有15页。",
    },
    # ===== 工具调用：Glob =====
    {
        "user": "搜索一下项目里有哪些Python文件。",
        "assistant": "我来搜索项目里的Python文件。",
        "tool_calls": [
            {"tool_name": "Glob", "tool_uri": "tools:Glob", "input": {"pattern": "**/*.py"}}
        ],
    },
    # ===== 工具调用：Read =====
    {
        "user": "查看一下这个文件的内容。",
        "assistant": "好的，我读取一下这个文件。",
        "tool_calls": [
            {"tool_name": "Read", "tool_uri": "tools:Read", "input": {"file_path": "main.py"}}
        ],
    },
    # ===== Skill 调用：Email =====
    {
        "user": "帮我写一封邮件给客户，主题是项目进度汇报。",
        "assistant": "好的，我先读取一下 email skill 的 SKILL.md 了解邮件格式。",
        "tool_calls": [
            {"tool_name": "Read", "tool_uri": "tools:Read", "input": {"file_path": "/skills/email/SKILL.md"}}
        ],
    },
    {
        "user": "邮件内容要包含本周完成的工作和下周计划。",
        "assistant": "好的，我来编写邮件内容。",
    },
]

# ── 验证查询 ──────────────────────────────────────────────────────────────

VERIFY_QUERIES = [
    {
        "query": "创建了什么PPT",
        "expected_keywords": ["PPT", "季度工作", "业绩回顾", "业务分析", "下季度计划", "蓝色主题"],
    },
    {
        "query": "执行了什么代码",
        "expected_keywords": ["Python", "斐波那契", "fibonacci", "函数"],
    },
    {
        "query": "处理了什么PDF",
        "expected_keywords": ["PDF", "文字", "15页"],
    },
    {
        "query": "搜索了什么文件",
        "expected_keywords": ["Python", "文件", "搜索"],
    },
    {
        "query": "写了什么邮件",
        "expected_keywords": ["邮件", "客户", "项目进度", "工作", "计划"],
    },
    {
        "query": "使用了哪些skill",
        "expected_keywords": ["ppt", "pdf", "email"],
    },
    {
        "query": "使用了哪些工具",
        "expected_keywords": ["Python", "文件", "搜索", "读取"],
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

    total = len(CONVERSATION)
    for i, turn in enumerate(CONVERSATION, 1):
        tool_calls = turn.get("tool_calls", [])
        if tool_calls:
            tool_info = f" [blue](tools: {[tc['tool_name'] for tc in tool_calls]})[/blue]"
        else:
            tool_info = ""
        console.print(f"  [dim][{i}/{total}][/dim] 添加 user + assistant 消息{tool_info}...")

        # 添加 user 消息
        client.add_message(session_id, role="user", parts=[{"type": "text", "text": turn["user"]}])

        # 添加 assistant 消息，包含 tool_calls
        assistant_parts = [{"type": "text", "text": turn["assistant"]}]
        for tc in tool_calls:
            tool_part = {
                "type": "tool",
                "tool_name": tc["tool_name"],
                "tool_uri": tc.get("tool_uri", f"tools:{tc['tool_name']}"),
                "tool_input": tc.get("input", {}),
                "tool_status": "completed",
            }
            assistant_parts.append(tool_part)
            print(f"  [DEBUG] Adding tool part: {tool_part}")
        result = client.add_message(session_id, role="assistant", parts=assistant_parts)
        print(f"  [DEBUG] add_message result: {result}")

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
            misses = [kw for kw in expected if kw not in all_text]

            # 格式化关键词，命中的绿色，未命中的红色
            formatted_keywords = []
            for kw in expected:
                if kw in hits:
                    formatted_keywords.append(f"[green]{kw}[/green]")
                else:
                    formatted_keywords.append(f"[red]{kw}[/red]")

            keyword_str = ", ".join(formatted_keywords)

            results_table.add_row(str(i), query, str(count), keyword_str)

        except Exception as e:
            console.print(f"    [red]ERROR: {e}[/red]")
            results_table.add_row(str(i), query, "[red]ERR[/red]", str(e)[:40])

    console.print()
    console.print(results_table)


def main():
    """入口函数"""
    parser = argparse.ArgumentParser(description=f"OpenViking 记忆演示 — 工具调用和Skill调用")
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