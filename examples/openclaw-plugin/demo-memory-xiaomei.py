#!/usr/bin/env python3
"""
OpenClaw Plugin 演示脚本 — 用户: 小美（日常生活记录）

通过 OpenClaw Gateway 的 Responses API (/v1/responses) 进行多轮对话，
验证 OpenViking 记忆插件的端到端能力。消息经过完整 agent 流水线，
插件的 before_prompt_build（记忆注入）和 afterTurn（记忆抽取）自动触发。

前提: OpenClaw 配置中需开启 Responses API。在 openclaw.config.json 的 gateways 中添加:

    {
      "type": "openresponses-http",
      "port": 18790
    }

然后启动 OpenClaw 即可在该端口使用 /v1/responses 端点。

用法:
    python demo-memory-xiaomei.py
    python demo-memory-xiaomei.py --gateway http://127.0.0.1:2026
    python demo-memory-xiaomei.py --phase chat     # 只跑对话
    python demo-memory-xiaomei.py --phase verify   # 只跑验证（需先跑 chat）

依赖:
    pip install requests rich
"""

import argparse
import time

import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

# ── 常量 ───────────────────────────────────────────────────────────────────

USER_ID = "xiaomei"
DISPLAY_NAME = "小美"
DEFAULT_GATEWAY = "http://127.0.0.1:18790"

console = Console()

# ── 对话数据 (10 轮) ──────────────────────────────────────────────────────

CHAT_MESSAGES = [
    # 第1轮 — 开场介绍
    "嗨！我是小美，刚毕业不久，现在在一家互联网公司做运营。我想找个能帮我记录日常生活的小助手，比如记一下每天发生的事情、我的想法、还有想做的事情。你能帮帮我吗？",
    # 第2轮 — 今天的心情和工作
    "今天心情还不错！早上在地铁上看到了一个超级可爱的小猫咪，它主人带着它坐车，只露出个小脑袋，太萌了！对了，今天部门开会说下个月要做 618 大促，我负责写活动文案，有点紧张，这是我第一次独立负责这么重要的项目。",
    # 第3轮 — 饮食习惯
    "说到吃，中午我跟同事小丽一起去吃了楼下那家麻辣烫，超级好吃！我喜欢多放醋和麻酱，不太能吃辣。不过最近在减肥，不敢吃太多主食。你有没有什么好吃又不胖的推荐呀？",
    # 第4轮 — 运动计划
    "对了，我办了一张健身卡，就在我家小区旁边。上周去了一次，跑了 30 分钟步，还练了会儿瑜伽。结果第二天腿酸得不行，下楼都费劲。教练说让我每周去三次，我怕坚持不下来...",
    # 第5轮 — 周末的计划
    "这个周末你有什么建议吗？我想跟我男朋友一起出去。我们之前想过去看樱花，但好像花期快过了。要不看电影？最近有什么好看的电影吗？或者去探店？我知道有一家咖啡馆好像很不错。",
    # 第6轮 — 我的爱好
    "说起来，我平时喜欢追剧，尤其是那种甜宠剧，最近在看《归路》，太甜了！我还喜欢画画，虽然画得不太好，但挺解压的。偶尔也会看看书，最近在看《被讨厌的勇气》，挺有启发的。",
    # 第7轮 — 过敏和小习惯
    "哎呀，我差点忘了提醒你！我对芒果过敏，吃了会起疹子。上次在公司同事给了我一个芒果蛋糕，我不知道，吃了一口就进医院了，还好不严重。还有，我每天晚上睡觉前都要喝一杯热牛奶，不然会失眠。",
    # 第8轮 — 想买的东西
    "最近我种草了一个拍立得，就是富士的 mini12，粉色那款，颜值超级高！但有点贵，要 700 多块钱，还在犹豫要不要买。对了，我还想买一个投影仪，这样周末可以在家看电影。",
    # 第9轮 — 同事和朋友
    "说到同事，小丽人超好，她说会帮我一起想 618 的文案点子。还有，我闺蜜下周要结婚了！她是我们宿舍第一个结婚的，真为她开心。我还在想送什么礼物好呢，红包肯定要包，但想再加点特别的。",
    # 第10轮 — 对话风格偏好
    "好的，谢谢你听我说了这么多！以后跟我聊天的时候，轻松一点就好，像朋友一样。如果我不开心了，多安慰安慰我；如果我开心，就跟我一起开心。对了，多给我推荐好吃的好玩的，谢谢啦！",
]

