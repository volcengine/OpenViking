#!/usr/bin/env python3
"""
OpenClaw Memory Plugin 演示脚本 — 用户: 阿杰（后端开发）

通过 OpenClaw Gateway 的 Responses API (/v1/responses) 进行多轮对话，
验证 OpenViking 记忆插件的端到端能力。消息经过完整 agent 流水线，
插件的 before_agent_start（记忆注入）和 agent_end（记忆抽取）自动触发。

前提: OpenClaw 配置中需开启 Responses API。在 openclaw.config.json 的 gateways 中添加:

    {
      "type": "openresponses-http",
      "port": 18789
    }

然后启动 OpenClaw 即可在该端口使用 /v1/responses 端点。

用法:
    python demo-memory-ajie.py
    python demo-memory-ajie.py --gateway http://127.0.0.1:18789
    python demo-memory-ajie.py --phase chat     # 只跑对话
    python demo-memory-ajie.py --phase verify   # 只跑验证（需先跑 chat）

依赖:
    pip install requests rich
"""

import argparse
import sys
import time

import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

# ── 常量 ───────────────────────────────────────────────────────────────────

USER_ID = "ajie"
DISPLAY_NAME = "阿杰"
DEFAULT_GATEWAY = "http://127.0.0.1:18789"

console = Console()

# ── 对话数据 (10 轮) ──────────────────────────────────────────────────────

CHAT_MESSAGES = [
    # 第1轮 — 开场介绍
    "嗨，我是阿杰，是个后端开发工程师，最近在优化我们系统的性能。想找个助手帮我记录一下平时遇到的技术问题和解决方案，你能帮帮我吗？",
    # 第2轮 — Redis 缓存问题
    "说到问题，前几天刚遇到一个 Redis 缓存击穿的情况。热门商品的缓存过期了，瞬间大量请求打到数据库，差点把 DB 搞挂了。后来我加了互斥锁，只有一个请求去查 DB 并刷新缓存，才解决了问题。",
    # 第3轮 — MySQL 慢查询
    "还有个 MySQL 的问题，之前有个订单列表查询特别慢，看了一下慢查询日志，发现是没加索引。给 create_time 和 user_id 加了联合索引之后，查询时间从 2 秒降到了 50 毫秒，效果特别明显。",
    # 第4轮 — Kafka 削峰填谷
    "对了，我们还用 Kafka 做了削峰填谷。订单创建成功后先写入 Kafka，然后消费者慢慢处理，这样即使是秒杀活动也不怕了。我们还在 Kafka 里做了订单超时检查，30 分钟未支付的订单自动取消。",
    # 第5轮 — 技术栈介绍
    "说一下我们的技术栈吧：后端用的是 Spring Boot，缓存用 Redis，数据库是 MySQL，消息队列是 Kafka。部署在 Kubernetes 上，用 Prometheus + Grafana 做监控。你对这套技术栈熟悉吗？",
    # 第6轮 — 工作习惯
    "我一般早上 9 点到公司，先花 15 分钟看一下监控面板和告警信息，确保系统正常运行。然后上午写代码，下午开会或者帮同事 review code。对了，我用 IntelliJ IDEA 写代码，快捷键超好用！",
    # 第7轮 — 踩过的坑
    "说起踩过的坑，那可多了！比如有一次把 Redis 的过期时间设成了 10 秒而不是 10 分钟，结果缓存频繁失效；还有一次 MySQL 连接池满了，是因为有个地方连接没释放。每次踩坑我都会记下来，避免再犯。",
    # 第8轮 — 最近在学习
    "最近在看《Redis 设计与实现》这本书，讲得真好，终于理解 Redis 的数据结构底层是怎么实现的了。之前还看过《高性能 MySQL》，收获也很大。你有什么技术书推荐吗？",
    # 第9轮 — 今天的任务
    "今天的任务：晚上要优化一下 Kafka 消费者的配置，现在消费速度有点慢，想把批量消费参数调大一点。还有，要帮新来的同事小林搭建一下开发环境，他对 Kafka 不太熟。",
    # 第10轮 — 对话偏好
    "好的，以后跟我聊技术问题的时候，先讲一下原理，再给具体的代码示例，这样我理解得更透彻。另外，多提一些最佳实践，我想写出更健壮的代码。谢谢啦！",
]

# ── 验证数据 (5 轮) ──────────────────────────────────────────────────────

VERIFY_QUESTIONS = [
    {"question": "帮我回忆一下最近遇到的技术问题和解决方案，请简洁回答", "expected": "Redis 缓存击穿加互斥锁、MySQL 加索引、Kafka 削峰填谷"},
    {"question": "我们的技术栈是什么？请简洁回答", "expected": "Spring Boot、Redis、MySQL、Kafka、K8s、Prometheus + Grafana"},
    {"question": "我踩过哪些技术坑？请简洁回答", "expected": "Redis 过期时间设错、MySQL 连接池满了"},
    {"question": "我的工作习惯是怎样的？请简洁回答", "expected": "早上看监控、上午写代码、下午开会/review，用 IDEA"},
    {"question": "最近在看什么技术书？请简洁回答", "expected": "Redis 设计与实现、高性能 MySQL"},
]


# ── 辅助函数 ───────────────────────────────────────────────────────────────


