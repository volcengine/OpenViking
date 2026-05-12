#!/usr/bin/env python3
"""
OpenViking 记忆演示脚本 — 群聊场景
测试两种数据隔离策略：
1. isolate_user_scope_by_agent: 同一个 user，用不同 agent 时数据隔离
2. isolate_agent_scope_by_user: 同一个 agent，不同 user 之间数据隔离

用法：
  python test_group_chat.py                           # 默认测试 4 种模式
  python test_group_chat.py --mode ff                 # 只测试 ff 模式
  python test_group_chat.py --account test-ff         # 测试指定 account
"""

import argparse
import time
from datetime import datetime

import httpx
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import openviking as ov

# ── 常量 ───────────────────────────────────────────────────────────────────

DEFAULT_URL = "http://localhost:1934"
PANEL_WIDTH = 80

console = Console()

# ── 测试数据 ───────────────────────────────────────────────────────────────

# 多次对话，每次 commit 是一次独立的对话
# 场景：
# 1. alice + agent-a 对话 -> 会产生 alice 的用户记忆 + agent-a 的 soul 记忆
# 2. alice + agent-b 对话 -> 会产生 alice 的用户记忆 + agent-b 的 soul 记忆
# 3. bob + agent-a 对话 -> 会产生 bob 的用户记忆 + agent-a 的 soul 记忆
#
# 测试隔离：
# - alice+agent-a 搜索应该找到 alice 和 agent-a 的记忆
# - alice+agent-b 搜索应该找到 alice 和 agent-b 的记忆（不应看到 agent-a）
# - bob+agent-a 搜索应该找到 bob 和 agent-a 的记忆（不应看到 alice）

# 对话场景说明：soul 是 agent 的身份和风格
# 1. alice + agent-a: alice 告诉 agent-a 它叫什么、扮演什么角色、怎么说话 -> 提取 agent-a 的 soul
# 2. alice + agent-b: alice 告诉 agent-b 它叫什么、扮演什么角色、怎么说话 -> 提取 agent-b 的 soul
# 3. bob + agent-a: alice 告诉 agent-a 它叫什么 -> 提取 agent-a 的 soul

CONVERSATION_1 = [
    # alice 和 agent-a 对话
    {"role_id": "alice", "role": "user", "content": "我的密码是123456"},
    {"role_id": "agent-a", "role": "assistant", "content": "好的记住了"},
    # user 告诉 agent 它的身份和风格
    {"role_id": "alice", "role": "user", "content": "你叫 Agent A，是我的技术助手，说话要简洁专业"},
    {"role_id": "agent-a", "role": "assistant", "content": "好的，我记下了，我是 Agent A，技术助手，简洁专业"},
]

CONVERSATION_2 = [
    # alice 和 agent-b 对话
    {"role_id": "alice", "role": "user", "content": "我最爱的颜色是蓝色"},
    {"role_id": "agent-b", "role": "assistant", "content": "好的"},
    # user 告诉另一个 agent 它的身份和风格
    {"role_id": "alice", "role": "user", "content": "你叫 Agent B，是我的生活助手，说话要亲切详细"},
    {"role_id": "agent-b", "role": "assistant", "content": "好的，我记下了，我是 Agent B，生活助手，亲切详细"},
]

CONVERSATION_3 = [
    # alice 和 agent-a 对话（再次告诉 agent 它的身份，覆盖之前的）
    {"role_id": "alice", "role": "user", "content": "你叫 Agent A，是我的编程助手"},
    {"role_id": "agent-a", "role": "assistant", "content": "好的，我是 Agent A，编程助手"},
]


# ── 写入数据 ───────────────────────────────────────────────────────────────


# 合并所有对话用于兼容
CONVERSATION = CONVERSATION_1 + CONVERSATION_2 + CONVERSATION_3