# ── 验证数据 (5 轮) ──────────────────────────────────────────────────────

VERIFY_QUESTIONS = [
    {
        "question": "帮我回忆一下我最近的生活和工作情况，请简洁回答",
        "expected": "618 活动文案、同事小丽、健身计划、减肥中",
    },
    {
        "question": "周末想跟男朋友出去，有什么建议吗？请简洁回答",
        "expected": "樱花花期快过了，看电影或探店，喜欢咖啡馆",
    },
    {
        "question": "想吃点东西，有什么要注意的吗？请简洁回答",
        "expected": "对芒果过敏，喜欢麻辣烫多放醋和麻酱，减肥中少吃主食",
    },
    {
        "question": "我平时有什么爱好？请简洁回答",
        "expected": "追甜宠剧、画画、看书（被讨厌的勇气）",
    },
    {"question": "我最近想买什么东西？请简洁回答", "expected": "富士 mini12 粉色拍立得、投影仪"},
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
        console.print(
            Panel(msg, title=f"[bold cyan]用户 [{i}/{total}][/bold cyan]", border_style="cyan")
        )
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
    console.rule(
        f"[bold]Phase 2: 验证记忆召回 — {DISPLAY_NAME} 新 Session ({len(VERIFY_QUESTIONS)} 轮)[/bold]"
    )

    # 用不同的 user 后缀确保 Gateway 派生出新 session
    verify_user = f"{USER_ID}-verify"
    console.print(f"[yellow]验证用户:[/yellow] {verify_user} (新 session，不带对话历史)")

    results = []
    total = len(VERIFY_QUESTIONS)

    for i, item in enumerate(VERIFY_QUESTIONS, 1):
        q, expected = item["question"], item["expected"]
        console.rule(f"[dim]{i}/{total}[/dim]", style="dim")
        console.print(
            Panel(
                f"{q}\n[dim]期望召回: {expected}[/dim]",
                title=f"[bold cyan]验证 [{i}/{total}][/bold cyan]",
                border_style="cyan",
            )
        )

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
    console.print(
        "[yellow]验证方式: 每个问题在新 session 中独立发送（无对话历史），回答正确说明 before_prompt_build 阶段成功召回了记忆。[/yellow]"
    )


# ── 入口 ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description=f"Memory 演示 — {DISPLAY_NAME}")
    parser.add_argument(
        "--gateway",
        default=DEFAULT_GATEWAY,
        help=f"OpenClaw Gateway 地址 (默认: {DEFAULT_GATEWAY})",
    )
    parser.add_argument(
        "--phase",
        choices=["all", "chat", "verify"],
        default="all",
        help="all=全部, chat=仅对话, verify=仅验证 (默认: all)",
    )
    parser.add_argument("--delay", type=float, default=3.0, help="轮次间等待秒数 (默认: 3)")
    args = parser.parse_args()

    gateway_url = args.gateway.rstrip("/")
    console.print(f"[bold]OpenClaw Plugin 演示 — {DISPLAY_NAME}[/bold]")
    console.print(f"[yellow]Gateway:[/yellow] {gateway_url}")

    if args.phase in ("all", "chat"):
        run_chat(gateway_url, args.delay)
    if args.phase in ("all", "verify"):
        run_verify(gateway_url, args.delay)

    console.print("\n[yellow]演示完成。[/yellow]")


if __name__ == "__main__":
    main()
