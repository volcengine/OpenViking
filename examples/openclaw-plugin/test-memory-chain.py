#!/usr/bin/env python3
"""
OpenClaw 记忆链路完整测试脚本

验证 OpenViking 记忆插件重构后的端到端链路:
1. afterTurn: 本轮消息无损写入 OpenViking session，sessionId 一致
2. commit: 归档消息 + 提取长期记忆 + .meta.json 写入
3. assemble: 同用户继续对话时, 从 latest_archive_overview + active messages 重组上下文
4. assemble budget trimming: 小 token budget 下 latest_archive_overview 被裁剪
5. sessionId 一致性: 整条链路使用统一的 sessionId (无 sessionKey)
6. 新用户记忆召回: 验证 before_prompt_build auto-recall

测试流程:
Phase 1: 多轮对话 (12 轮) — afterTurn 写入
Phase 2: afterTurn 验证 — 检查 OV session 内部状态
Phase 3: Commit 验证 — 触发 commit, 检查归档结构
Phase 4: Assemble 验证 — 同用户继续对话, 验证上下文重组
Phase 5: SessionId 一致性验证
Phase 6: 新用户记忆召回

前提:
- OpenViking 服务已启动 (默认 http://127.0.0.1:8000)
- OpenClaw Gateway 已启动并配置了 OpenViking 插件

用法:
    python test-memory-chain.py
    python test-memory-chain.py --gateway http://127.0.0.1:18790 --openviking http://127.0.0.1:8000
    python test-memory-chain.py --phase chat
    python test-memory-chain.py --phase afterTurn
    python test-memory-chain.py --phase commit
    python test-memory-chain.py --phase assemble
    python test-memory-chain.py --phase session-id
    python test-memory-chain.py --phase recall
    python test-memory-chain.py --verbose

依赖:
    pip install requests rich
"""

import argparse
import json
import time
import uuid
from datetime import datetime
from typing import Any

import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

# ── 常量 ───────────────────────────────────────────────────────────────────

USER_ID = f"test-chain-{uuid.uuid4().hex[:8]}"
DISPLAY_NAME = "测试用户"
DEFAULT_GATEWAY = "http://127.0.0.1:18790"
DEFAULT_OPENVIKING = "http://127.0.0.1:8000"
AGENT_ID = "openclaw"

console = Console()

# ── 测试结果收集 ──────────────────────────────────────────────────────────

assertions: list[dict] = []


def check(label: str, condition: bool, detail: str = ""):
    """记录一个断言结果。"""
    assertions.append({"label": label, "ok": condition, "detail": detail})
    icon = "[green]✓[/green]" if condition else "[red]✗[/red]"
    msg = f"  {icon} {label}"
    if detail:
        msg += f"  [dim]({detail})[/dim]"
    console.print(msg)


# ── 对话数据 ──────────────────────────────────────────────────────────────

CHAT_MESSAGES = [
    "你好，我是一个软件工程师，我叫张明，在一家科技公司工作。我主要负责后端服务开发，使用的技术栈是 Python 和 Go。最近我们在重构一个订单系统，遇到了不少挑战。",
    "关于订单系统的问题，主要是性能瓶颈。我们发现在高峰期，数据库连接池经常被耗尽。目前用的是 PostgreSQL，连接池大小设置的是100，但每秒峰值请求量有5000。你有什么建议吗？",
    "谢谢你的建议。我还想问一下，我们目前的缓存策略用的是 Redis，但缓存击穿的问题很严重。热点数据过期后，大量请求直接打到数据库。我们尝试过加互斥锁，但性能下降很多。",
    "对了，关于代码风格，我们团队更倾向于使用函数式编程的思想，尽量避免副作用。变量命名用 snake_case，文档用中文写。代码审查很严格，每个 PR 至少需要两人 review。",
    "说到工作流程，我们每天早上9点站会，周三下午技术分享会。我一般上午写代码，下午处理 code review 和会议。晚上如果不加班，会看看技术书籍或者写写博客。",
    "我最近在学习分布式系统的设计，正在看《数据密集型应用系统设计》这本书。之前看完了《深入理解计算机系统》，收获很大。你有什么好的分布式系统学习资料推荐吗？",
    "目前订单系统重构的进度大概完成了60%，还剩下支付模块和库存同步模块。支付模块比较复杂，需要对接多个支付渠道。我们打算用消息队列来解耦库存同步。",
    "消息队列我们在 Kafka 和 RabbitMQ 之间犹豫。Kafka 吞吐量高，但运维复杂；RabbitMQ 功能丰富，但性能稍差。我们的消息量大概每天1000万条，你觉得选哪个好？",
    "我们团队有8个人，3个后端、2个前端、1个测试、1个运维，还有1个产品经理。后端老王经验最丰富，遇到难题都找他。测试小李很细心，bug检出率很高。",
    "对了，跟我聊天的时候注意几点：我喜欢简洁直接的回答，不要太啰嗦；技术问题最好带代码示例；如果不确定的问题要说明，不要瞎编。谢谢！",
    "补充一下，我们的监控用的是 Prometheus + Grafana，日志用 ELK Stack。最近在考虑引入链路追踪，OpenTelemetry 看起来不错，但不知道跟现有系统集成麻不麻烦。",
    "昨天线上出了个诡异的 bug，某个接口偶发超时，但日志里看不出什么问题。后来发现是下游服务的连接数满了，但监控指标没配好，没报警。这种问题怎么预防比较好？",
]