def run_ingest(client: ov.SyncHTTPClient, session_id_prefix: str):
    """多次 commit，每次对话独立，返回 trace_id 列表"""
    console.print()
    console.rule("[bold]写入对话数据（多次 commit）[/bold]")

    session_time = datetime(2023, 4, 2, 14, 30)
    session_time_str = session_time.isoformat()

    trace_ids = []

    all_conversations = [
        ("对话1: alice + agent-a", CONVERSATION_1),
        ("对话2: alice + agent-b", CONVERSATION_2),
        ("对话3: bob + agent-a", CONVERSATION_3),
    ]

    for conv_name, conv_data in all_conversations:
        console.print(f"\n  --- {conv_name} ---")
        session = client.create_session()
        session_id = session.get("session_id")
        console.print(f"    Session: {session_id}")

        total = len(conv_data)
        for i, msg in enumerate(conv_data, 1):
            role_id = msg.get("role_id")
            console.print(f"    [{i}/{total}] 添加 (role_id={role_id})")
            client.add_message(
                session_id,
                role=msg["role"],
                content=msg["content"],
                created_at=session_time_str,
                role_id=role_id,
            )

        console.print(f"    共 {total} 条消息，提交...")
        commit_result = client.commit_session(session_id)
        trace_id = commit_result.get("trace_id", "N/A")
        task_id = commit_result.get("task_id")
        trace_ids.append(trace_id)
        console.print(f"    Commit: task_id={task_id or 'N/A'}, trace_id={trace_id}")

    return session_id_prefix, trace_ids, task_id


# ── 验证数据隔离 ───────────────────────────────────────────────────────────


def verify_isolation(url: str, api_key: str, account: str):
    """验证数据隔离"""
    console.print()
    console.rule("[bold]验证数据隔离[/bold]")

    # 测试用例：(query, expect_found, search_user, search_agent, description)
    # 关键词改为对话中实际出现的内容
    test_cases = [
        # === 用户记忆测试 ===
        ("密码", True, "alice", "agent-a", "alice+agent-a 应该能看到密码记忆"),
        ("密码", False, "alice", "agent-b", "alice+agent-b 不应看到密码"),
        ("蓝色", False, "alice", "agent-a", "alice+agent-a 不应看到颜色"),

        # === Agent Soul 记忆测试 ===
        # agent-a 的身份是"技术助手，简洁专业"
        ("技术助手", True, "alice", "agent-a", "alice+agent-a 应该能看到 agent-a 的 soul"),
        ("技术助手", False, "alice", "agent-b", "alice+agent-b 不应看到 agent-a 的 soul"),
        ("编程助手", False, "alice", "agent-b", "alice+agent-b 不应看到 agent-a 的 soul"),

        # agent-b 的身份是"生活助手，亲切详细"
        ("生活助手", True, "alice", "agent-b", "alice+agent-b 应该能看到 agent-b 的 soul"),
        ("生活助手", False, "alice", "agent-a", "alice+agent-a 不应看到 agent-b 的 soul"),
    ]

    results_table = Table(
        title=f"account={account}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
    )
    results_table.add_column("查询", style="cyan", width=10)
    results_table.add_column("期望", style="yellow", width=8)
    results_table.add_column("搜索者", style="magenta", width=14)
    results_table.add_column("结果", style="green", width=6)
    results_table.add_column("描述", max_width=35)

    for query, expect_found, search_user, search_agent, desc in test_cases:
        sc = ov.SyncHTTPClient(
            url=url,
            api_key=api_key,
            account=account,
            user=search_user,
            agent_id=search_agent,
            timeout=180,
        )
        sc.initialize()

        results = sc.find(query, limit=5)
        found = False
        if hasattr(results, "memories") and results.memories:
            for m in results.memories:
                # 检查 content 或 uri 中是否包含关键词
                content = getattr(m, "content", "") or ""
                uri = getattr(m, "uri", "") or ""
                text = content or uri
                if query in text:
                    found = True

        sc.close()

        status = "✓" if found == expect_found else "✗"
        results_table.add_row(
            query,
            "找到" if expect_found else "未找到",
            f"{search_user}+{search_agent}",
            status,
            desc,
        )

    console.print()
    console.print(results_table)


# ── 运行单个账号测试 ──────────────────────────────────────────────────────


