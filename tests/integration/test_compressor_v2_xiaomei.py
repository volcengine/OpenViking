#!/usr/bin/env python3
"""
OpenViking 记忆演示脚本 — 用户: 小美（日常生活记录）
"""

import argparse
import time

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import openviking as ov

# ── 常量 ───────────────────────────────────────────────────────────────────

DISPLAY_NAME = "小美"
DEFAULT_URL = "http://localhost:1933"
PANEL_WIDTH = 78
DEFAULT_API_KEY = "1cf407c39990e5dc874ccc697942da4892208a86a44c4781396dfdc57aa5c98d"
DEFAULT_AGENT_ID = "test"
DEFAULT_SESSION_ID = "xiaomei-demo"



console = Console()

# ── 对话数据 (10 轮 user + assistant 模拟) ─────────────────────────────────
# user 消息取自原始 demo，assistant 消息为模拟回复（用于填充 session 上下文）

CONVERSATION = [
    {
        "user": "嗨！我是小美，刚毕业不久，现在在一家互联网公司做运营。我想找个能帮我记录日常生活的小助手，比如记一下每天发生的事情、我的想法、还有想做的事情。你能帮帮我吗？",
        "assistant": "你好小美！当然可以，我很乐意当你的生活小助手。你可以随时跟我聊天，我会帮你记住重要的事情。",
    },
    {
        "user": "今天心情还不错！早上在地铁上看到了一个超级可爱的小猫咪，它主人带着它坐车，只露出个小脑袋，太萌了！对了，今天部门开会说下个月要做 618 大促，我负责写活动文案，有点紧张，这是我第一次独立负责这么重要的项目。",
        "assistant": "看到可爱的猫咪真的很治愈！618 大促是个很好的锻炼机会，第一次独立负责确实会紧张，但这说明领导信任你。你可以先列个文案大纲，慢慢来。",
    },
    {
        "user": "说到吃，中午我跟同事小丽一起去吃了楼下那家麻辣烫，超级好吃！我喜欢多放醋和麻酱，不太能吃辣。不过最近在减肥，不敢吃太多主食。你有没有什么好吃又不胖的推荐呀？",
        "assistant": "麻辣烫多放醋和麻酱是很经典的搭配。减肥期间可以试试轻食沙拉，或者选麻辣烫里多加蔬菜和豆腐，少放粉丝和面条。",
    },
    {
        "user": "对了，我办了一张健身卡，就在我家小区旁边。上周去了一次，跑了 30 分钟步，还练了会儿瑜伽。结果第二天腿酸得不行，下楼都费劲。教练说让我每周去三次，我怕坚持不下来...",
        "assistant": "刚开始运动肌肉酸痛是正常的。不用一下子每周三次，先从每周两次开始适应，跑步和瑜伽交替练习，慢慢增加频率。",
    },
    {
        "user": "这个周末你有什么建议吗？我想跟我男朋友一起出去。我们之前想过去看樱花，但好像花期快过了。要不看电影？最近有什么好看的电影吗？或者去探店？我知道有一家咖啡馆好像很不错。",
        "assistant": "花期快过了的话可以抓紧最后的机会去看。也可以先去那家咖啡馆探店，下午再看场电影，安排一个轻松的约会日。",
    },
    {
        "user": "说起来，我平时喜欢追剧，尤其是那种甜宠剧，最近在看《归路》，太甜了！我还喜欢画画，虽然画得不太好，但挺解压的。偶尔也会看看书，最近在看《被讨厌的勇气》，挺有启发的。",
        "assistant": "甜宠剧确实很解压。画画也是个很好的放松方式，不用在意画得好不好。《被讨厌的勇气》是本好书，里面关于课题分离的理念很有用。",
    },
    {
        "user": "哎呀，我差点忘了提醒你！我对芒果过敏，吃了会起疹子。上次在公司同事给了我一个芒果蛋糕，我不知道，吃了一口就进医院了，还好不严重。还有，我每天晚上睡觉前都要喝一杯热牛奶，不然会失眠。",
        "assistant": "芒果过敏一定要注意，我记住了，以后推荐食物会避开芒果相关的。睡前喝热牛奶是个好习惯，有助于睡眠。",
    },
    {
        "user": "最近我种草了一个拍立得，就是富士的 mini12，粉色那款，颜值超级高！但有点贵，要 700 多块钱，还在犹豫要不要买。对了，我还想买一个投影仪，这样周末可以在家看电影。",
        "assistant": "富士 mini12 粉色确实很好看，适合记录生活。700 多块不算特别贵，可以等促销入手。投影仪也很实用，在家看电影很有氛围。",
    },
    {
        "user": "说到同事，小丽人超好，她说会帮我一起想 618 的文案点子。还有，我闺蜜下周要结婚了！她是我们宿舍第一个结婚的，真为她开心。我还在想送什么礼物好呢，红包肯定要包，但想再加点特别的。",
        "assistant": "小丽是个好搭档。闺蜜结婚的话，除了红包可以送一本定制相册，把你们大学的照片整理进去，很有纪念意义。",
    },
    {
        "user": "好的，谢谢你听我说了这么多！以后跟我聊天的时候，轻松一点就好，像朋友一样。如果我不开心了，多安慰安慰我；如果我开心，就跟我一起开心。对了，多给我推荐好吃的好玩的，谢谢啦！",
        "assistant": "没问题小美！以后就当朋友一样聊天，我会记住你的喜好和习惯。有什么开心的或不开心的都可以找我聊。",
    },
]