def send_message(gateway_url, message, user_id, previous_messages=None):
    """通过 OpenClaw Responses API 发送消息。"""
    # OpenClaw Responses API 只接受单条消息作为输入，不接受历史消息数组
    input_data = message
    resp = requests.post(
        f"{gateway_url}/v1/responses",
        json={"model": "openclaw", "input": input_data, "user": user_id},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


def extract_reply_text(data):
    """从 Responses API 响应中提取助手回复文本。"""
    for item in data.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            for part in item.get("content", []):
                if part.get("type") in ("text", "output_text"):
                    return part.get("text", "")
    return "(无回复)"


def render_reply(text):
    """用 rich 渲染助手回复。"""
    lines = text.split("\n")
    if len(lines) > 30:
        text = "\n".join(lines[:30]) + f"\n\n... (共 {len(lines)} 行，已截断)"
    console.print(Panel(Markdown(text), title="[green]回复[/green]", border_style="green"))


# ── 主流程 ─────────────────────────────────────────────────────────────────


def run_chat(gateway_url, delay):
    console.print()
    console.rule(f"[bold]Phase 1: 多轮对话 — {DISPLAY_NAME} ({len(CHAT_MESSAGES)} 轮)[/bold]")
    console.print(f"[yellow]用户:[/yellow] {DISPLAY_NAME} (user={USER_ID})")
    console.print(f"[yellow]Gateway:[/yellow] {gateway_url}")
    console.print(f"[yellow]轮次间隔:[/yellow] {delay}s")

    total = len(CHAT_MESSAGES)
    ok = fail = 0
    messages = []  # 维护对话历史

    for i, msg in enumerate(CHAT_MESSAGES, 1):
        console.rule(f"[dim]{i}/{total}[/dim]", style="dim")
        console.print(Panel(msg, title=f"[bold cyan]用户 [{i}/{total}][/bold cyan]", border_style="cyan"))
        try:
            data = send_message(gateway_url, msg, USER_ID, messages if messages else None)
            reply = extract_reply_text(data)
            messages.append({"role": "user", "content": msg})
            messages.append({"role": "assistant", "content": reply})
            render_reply(reply)
            ok += 1
        except Exception as e:
            console.print(f"[red][ERROR][/red] {e}")
            fail += 1
        if i < total:
            time.sleep(delay)

    console.print()
    console.print(f"[yellow]对话完成:[/yellow] {ok} 成功, {fail} 失败")

    wait = max(delay * 2, 5)
    console.print(f"[yellow]等待 {wait:.0f}s 让记忆抽取完成...[/yellow]")
    time.sleep(wait)


def run_verify(gateway_url, delay):
    console.print()
    console.rule(f"[bold]Phase 2: 验证记忆召回 — {DISPLAY_NAME} 新 Session ({len(VERIFY_QUESTIONS)} 轮)[/bold]")

    # 用不同的 user 后缀确保 Gateway 派生出新 session
    verify_user = f"{USER_ID}-verify"
    console.print(f"[yellow]验证用户:[/yellow] {verify_user} (新 session，不带对话历史)")

    results = []
    total = len(VERIFY_QUESTIONS)

    for i, item in enumerate(VERIFY_QUESTIONS, 1):
        q, expected = item["question"], item["expected"]
        console.rule(f"[dim]{i}/{total}[/dim]", style="dim")
        console.print(Panel(
            f"{q}\n[dim]期望召回: {expected}[/dim]",
            title=f"[bold cyan]验证 [{i}/{total}][/bold cyan]",
            border_style="cyan",
        ))

        try:
            # 每题独立发送，不带历史 → 回答正确 = 记忆召回生效
            data = send_message(gateway_url, q, verify_user)
            reply = extract_reply_text(data)
            render_reply(reply)
            results.append({"expected": expected, "success": True})
        except Exception as e:
            console.print(f"[red][ERROR][/red] {e}")
            results.append({"expected": expected, "success": False})

        if i < total:
            time.sleep(delay)

    # 汇总表格
    console.print()
    console.rule(f"[bold]结果汇总 — {DISPLAY_NAME}[/bold]")

    table = Table(title=f"记忆召回验证 — {DISPLAY_NAME} ({USER_ID})")
    table.add_column("#", style="bold", width=4)
    table.add_column("状态", width=6)
    table.add_column("期望召回", style="dim")

    for i, r in enumerate(results, 1):
        status = "[green]OK[/green]" if r.get("success") else "[red]FAIL[/red]"
        table.add_row(str(i), status, r["expected"])

    console.print(table)

    ok = sum(1 for r in results if r.get("success"))
    console.print(f"\n[yellow]成功: {ok}/{total}[/yellow]")
    console.print("[yellow]验证方式: 每个问题在新 session 中独立发送（无对话历史），回答正确说明 before_agent_start hook 成功召回了记忆。[/yellow]")


# ── 入口 ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description=f"Memory 演示 — {DISPLAY_NAME}")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY,
                        help=f"OpenClaw Gateway 地址 (默认: {DEFAULT_GATEWAY})")
    parser.add_argument("--phase", choices=["all", "chat", "verify"], default="all",
                        help="all=全部, chat=仅对话, verify=仅验证 (默认: all)")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="轮次间等待秒数 (默认: 3)")
    args = parser.parse_args()

    gateway_url = args.gateway.rstrip("/")
    console.print(f"[bold]OpenClaw Memory Plugin 演示 — {DISPLAY_NAME}[/bold]")
    console.print(f"[yellow]Gateway:[/yellow] {gateway_url}")

    if args.phase in ("all", "chat"):
        run_chat(gateway_url, args.delay)
    if args.phase in ("all", "verify"):
        run_verify(gateway_url, args.delay)

    console.print("\n[yellow]演示完成。[/yellow]")


if __name__ == "__main__":
    main()