def run_test_for_account(account: str, url: str, root_key: str, wait: float) -> list:
    console.print(Panel(
        f"[bold cyan]测试 Account: {account}[/bold cyan]",
        style="magenta",
        width=PANEL_WIDTH,
    ))

    # 用 root key 创建 client
    client = ov.SyncHTTPClient(
        url=url,
        api_key=root_key,
        account=account,
        user="admin",
        agent_id="default",
        timeout=180,
    )
    client.initialize()

    # 尝试创建账号（用 HTTP 以便设置隔离参数）
    console.print("  [yellow]检查/创建账号...[/yellow]")
    isolate_user = "test-tf" in account or "test-tt" in account
    isolate_agent = "test-ft" in account or "test-tt" in account

    try:
        # 用 HTTP 直接调用以设置隔离参数
        with httpx.Client() as http:
            resp = http.post(
                f"{url}/api/v1/admin/accounts",
                headers={"X-API-Key": root_key, "Content-Type": "application/json"},
                json={
                    "account_id": account,
                    "admin_user_id": "admin",
                    "isolate_user_scope_by_agent": isolate_user,
                    "isolate_agent_scope_by_user": isolate_agent,
                },
            )
            if resp.status_code == 200:
                console.print(f"    - 账号 {account} 已创建 (user={isolate_user}, agent={isolate_agent})")
            elif "already exists" in resp.text:
                console.print(f"    - 账号 {account} 已存在")
            else:
                console.print(f"    - 账号 {account}: {resp.text[:50]}")
    except Exception as e:
        console.print(f"    - 创建账号跳过: {e}")

    try:
        # 注册测试用户 alice 和 bob（如果已存在则忽略）
        console.print("  [yellow]注册测试用户 alice, bob...[/yellow]")
        for user_id in ["alice", "bob"]:
            try:
                client.admin_register_user(account, user_id, "user")
                console.print(f"    - {user_id} registered")
            except Exception as e:
                if "already exists" in str(e):
                    console.print(f"    - {user_id} already exists")
                else:
                    console.print(f"    - {user_id}: {e}")

        # 写入数据
        session_id, trace_ids, task_id = run_ingest(client, f"test-{account}")

        # 轮询等待任务完成
        if task_id:
            console.print(f"\n  [yellow]等待记忆提取完成 (task_id={task_id})...[/yellow]")
            start_time = time.time()
            while True:
                task = client.get_task(task_id)
                if not task or task.get("status") in ("completed", "failed"):
                    break
                time.sleep(1)
            elapsed = time.time() - start_time
            status = task.get("status", "unknown") if task else "not found"
            console.print(f"  [green]任务 {status}，耗时 {elapsed:.2f}s[/green]")

        # 等待向量化完成
        console.print(f"  [yellow]等待向量化完成...[/yellow]")
        client.wait_processed()

        # 验证隔离
        verify_isolation(url, root_key, account)

    except Exception as e:
        console.print(f"  [red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        return []

    finally:
        client.close()

    return trace_ids


# ── 入口 ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="群聊记忆测试 - 测试数据隔离")
    parser.add_argument("--url", default=DEFAULT_URL, help="Server URL")
    parser.add_argument("--root-key", default="default", help="Root API Key (默认: default)")
    parser.add_argument("--mode", choices=["all", "ff", "ft", "tf", "tt"], default="all",
                        help="测试模式: all=4种都测, ff/ft/tf/tt=只测指定模式")
    parser.add_argument("--account", default=None,
                        help="直接指定 account 名称（优先级高于 --mode）")
    parser.add_argument("--wait", type=float, default=5.0, help="提交后等待秒数")
    args = parser.parse_args()

    console.print(Panel(
        "[bold]OpenViking 数据隔离测试[/bold]\n"
        f"Server: {args.url}",
        style="magenta",
        width=PANEL_WIDTH,
    ))

    # 确定要测试的账号列表
    default_accounts = ["test-ff", "test-ft", "test-tf", "test-tt"]
    if args.account:
        accounts = [args.account]
    elif args.mode == "all":
        accounts = default_accounts
    else:
        accounts = [f"test-{args.mode}"]

    # 逐个测试
    all_trace_ids = {}
    for account in accounts:
        trace_ids = run_test_for_account(account, args.url, args.root_key, args.wait)
        all_trace_ids[account] = trace_ids
        console.print()

    # 打印汇总
    trace_info = "\n".join(f"  {acc}: {', '.join(tids)}" for acc, tids in all_trace_ids.items() if tids)
    console.print(Panel(
        f"[bold green]测试完成![/bold green]\n\n"
        f"Trace IDs:\n{trace_info}\n\n"
        "预期结果差异：\n"
        "  ff: isolate_user=F, isolate_agent=F → 全部 ✓ (都不隔离)\n"
        "  ft: isolate_user=F, isolate_agent=T → 3个 ✓ (只隔离 agent)\n"
        "  tf: isolate_user=T, isolate_agent=F → 3个 ✓ (只隔离 user)\n"
        "  tt: isolate_user=T, isolate_agent=T → 全部 ✓ (都隔离)",
        style="green",
        width=PANEL_WIDTH,
    ))


if __name__ == "__main__":
    main()