# ── 验证查询 ──────────────────────────────────────────────────────────────

VERIFY_QUERIES = [
    {
        "query": "小美的工作情况",
        "expected_keywords": ["618", "文案", "运营", "小丽"],
    },
    {
        "query": "小美的饮食偏好和注意事项",
        "expected_keywords": ["芒果过敏", "麻辣烫", "减肥"],
    },
    {
        "query": "小美的爱好",
        "expected_keywords": ["追剧", "画画", "看书"],
    },
    {
        "query": "小美想买什么",
        "expected_keywords": ["拍立得", "投影仪"],
    },
    {
        "query": "小美的运动计划",
        "expected_keywords": ["健身", "瑜伽", "跑步"],
    },
]


# ── Phase 1: 写入对话并提交 ────────────────────────────────────────────────


def run_ingest(client: ov.SyncHTTPClient, session_id: str, wait_seconds: float):
    console.print()
    console.rule(f"[bold]Phase 1: 写入对话 — {DISPLAY_NAME} ({len(CONVERSATION)} 轮)[/bold]")

    # 获取 session；若不存在则由服务端按 session_id 自动创建
    session= client.create_session()
    session_id = session.get('session_id')
    print(f'session_id={session_id}')
    console.print(f"  Session: [bold cyan]{session_id}[/bold cyan]")
    console.print()

    # 逐轮添加消息
    total = len(CONVERSATION)
    for i, turn in enumerate(CONVERSATION, 1):
        console.print(f"  [dim][{i}/{total}][/dim] 添加 user + assistant 消息...")
        client.add_message(session_id, role="user", parts=[{"type": "text", "text": turn["user"]}])
        client.add_message(session_id, role="assistant", parts=[{"type": "text", "text": turn["assistant"]}])

    console.print()
    console.print(f"  共添加 [bold]{total * 2}[/bold] 条消息")

    # 提交 session — 触发记忆抽取
    console.print()
    console.print("  [yellow]提交 Session（触发记忆抽取）...[/yellow]")
    commit_result = client.commit_session(session_id)
    task_id = commit_result.get("task_id")
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
    console.print(f"  [yellow]等待向量化完成...[/yellow]")
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
                    recall_texts.append(text)
                    uri = getattr(m, "uri", "")
                    score = getattr(m, "score", 0)
                    console.print(f"    [green]Memory:[/green] {uri} (score: {score:.4f})")
                    console.print(f"    [dim]{text[:120]}...[/dim]" if len(text) > 120 else f"    [dim]{text}[/dim]")
                count += len(results.memories)

            if hasattr(results, "resources") and results.resources:
                for r in results.resources:
                    text = getattr(r, "content", "") or getattr(r, "text", "") or str(r)
                    recall_texts.append(text)
                    console.print(
                        f"    [blue]Resource:[/blue] {r.uri} (score: {r.score:.4f})"
                    )
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
        "--wait", type=float, default=5.0, help="提交后额外等待秒数 (默认: 5)"
    )
    args = parser.parse_args()

    console.print(
        Panel(
            f"[bold]OpenViking 记忆演示 — {DISPLAY_NAME}[/bold]\n"
            f"Server: {args.url}  |  Phase: {args.phase}",
            style="magenta",
            width=PANEL_WIDTH,
        )
    )

    client = ov.SyncHTTPClient(
        url=args.url, api_key=args.api_key, agent_id=args.agent_id,
        timeout=180
    )

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
        console.print(
            Panel(f"[bold red]Error:[/bold red] {e}", style="red", width=PANEL_WIDTH)
        )
        import traceback

        traceback.print_exc()

    finally:
        client.close()


if __name__ == "__main__":
    main()