# assemble 阶段: 同用户继续对话，用于验证 assemble 是否携带了摘要上下文
ASSEMBLE_FOLLOWUP_MESSAGES = [
    {
        "question": "对了，我之前提到的订单系统重构进展到哪了？支付模块开始了吗？",
        "anchor_keywords": ["订单系统", "支付模块", "60%"],
        "hook": "assemble — latest_archive_overview 重组",
    },
    {
        "question": "我们团队消息队列最终选了什么？之前我跟你讨论过 Kafka 和 RabbitMQ 的取舍。",
        "anchor_keywords": ["Kafka", "RabbitMQ", "消息队列"],
        "hook": "assemble — latest_archive_overview 重组",
    },
]

# 新用户记忆召回
RECALL_QUESTIONS = [
    {
        "question": "我是做什么工作的？用什么技术栈？请简洁回答",
        "expected_keywords": ["软件工程师", "Python", "Go"],
    },
    {
        "question": "我最近在做什么项目？遇到了什么技术挑战？请简洁回答",
        "expected_keywords": ["订单系统", "性能瓶颈", "缓存"],
    },
    {
        "question": "跟我聊天有什么注意事项？请简洁回答",
        "expected_keywords": ["简洁", "代码示例"],
    },
]


# ── Gateway / OpenViking API ─────────────────────────────────────────────


def send_message(gateway_url: str, message: str, user_id: str) -> dict:
    """通过 OpenClaw Responses API 发送消息。"""
    resp = requests.post(
        f"{gateway_url}/v1/responses",
        json={"model": "openclaw", "input": message, "user": user_id},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


def extract_reply_text(data: dict) -> str:
    """从 Responses API 响应中提取助手回复文本。"""
    for item in data.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            for part in item.get("content", []):
                if part.get("type") in ("text", "output_text"):
                    return part.get("text", "")
    return "(无回复)"


class OpenVikingInspector:
    """OpenViking 内部状态检查器。"""

    def __init__(self, base_url: str, api_key: str = "", agent_id: str = AGENT_ID):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.agent_id = agent_id

    def _headers(self) -> dict:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        if self.agent_id:
            h["X-OpenViking-Agent"] = self.agent_id
        return h

    def _get(self, path: str, timeout: int = 10) -> dict | None:
        try:
            resp = requests.get(f"{self.base_url}{path}", headers=self._headers(), timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", data)
            return None
        except Exception as e:
            console.print(f"[dim]GET {path} 失败: {e}[/dim]")
            return None

    def _post(self, path: str, body: dict | None = None, timeout: int = 30) -> dict | None:
        try:
            resp = requests.post(
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=body or {},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", data)
            return None
        except Exception as e:
            console.print(f"[dim]POST {path} 失败: {e}[/dim]")
            return None

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def get_session(self, session_id: str) -> dict | None:
        return self._get(f"/api/v1/sessions/{session_id}")

    def get_session_messages(self, session_id: str) -> list | None:
        result = self._get(f"/api/v1/sessions/{session_id}/messages")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("messages", [])
        return None

    def get_session_context(self, session_id: str, token_budget: int = 128000) -> dict | None:
        return self._get(f"/api/v1/sessions/{session_id}/context?token_budget={token_budget}")

    def commit_session(self, session_id: str, wait: bool = True) -> dict | None:
        result = self._post(f"/api/v1/sessions/{session_id}/commit", timeout=120)
        if not result:
            return None

        if wait and result.get("task_id"):
            task_id = result["task_id"]
            deadline = time.time() + 120
            while time.time() < deadline:
                time.sleep(0.5)
                task = self._get(f"/api/v1/tasks/{task_id}")
                if not task:
                    continue
                if task.get("status") == "completed":
                    result["status"] = "completed"
                    result["memories_extracted"] = task.get("result", {}).get(
                        "memories_extracted", {}
                    )
                    return result
                if task.get("status") == "failed":
                    result["status"] = "failed"
                    result["error"] = task.get("error")
                    return result

        return result

    def search_memories(
        self, query: str, target_uri: str = "viking://user/memories", limit: int = 10
    ) -> list:
        result = self._post(
            "/api/v1/search/find",
            {"query": query, "target_uri": target_uri, "limit": limit},
        )
        if isinstance(result, dict):
            return result.get("memories", [])
        return []

    def list_fs(self, uri: str) -> list:
        result = self._get(f"/api/v1/fs/ls?uri={uri}&output=original")
        return result if isinstance(result, list) else []

    def read_fs(self, uri: str) -> str | None:
        """读取 fs 中某个文件的内容。"""
        result = self._get(f"/api/v1/content/read?uri={uri}")
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return result.get("content")
        return None


# ── 渲染函数 ──────────────────────────────────────────────────────────────


def render_reply(text: str, title: str = "回复"):
    lines = text.split("\n")
    if len(lines) > 25:
        text = "\n".join(lines[:25]) + f"\n\n... (共 {len(lines)} 行，已截断)"
    console.print(Panel(Markdown(text), title=f"[green]{title}[/green]", border_style="green"))


def render_json(data: Any, title: str = "JSON"):
    console.print(
        Panel(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:2000], title=title)
    )


def render_session_info(info: dict, title: str = "Session 信息"):
    table = Table(title=title, show_header=True)
    table.add_column("属性", style="cyan")
    table.add_column("值", style="green")
    for key, value in info.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        table.add_row(str(key), str(value)[:120])
    console.print(table)


# ── Phase 1: 多轮对话 ────────────────────────────────────────────────────


def run_phase_chat(gateway_url: str, user_id: str, delay: float, verbose: bool) -> tuple[int, int]:
    """Phase 1: 多轮对话 — 测试 afterTurn 写入。"""
    console.print()
    console.rule(f"[bold]Phase 1: 多轮对话 ({len(CHAT_MESSAGES)} 轮) — afterTurn 写入[/bold]")
    console.print(f"[yellow]用户ID:[/yellow] {user_id}")
    console.print(f"[yellow]Gateway:[/yellow] {gateway_url}")
    console.print()

    total = len(CHAT_MESSAGES)
    ok = fail = 0

    for i, msg in enumerate(CHAT_MESSAGES, 1):
        console.rule(f"[dim]Turn {i}/{total}[/dim]", style="dim")
        console.print(
            Panel(
                msg[:200] + ("..." if len(msg) > 200 else ""),
                title=f"[bold cyan]用户 [{i}/{total}][/bold cyan]",
                border_style="cyan",
            )
        )
        try:
            data = send_message(gateway_url, msg, user_id)
            reply = extract_reply_text(data)
            render_reply(reply[:500] + ("..." if len(reply) > 500 else ""))
            ok += 1
        except Exception as e:
            console.print(f"[red][ERROR][/red] {e}")
            fail += 1

        if i < total:
            time.sleep(delay)

    console.print()
    console.print(f"[yellow]对话完成:[/yellow] {ok} 成功, {fail} 失败")

    wait = max(delay * 2, 5)
    console.print(f"[yellow]等待 {wait:.0f}s 让 afterTurn 处理完成...[/yellow]")
    time.sleep(wait)

    return ok, fail


# ── Phase 2: afterTurn 验证 ──────────────────────────────────────────────


def run_phase_after_turn(openviking_url: str, user_id: str, verbose: bool) -> bool:
    """Phase 2: afterTurn 验证 — 检查 OV session 内部状态确认消息已写入。"""
    console.print()
    console.rule("[bold]Phase 2: afterTurn 验证 — 检查 OV session 消息写入[/bold]")
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print("[dim]- afterTurn 应将每轮消息写入 OV session[/dim]")
    console.print("[dim]- session.message_count > 0[/dim]")
    console.print("[dim]- pending_tokens > 0 (消息尚未 commit)[/dim]")
    console.print("[dim]- sessionId 应为 OpenClaw 传入的 user_id[/dim]")
    console.print()

    inspector = OpenVikingInspector(openviking_url)

    # 2.1 健康检查
    console.print("[bold]2.1 OpenViking 健康检查[/bold]")
    healthy = inspector.health_check()
    check("OpenViking 服务可达", healthy)
    if not healthy:
        return False

    # 2.2 Session 存在且有消息
    console.print("\n[bold]2.2 Session 存在性 & 消息计数[/bold]")
    session_info = inspector.get_session(user_id)
    check("Session 存在", session_info is not None, f"session_id={user_id}")

    if not session_info:
        console.print("[red]Session 不存在，无法继续验证[/red]")
        return False

    if verbose:
        render_session_info(session_info, f"Session: {user_id}")

    msg_count = session_info.get("message_count", 0)
    check(
        "message_count > 0 (afterTurn 写入成功)",
        msg_count > 0,
        f"message_count={msg_count}",
    )

    # pending_tokens 表示尚未 commit 的 token 数
    pending = session_info.get("pending_tokens", 0)
    check(
        "pending_tokens > 0 (有待 commit 的内容)",
        pending > 0,
        f"pending_tokens={pending}",
    )

    # 2.3 检查消息内容: 至少部分对话内容能在 OV 消息中找到
    console.print("\n[bold]2.3 消息内容抽样校验[/bold]")
    messages = inspector.get_session_messages(user_id)
    if messages is not None:
        check("能获取到 session 消息列表", True, f"共 {len(messages)} 条消息")

        # 取第一条用户消息的特征文本做匹配
        sample_text = "张明"
        all_text = json.dumps(messages, ensure_ascii=False)
        check(
            f"消息内容包含特征文本「{sample_text}」",
            sample_text in all_text,
            "验证 afterTurn 写入的内容与发送一致",
        )

        sample_text_2 = "PostgreSQL"
        check(
            f"消息内容包含特征文本「{sample_text_2}」",
            sample_text_2 in all_text,
            "验证多轮消息写入",
        )
    else:
        check("能获取到 session 消息列表", False, "GET messages 返回 None")

    # 2.4 context 在 commit 前应返回 messages
    console.print("\n[bold]2.4 Commit 前 context[/bold]")
    ctx = inspector.get_session_context(user_id)
    if ctx:
        ctx_msg_count = len(ctx.get("messages", []))
        has_summary_archive = bool(ctx.get("latest_archive_overview"))
        check(
            "context 返回 messages > 0",
            ctx_msg_count > 0,
            f"messages={ctx_msg_count}",
        )
        check(
            "commit 前 latest_archive_overview 为空",
            not has_summary_archive,
            f"latest_archive_overview={ctx.get('latest_archive_overview')}",
        )
        if verbose and ctx.get("stats"):
            console.print(f"  [dim]stats: {ctx['stats']}[/dim]")
    else:
        check("context 可调用", False, "返回 None")

    return True


# ── Phase 3: Commit 验证 ─────────────────────────────────────────────────


def run_phase_commit(openviking_url: str, user_id: str, verbose: bool) -> bool:
    """Phase 3: Commit 验证 — 触发 commit, 检查归档结构和记忆提取。"""
    console.print()
    console.rule("[bold]Phase 3: Commit 验证 — 触发 session.commit()[/bold]")
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print("[dim]- commit 返回 status=completed/accepted[/dim]")
    console.print("[dim]- 消息被归档 (archived=true)[/dim]")
    console.print("[dim]- 提取出记忆 (memories_extracted > 0)[/dim]")
    console.print("[dim]- 归档目录含 .overview.md 和 .meta.json[/dim]")
    console.print()

    inspector = OpenVikingInspector(openviking_url)

    # 3.1 执行 commit
    console.print("[bold]3.1 执行 session.commit()[/bold]")
    console.print("[dim]正在等待 commit 完成 (可能需要 1-2 分钟)...[/dim]")

    commit_result = inspector.commit_session(user_id, wait=True)
    check("commit 返回结果", commit_result is not None)

    if not commit_result:
        console.print("[red]Commit 失败，无法继续[/red]")
        return False

    if verbose:
        render_json(commit_result, "Commit 结果")

    status = commit_result.get("status", "unknown")
    check(
        "commit status 为 completed 或 accepted",
        status in ("completed", "accepted"),
        f"status={status}",
    )

    archived = commit_result.get("archived", False)
    check("archived=true (消息已归档)", archived is True, f"archived={archived}")

    memories = commit_result.get("memories_extracted", {})
    total_mem = sum(memories.values()) if memories else 0
    check(
        "memories_extracted > 0 (提取出记忆)",
        total_mem > 0,
        f"total={total_mem}, categories={memories}",
    )

    # 3.2 commit 后 session 状态
    console.print("\n[bold]3.2 Commit 后 session 状态[/bold]")
    post_session = inspector.get_session(user_id)
    if post_session:
        commit_count = post_session.get("commit_count", 0)
        check(
            "commit_count >= 1",
            commit_count >= 1,
            f"commit_count={commit_count}",
        )

        post_pending = post_session.get("pending_tokens", 0)
        # commit 后 pending_tokens 应该很低 (归档后清空了旧消息)
        console.print(f"  [dim]commit 后 pending_tokens={post_pending}[/dim]")

    # 3.3 检查归档目录结构
    console.print("\n[bold]3.3 归档目录结构检查[/bold]")
    # 尝试用 context 来间接确认 latest_archive_overview 存在
    ctx_after = inspector.get_session_context(user_id)
    if ctx_after:
        has_summary_archive = bool(ctx_after.get("latest_archive_overview"))
        check(
            "commit 后 context 返回 latest_archive_overview",
            has_summary_archive,
            f"latest_archive_overview={ctx_after.get('latest_archive_overview')}",
        )

        if has_summary_archive:
            overview = ctx_after.get("latest_archive_overview", "")
            check(
                "latest_archive_overview 非空 (摘要已生成)",
                len(overview) > 10,
                f"overview 长度={len(overview)} chars",
            )
            if verbose:
                console.print(f"  [dim]overview 前 200 字: {overview[:200]}...[/dim]")
    else:
        check("commit 后 context 可调用", False)

    # 3.4 检查 estimatedTokens 合理性
    if ctx_after:
        stats = ctx_after.get("stats", {})
        archive_tokens = stats.get("archiveTokens", 0)
        check(
            "archiveTokens > 0 (归档 token 计数合理)",
            archive_tokens > 0,
            f"archiveTokens={archive_tokens}",
        )

    return True


# ── Phase 4: Assemble 验证 ───────────────────────────────────────────────


def run_phase_assemble(
    gateway_url: str, openviking_url: str, user_id: str, delay: float, verbose: bool
) -> bool:
    """Phase 4: Assemble 验证 — 同用户继续对话，验证上下文从 latest archive overview 重组。"""
    console.print()
    console.rule("[bold]Phase 4: Assemble 验证 — 同用户继续对话[/bold]")
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print(
        "[dim]- 同用户对话触发 assemble(): 从 OV latest_archive_overview + active messages 重组上下文[/dim]"
    )
    console.print("[dim]- 回复应能引用 Phase 1 中已被归档的信息[/dim]")
    console.print("[dim]- context 应返回 latest_archive_overview (证明 assemble 有数据源)[/dim]")
    console.print()

    inspector = OpenVikingInspector(openviking_url)

    # 4.1 确认 assemble 的数据源 (latest_archive_overview) 就绪
    console.print("[bold]4.1 确认 assemble 数据源[/bold]")
    ctx = inspector.get_session_context(user_id)
    if ctx:
        has_summary_archive = bool(ctx.get("latest_archive_overview"))
        check(
            "context 返回 latest_archive_overview",
            has_summary_archive,
            f"latest_archive_overview={ctx.get('latest_archive_overview')}",
        )
    else:
        check("context 可用", False)
        return False

    # 4.2 assemble budget trimming: 用极小 budget 验证裁剪
    console.print("\n[bold]4.2 Assemble budget trimming[/bold]")
    tiny_ctx = inspector.get_session_context(user_id, token_budget=1)
    if tiny_ctx:
        stats = tiny_ctx.get("stats", {})
        total_archives = stats.get("totalArchives", 0)
        included = stats.get("includedArchives", 0)
        dropped = stats.get("droppedArchives", 0)
        check(
            "budget=1 时 latest_archive_overview 被裁剪",
            included == 0 or dropped > 0,
            f"total={total_archives}, included={included}, dropped={dropped}",
        )
        active_tokens = stats.get("activeTokens", 0)
        console.print(
            f"  [dim]activeTokens={active_tokens}, archiveTokens={stats.get('archiveTokens', 0)}[/dim]"
        )
    else:
        check("tiny budget context 可调用", False)

    # 4.3 同用户继续对话 — assemble 应重组归档上下文
    console.print("\n[bold]4.3 同用户继续对话 — 验证 assemble 重组归档内容[/bold]")
    console.print(f"[yellow]用户ID:[/yellow] {user_id} (同一用户，继续对话)")
    console.print()

    total = len(ASSEMBLE_FOLLOWUP_MESSAGES)
    for i, item in enumerate(ASSEMBLE_FOLLOWUP_MESSAGES, 1):
        q = item["question"]
        keywords = item["anchor_keywords"]

        console.rule(f"[dim]Assemble 验证 {i}/{total}[/dim]", style="dim")
        console.print(
            Panel(
                f"{q}\n\n[dim]锚点关键词: {', '.join(keywords)}[/dim]\n[dim]Hook: {item['hook']}[/dim]",
                title=f"[bold cyan]Assemble Q{i}[/bold cyan]",
                border_style="cyan",
            )
        )

        try:
            data = send_message(gateway_url, q, user_id)
            reply = extract_reply_text(data)
            render_reply(reply)

            reply_lower = reply.lower()
            hits = [kw for kw in keywords if kw.lower() in reply_lower]
            hit_rate = len(hits) / len(keywords) if keywords else 0
            check(
                f"Assemble Q{i}: 回复包含归档内容 (命中率 >= 50%)",
                hit_rate >= 0.5,
                f"命中={hits}, 未命中={[k for k in keywords if k not in hits]}, rate={hit_rate:.0%}",
            )
        except Exception as e:
            check(f"Assemble Q{i}: 发送成功", False, str(e))

        if i < total:
            time.sleep(delay)

    # 4.4 对话后验证 afterTurn 继续写入 (新消息进入 active messages)
    console.print("\n[bold]4.4 Assemble 后 afterTurn 继续写入[/bold]")
    time.sleep(3)
    post_ctx = inspector.get_session_context(user_id)
    if post_ctx:
        post_msg_count = len(post_ctx.get("messages", []))
        check(
            "继续对话后 active messages 增加",
            post_msg_count > 0,
            f"active messages={post_msg_count}",
        )

    return True


# ── Phase 5: SessionId 一致性验证 ────────────────────────────────────────


def run_phase_session_id(openviking_url: str, user_id: str, verbose: bool) -> bool:
    """Phase 5: SessionId 一致性验证 — 确认整条链路使用统一的 sessionId。"""
    console.print()
    console.rule("[bold]Phase 5: SessionId 一致性验证[/bold]")
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print("[dim]- 重构后 sessionId 统一为 OpenClaw 传入的 user_id[/dim]")
    console.print("[dim]- OV session_id == user_id (无 sessionKey 前缀/后缀)[/dim]")
    console.print("[dim]- context 用同一 sessionId 可查到数据[/dim]")
    console.print()

    inspector = OpenVikingInspector(openviking_url)

    # 5.1 session_id 就是 user_id
    console.print("[bold]5.1 SessionId == UserId[/bold]")
    session = inspector.get_session(user_id)
    check(
        f"OV session 以 user_id={user_id} 为 ID 可查到",
        session is not None,
        "sessionId 统一: 插件直接用 user_id 作为 OV session_id",
    )

    # 5.2 不存在以 sessionKey 变体为 ID 的 session
    console.print("\n[bold]5.2 无 sessionKey 残留[/bold]")
    # 如果旧代码有 sessionKey 逻辑, 可能会创建带前缀的 session
    stale_variants = [
        f"sk:{user_id}",
        f"sessionKey:{user_id}",
        f"key:{user_id}",
    ]
    for variant in stale_variants:
        stale = inspector.get_session(variant)
        is_absent = stale is None or stale.get("message_count", 0) == 0
        check(
            f"不存在残留 session「{variant}」",
            is_absent,
            "旧 sessionKey 映射应已移除" if is_absent else f"发现残留: {stale}",
        )

    # 5.3 context 用 user_id 能查到数据
    console.print("\n[bold]5.3 同一 sessionId 查询归档[/bold]")
    ctx = inspector.get_session_context(user_id)
    if ctx:
        has_data = bool(ctx.get("latest_archive_overview")) or len(ctx.get("messages", [])) > 0
        check(
            "context(user_id) 返回数据",
            has_data,
            f"latest_archive_overview={ctx.get('latest_archive_overview')}, messages={len(ctx.get('messages', []))}",
        )
    else:
        check("context(user_id) 可调用", False)

    # 5.4 验证 commit 也是用同一 sessionId (session 有 commit_count > 0)
    console.print("\n[bold]5.4 Commit 使用同一 sessionId[/bold]")
    if session:
        cc = session.get("commit_count", 0)
        check(
            "session(user_id) 有 commit 记录",
            cc > 0,
            f"commit_count={cc}, 说明 commit 也走 user_id 而非 sessionKey",
        )

    return True


# ── Phase 6: 新用户记忆召回 ──────────────────────────────────────────────


def run_phase_recall(gateway_url: str, user_id: str, delay: float, verbose: bool) -> list:
    """Phase 6: 新用户记忆召回 — 验证 before_prompt_build auto-recall。"""
    console.print()
    console.rule(f"[bold]Phase 6: 新用户记忆召回 ({len(RECALL_QUESTIONS)} 轮) — auto-recall[/bold]")
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print("[dim]- 新用户 (新 session) 发送问题[/dim]")
    console.print("[dim]- before_prompt_build 通过 memory search 注入相关记忆[/dim]")
    console.print("[dim]- 回复应包含 Phase 1 对话中的关键信息[/dim]")
    console.print()

    verify_user = f"{user_id}-recall-{uuid.uuid4().hex[:4]}"
    console.print(f"[yellow]验证用户:[/yellow] {verify_user} (新 session)")
    console.print()

    results = []
    total = len(RECALL_QUESTIONS)

    for i, item in enumerate(RECALL_QUESTIONS, 1):
        q = item["question"]
        expected = item["expected_keywords"]

        console.rule(f"[dim]Recall {i}/{total}[/dim]", style="dim")
        console.print(
            Panel(
                f"{q}\n\n[dim]期望关键词: {', '.join(expected)}[/dim]",
                title=f"[bold cyan]Recall Q{i}[/bold cyan]",
                border_style="cyan",
            )
        )

        try:
            data = send_message(gateway_url, q, verify_user)
            reply = extract_reply_text(data)
            render_reply(reply)

            reply_lower = reply.lower()
            hits = [kw for kw in expected if kw.lower() in reply_lower]
            hit_rate = len(hits) / len(expected) if expected else 0
            success = hit_rate >= 0.5

            check(
                f"Recall Q{i}: 关键词命中率 >= 50%",
                success,
                f"命中={hits}, rate={hit_rate:.0%}",
            )
            results.append({"question": q, "hits": hits, "hit_rate": hit_rate, "success": success})
        except Exception as e:
            check(f"Recall Q{i}: 发送成功", False, str(e))
            results.append({"question": q, "hits": [], "hit_rate": 0, "success": False})

        if i < total:
            time.sleep(delay)

    return results


# ── 完整测试 ──────────────────────────────────────────────────────────────


def run_full_test(gateway_url: str, openviking_url: str, user_id: str, delay: float, verbose: bool):
    console.print()
    console.print(
        Panel.fit(
            f"[bold]OpenClaw 记忆链路完整测试[/bold]\n\n"
            f"Gateway: {gateway_url}\n"
            f"OpenViking: {openviking_url}\n"
            f"User ID: {user_id}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            title="测试信息",
        )
    )

    # Phase 1: Chat
    chat_ok, chat_fail = run_phase_chat(gateway_url, user_id, delay, verbose)

    # Phase 2: afterTurn
    run_phase_after_turn(openviking_url, user_id, verbose)

    # Phase 3: Commit
    run_phase_commit(openviking_url, user_id, verbose)

    console.print("\n[yellow]等待 10s 让记忆提取完成...[/yellow]")
    time.sleep(10)

    # Phase 4: Assemble (同用户继续)
    run_phase_assemble(gateway_url, openviking_url, user_id, delay, verbose)

    # Phase 5: SessionId 一致性
    run_phase_session_id(openviking_url, user_id, verbose)

    # Phase 6: 新用户召回
    run_phase_recall(gateway_url, user_id, delay, verbose)

    # ── 汇总报告 ──────────────────────────────────────────────────────────
    console.print()
    console.rule("[bold]测试报告[/bold]")

    passed = sum(1 for a in assertions if a["ok"])
    failed = sum(1 for a in assertions if not a["ok"])
    total = len(assertions)

    table = Table(title=f"断言结果: {passed}/{total} 通过")
    table.add_column("#", style="bold", width=4)
    table.add_column("状态", width=6)
    table.add_column("断言", max_width=60)
    table.add_column("详情", style="dim", max_width=50)

    for i, a in enumerate(assertions, 1):
        status = "[green]PASS[/green]" if a["ok"] else "[red]FAIL[/red]"
        table.add_row(str(i), status, a["label"][:60], (a.get("detail") or "")[:50])

    console.print(table)

    # 按阶段汇总
    tree = Tree(f"[bold]通过: {passed}/{total}, 失败: {failed}[/bold]")
    tree.add(f"Phase 1: 多轮对话 — {chat_ok} 成功 / {chat_fail} 失败")

    fail_list = [a for a in assertions if not a["ok"]]
    if fail_list:
        fail_branch = tree.add(f"[red]失败断言 ({len(fail_list)})[/red]")
        for a in fail_list:
            fail_branch.add(f"[red]✗[/red] {a['label']}")

    console.print(tree)

    if failed == 0:
        console.print("\n[green bold]全部通过！端到端链路验证成功。[/green bold]")
    else:
        console.print(f"\n[red bold]有 {failed} 个断言失败，请检查上方详情。[/red bold]")


# ── 入口 ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw 记忆链路完整测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python test-memory-chain.py
    python test-memory-chain.py --gateway http://127.0.0.1:18790
    python test-memory-chain.py --phase chat
    python test-memory-chain.py --phase afterTurn --user-id test-chain-abc123
    python test-memory-chain.py --phase assemble --user-id test-chain-abc123
    python test-memory-chain.py --verbose
        """,
    )
    parser.add_argument(
        "--gateway",
        default=DEFAULT_GATEWAY,
        help=f"OpenClaw Gateway 地址 (默认: {DEFAULT_GATEWAY})",
    )
    parser.add_argument(
        "--openviking",
        default=DEFAULT_OPENVIKING,
        help=f"OpenViking 服务地址 (默认: {DEFAULT_OPENVIKING})",
    )
    parser.add_argument(
        "--user-id",
        default=USER_ID,
        help="测试用户ID (默认: 随机生成)",
    )
    parser.add_argument(
        "--phase",
        choices=["all", "chat", "afterTurn", "commit", "assemble", "session-id", "recall"],
        default="all",
        help="运行阶段 (默认: all)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="轮次间等待秒数 (默认: 2)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="详细输出",
    )
    args = parser.parse_args()

    gateway_url = args.gateway.rstrip("/")
    openviking_url = args.openviking.rstrip("/")
    user_id = args.user_id

    console.print("[bold]OpenClaw 记忆链路测试[/bold]")
    console.print(f"[yellow]Gateway:[/yellow] {gateway_url}")
    console.print(f"[yellow]OpenViking:[/yellow] {openviking_url}")
    console.print(f"[yellow]User ID:[/yellow] {user_id}")

    if args.phase == "all":
        run_full_test(gateway_url, openviking_url, user_id, args.delay, args.verbose)
    elif args.phase == "chat":
        run_phase_chat(gateway_url, user_id, args.delay, args.verbose)
    elif args.phase == "afterTurn":
        run_phase_after_turn(openviking_url, user_id, args.verbose)
    elif args.phase == "commit":
        run_phase_commit(openviking_url, user_id, args.verbose)
    elif args.phase == "assemble":
        run_phase_assemble(gateway_url, openviking_url, user_id, args.delay, args.verbose)
    elif args.phase == "session-id":
        run_phase_session_id(openviking_url, user_id, args.verbose)
    elif args.phase == "recall":
        run_phase_recall(gateway_url, user_id, args.delay, args.verbose)

    # 打印最终断言统计
    if assertions:
        passed = sum(1 for a in assertions if a["ok"])
        total = len(assertions)
        console.print(f"\n[yellow]断言统计: {passed}/{total} 通过[/yellow]")

    console.print("\n[yellow]测试结束。[/yellow]")


if __name__ == "__main__":
    main()
