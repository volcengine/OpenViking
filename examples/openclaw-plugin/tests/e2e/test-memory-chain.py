#!/usr/bin/env python3
"""
OpenClaw 记忆链路完整端到端测试

================================================================================
一、用例设计思路
================================================================================

验证 OpenViking 记忆插件的完整链路，覆盖消息写入到记忆召回的每个环节:

  afterTurn → commit → assemble → sessionId 一致性 → 新用户记忆召回

各环节验证目标:
  1. afterTurn: 本轮消息无损写入 OV session，sessionId 一致
  2. commit: 归档消息 + 提取长期记忆（cards/events/profile 等）
  3. assemble: 同用户继续对话时，从 latest_archive_overview + active
     messages 正确重组上下文
  4. budget trimming: 小 token budget 下 archive overview 被合理裁剪
  5. sessionId 一致性: 整条链路使用统一的 OV sessionId，无 sessionKey 残留
  6. 新用户记忆召回: 不同用户发问时，auto-recall 注入相关记忆

对话数据设计:
  12 轮对话涵盖：个人背景、技术栈、项目细节、缓存方案讨论、团队信息、
  消息队列选型、监控体系、偏好设置等，确保记忆提取能覆盖多种类别。

断言策略:
  - 确定性检查 (hard): afterTurn 写入、commit_count、sessionId、overview 存在性
  - LLM 依赖检查 (soft): Assemble/Recall 的关键词命中率 >= 50%

================================================================================
二、测试流程
================================================================================

  Phase 1: 多轮对话 (12 轮) — 写入对话数据
  Phase 2: afterTurn 验证 — 检查 OV session 存在性和消息内容
  Phase 3: Commit 验证 — 触发 commit, 检查归档结构和记忆提取
  Phase 4: Assemble 验证 — 同用户继续对话, 验证上下文重组 + budget trimming
  Phase 5: SessionId 一致性验证 — 无 sessionKey 残留
  Phase 6: 新用户记忆召回 (3 问) — 验证 auto-recall

================================================================================
三、环境前提
================================================================================

  1. OpenViking 服务已启动
  2. OpenClaw Gateway 已启动并配置了 OpenViking 插件
  3. LLM 后端可达（Gateway 需要调用 LLM 生成回复）
  4. 有效的 Gateway auth token

  关键 openclaw.json 配置（影响测试行为）:
    - plugins.slots.contextEngine = "openviking"   # 使用 OV 作为 context engine
    - plugins.slots.memory = "none"                # 不使用内置 memory-core
    - plugins.entries.openviking.enabled = true     # 启用 OV 插件
    - plugins.entries.openviking.config.autoCapture = true  # afterTurn 自动捕获
    - plugins.entries.openviking.config.autoRecall = true   # 新用户自动召回记忆
    - plugins.entries.openviking.config.commitTokenThreshold = 50
      ↑ 此值控制 auto-commit 触发时机。设为 50（token）时几乎每轮触发；
        若值较大（如 10000），测试中 auto-commit 不会提前发生，
        Phase 3 的 commit 验证行为会不同。脚本已兼容两种场景。

================================================================================
四、使用方法
================================================================================

  安装依赖:
    pip install requests rich

  完整测试:
    python test-memory-chain.py --phase all \\
        --gateway http://127.0.0.1:19789 \\
        --openviking http://127.0.0.1:2934 \\
        --token <your_gateway_token>

  分阶段执行:
    python test-memory-chain.py --phase chat        # 仅多轮对话
    python test-memory-chain.py --phase afterTurn   # 仅 afterTurn 验证
    python test-memory-chain.py --phase commit      # 仅 commit 验证
    python test-memory-chain.py --phase assemble    # 仅 assemble 验证
    python test-memory-chain.py --phase session-id  # 仅 sessionId 检查
    python test-memory-chain.py --phase recall      # 仅记忆召回

  其他选项:
    --user-id <id>      固定用户 ID（默认随机生成）
    --delay <seconds>    轮次间等待秒数（默认 3s）
    --verbose / -v       详细输出

  注意:
    - 完整测试约需 8-12 分钟
    - 首次运行前建议清理 OV 数据和 session 数据

================================================================================
五、已知限制
================================================================================

  1. LLM 回复非确定性:
     Assemble 和 Recall 阶段的关键词命中依赖 LLM 回复内容。不同模型、不同
     temperature 设置下可能产生不同结果。关键词命中率阈值已设为 50% 以容忍
     表述差异，但仍可能偶发失败。

  2. auto-commit 时序:
     Gateway 的 afterTurn 可能触发 auto-commit，导致手动 commit 时无新内容。
     脚本已处理此场景（auto_committed=True 时条件性通过）。

  3. 记忆提取质量:
     不同 LLM 对同一对话提取的记忆类别和内容可能不同，影响 Recall 阶段的
     关键词匹配。建议使用支持中文的高质量模型。

  4. sessionId 映射:
     Gateway 内部使用 UUID 作为 OV sessionId，不等于传入的 user_id。脚本
     通过 OV sessions 列表接口自动发现实际 sessionId。

================================================================================
六、预期结果
================================================================================

  29/29 断言全部通过:
    - Phase 1: 12 轮对话全部成功
    - Phase 2~3: afterTurn 写入正确, commit 归档正常
    - Phase 4: Assemble Q1/Q2 关键词命中率 >= 50%
    - Phase 5: sessionId 一致，无残留
    - Phase 6: Recall Q1/Q2/Q3 关键词命中率 >= 50%
"""

import argparse
import io
import json
import os
import sys
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

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 常量 ───────────────────────────────────────────────────────────────────

USER_ID = f"test-chain-{uuid.uuid4().hex[:8]}"
DISPLAY_NAME = "测试用户"
DEFAULT_GATEWAY = "http://127.0.0.1:19789"
DEFAULT_OPENVIKING = "http://127.0.0.1:2934"
AGENT_ID = "main"

console = Console(force_terminal=True)

# ── 测试结果收集 ──────────────────────────────────────────────────────────

assertions: list[dict] = []


def check(label: str, condition: bool, detail: str = ""):
    """记录一个断言结果。"""
    assertions.append({"label": label, "ok": condition, "detail": detail})
    icon = "[green]PASS[/green]" if condition else "[red]FAIL[/red]"
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
        "question": "我之前跟你说过选消息队列的事，Kafka 和 RabbitMQ 各有什么优缺点来着？",
        "anchor_keywords": ["Kafka", "RabbitMQ"],
        "hook": "assemble — latest_archive_overview 重组",
    },
]

# 新用户记忆召回
RECALL_QUESTIONS = [
    {
        "question": "请根据本轮测试标记 {marker} 回答：张明是做什么工作的？用什么技术栈？请简洁回答",
        "expected_keywords": ["张明", "软件工程师", "Python", "Go"],
    },
    {
        "question": "请根据本轮测试标记 {marker} 回答：张明最近在做什么项目？遇到了什么技术挑战？请简洁回答",
        "expected_keywords": ["订单系统", "性能瓶颈", "缓存"],
    },
    {
        "question": "请根据本轮测试标记 {marker} 回答：张明团队有多少人？团队里谁经验最丰富？请简洁回答",
        "expected_keywords": ["8", "老王"],
    },
]


def test_marker(user_id: str) -> str:
    return f"OV-E2E-MARKER:{user_id}"


def chat_message_for_turn(user_id: str, index: int, message: str) -> str:
    if index == 1:
        return f"{message}\n\n本轮 OpenViking e2e 测试标记：{test_marker(user_id)}。"
    return message


def flatten_message_text(value: Any) -> str:
    chunks: list[str] = []
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            chunks.append(flatten_message_text(item))
        return "\n".join(part for part in chunks if part)
    if isinstance(value, dict):
        for key in ("text", "content", "summary", "latest_archive_overview"):
            raw = value.get(key)
            if isinstance(raw, str):
                chunks.append(raw)
        for key in ("parts", "messages", "content"):
            raw = value.get(key)
            if isinstance(raw, list):
                chunks.append(flatten_message_text(raw))
        return "\n".join(part for part in chunks if part)
    return ""


def flatten_context_text(ctx: dict | None) -> str:
    if not isinstance(ctx, dict):
        return ""
    chunks = [
        str(ctx.get("latest_archive_overview") or ""),
        flatten_message_text(ctx.get("pre_archive_abstracts")),
        flatten_message_text(ctx.get("messages")),
    ]
    return "\n".join(part for part in chunks if part)


# ── Token 自动发现 ────────────────────────────────────────────────────────

_gateway_token: str = ""


def discover_gateway_token() -> str:
    """从常见路径自动发现 gateway auth token。"""
    import pathlib

    candidates = [
        pathlib.Path.cwd() / "config" / ".openclaw" / "openclaw.json",
        pathlib.Path.home() / ".openclaw" / "openclaw.json",
    ]
    state_dir = os.environ.get("OPENCLAW_STATE_DIR")
    if state_dir:
        candidates.insert(0, pathlib.Path(state_dir) / "openclaw.json")

    for p in candidates:
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
            if token:
                return token
        except Exception:
            continue
    return ""


def set_gateway_token(token: str):
    global _gateway_token
    _gateway_token = token


# ── Gateway / OpenViking API ─────────────────────────────────────────────


def send_message(gateway_url: str, message: str, user_id: str) -> dict:
    """通过 OpenClaw Responses API 发送消息。"""
    headers = {"Content-Type": "application/json"}
    if _gateway_token:
        headers["Authorization"] = f"Bearer {_gateway_token}"
    resp = requests.post(
        f"{gateway_url}/v1/responses",
        headers=headers,
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

    def list_sessions(self) -> list:
        result = self._get("/api/v1/sessions")
        if isinstance(result, list):
            return result
        return []

    def find_session_for_user(self, user_hint: str) -> str | None:
        """通过遍历 OV session 列表找到与当前测试关联的 session。
        Gateway 可能使用内部 UUID 而非 user_id 作为 OV session_id，
        因此必须检查本轮唯一 marker，不能回退到历史 session。"""
        sessions = self.list_sessions()
        real_sessions = [
            s
            for s in sessions
            if isinstance(s, dict) and not s.get("session_id", "").startswith("memory-store-")
        ]
        if not real_sessions:
            return None

        marker = test_marker(user_hint)
        best_id: str | None = None
        best_score = 0
        for s in real_sessions:
            sid = s.get("session_id", "")
            if not sid:
                continue
            if sid == user_hint:
                return sid
            ctx = self.get_session_context(sid)
            text = flatten_context_text(ctx)
            messages = self.get_session_messages(sid)
            text += "\n" + flatten_message_text(messages)
            score = 0
            if marker in text:
                score += 100
            if user_hint in text:
                score += 20
            if "张明" in text:
                score += 5
            if "PostgreSQL" in text:
                score += 5
            if score > best_score:
                best_score = score
                best_id = sid

        return best_id if best_score >= 100 else None

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

    for i, base_msg in enumerate(CHAT_MESSAGES, 1):
        msg = chat_message_for_turn(user_id, i, base_msg)
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


def run_phase_after_turn(openviking_url: str, user_id: str, verbose: bool) -> tuple[bool, str]:
    """Phase 2: afterTurn 验证 — 检查 OV session 内部状态确认消息已写入。
    返回 (success, resolved_session_id)。"""
    console.print()
    console.rule("[bold]Phase 2: afterTurn 验证 — 检查 OV session 消息写入[/bold]")
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print("[dim]- afterTurn 应将每轮消息写入 OV session[/dim]")
    console.print("[dim]- session.message_count > 0[/dim]")
    console.print("[dim]- pending_tokens > 0 (消息尚未 commit)[/dim]")
    console.print()

    inspector = OpenVikingInspector(openviking_url)

    # 2.1 健康检查
    console.print("[bold]2.1 OpenViking 健康检查[/bold]")
    healthy = inspector.health_check()
    check("OpenViking 服务可达", healthy)
    if not healthy:
        return False, user_id

    # 2.2 Session 发现: Gateway 可能使用内部 UUID 而非 user_id
    console.print("\n[bold]2.2 Session 发现 & 消息计数[/bold]")
    resolved_id = inspector.find_session_for_user(user_id)

    if resolved_id and resolved_id != user_id:
        console.print(f"  [yellow]Gateway 使用内部 session_id: {resolved_id} (非 user_id)[/yellow]")

    check("Session 存在", resolved_id is not None, f"resolved_id={resolved_id}")

    if not resolved_id:
        console.print("[red]OV 中没有找到任何相关 session，无法继续验证[/red]")
        return False, user_id

    session_info = inspector.get_session(resolved_id)
    if not session_info:
        console.print("[red]Session 详情获取失败[/red]")
        return False, resolved_id

    if verbose:
        render_session_info(session_info, f"Session: {resolved_id}")

    msg_count = session_info.get("message_count", 0)
    commit_count = session_info.get("commit_count", 0)
    # Gateway 可能在对话过程中 auto-commit，所以 message_count 可能为 0
    # 但 commit_count > 0 表明 afterTurn 已经处理并提交了消息
    has_activity = msg_count > 0 or commit_count > 0
    check(
        "afterTurn 已处理 (message_count > 0 或 commit_count > 0)",
        has_activity,
        f"message_count={msg_count}, commit_count={commit_count}",
    )

    pending = session_info.get("pending_tokens", 0)
    if commit_count == 0:
        check(
            "pending_tokens > 0 (有待 commit 的内容)",
            pending > 0,
            f"pending_tokens={pending}",
        )
    else:
        console.print(
            f"  [dim]auto-commit 已触发 (commit_count={commit_count})，"
            f"pending_tokens={pending} 属正常[/dim]"
        )

    auto_committed = commit_count > 0

    # 2.3 检查消息内容: 通过 context API 检查（兼容 auto-commit 场景）
    console.print("\n[bold]2.3 消息内容抽样校验[/bold]")
    ctx = inspector.get_session_context(resolved_id)
    if ctx:
        ctx_messages = ctx.get("messages", [])
        overview = ctx.get("latest_archive_overview", "")
        all_text = flatten_context_text(ctx)

        check(
            "context 返回内容 (messages 或 overview)",
            len(ctx_messages) > 0 or bool(overview),
            f"messages={len(ctx_messages)}, overview_len={len(overview)}",
        )

        sample_text = "张明"
        # 如果已 auto-commit, 信息可能在 overview 里而非 messages 中
        check(
            f"内容包含特征文本「{sample_text}」",
            sample_text in all_text,
            "验证 afterTurn 写入的内容与发送一致",
        )

        sample_text_2 = "PostgreSQL"
        check(
            f"内容包含特征文本「{sample_text_2}」",
            sample_text_2 in all_text,
            "验证多轮消息写入",
        )

        marker = test_marker(user_id)
        check(
            f"内容包含本轮唯一标记「{marker}」",
            marker in all_text,
            "确保未误选历史 session",
        )

        if verbose and ctx.get("stats"):
            console.print(f"  [dim]stats: {ctx['stats']}[/dim]")
    else:
        # 回退到 messages API
        messages = inspector.get_session_messages(resolved_id)
        if messages is not None:
            check("能获取到 session 消息列表", True, f"共 {len(messages)} 条消息")
        else:
            check("能获取到 session 消息列表", False, "GET messages 返回 None")

    return True, resolved_id, auto_committed


# ── Phase 3: Commit 验证 ─────────────────────────────────────────────────


def run_phase_commit(
    openviking_url: str,
    session_id: str,
    verbose: bool,
    auto_committed: bool = False,
) -> bool:
    """Phase 3: Commit 验证 — 触发 commit, 检查归档结构和记忆提取。"""
    console.print()
    console.rule("[bold]Phase 3: Commit 验证 — 触发 session.commit()[/bold]")
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print("[dim]- commit 返回 status=completed/accepted[/dim]")
    console.print("[dim]- 消息被归档或已经归档 (auto-commit)[/dim]")
    console.print("[dim]- 提取出记忆 (memories_extracted > 0)[/dim]")
    console.print(f"[dim]- 使用 session_id: {session_id}[/dim]")
    if auto_committed:
        console.print("[yellow]注: Gateway 已触发 auto-commit, 手动 commit 可能无新内容[/yellow]")
    console.print()

    inspector = OpenVikingInspector(openviking_url)

    # 3.1 执行 commit
    console.print("[bold]3.1 执行 session.commit()[/bold]")
    console.print("[dim]正在等待 commit 完成 (可能需要 1-2 分钟)...[/dim]")

    commit_result = inspector.commit_session(session_id, wait=True)
    if auto_committed and not commit_result:
        check(
            "commit 返回结果",
            True,
            "auto-commit 已处理, 手动 commit 无新内容属正常",
        )
    else:
        check("commit 返回结果", commit_result is not None)

    if not commit_result:
        if auto_committed:
            console.print("[yellow]auto-commit 已处理, 手动 commit 无新内容属正常[/yellow]")
        else:
            console.print("[red]Commit 失败，无法继续[/red]")
            return False

    if commit_result:
        if verbose:
            render_json(commit_result, "Commit 结果")

        status = commit_result.get("status", "unknown")
        check(
            "commit status 为 completed 或 accepted",
            status in ("completed", "accepted"),
            f"status={status}",
        )

        archived = commit_result.get("archived", False)
        if auto_committed and not archived:
            check(
                "归档状态合理 (auto-commit 已处理)",
                True,
                "auto-commit 已归档, 本次 commit 无新内容",
            )
        else:
            check("archived=true (消息已归档)", archived is True, f"archived={archived}")

        memories = commit_result.get("memories_extracted", {})
        total_mem = sum(memories.values()) if memories else 0
        if auto_committed and total_mem == 0:
            check(
                "记忆提取状态合理 (auto-commit 已处理)",
                True,
                "auto-commit 已提取记忆",
            )
        else:
            check(
                "memories_extracted > 0 (提取出记忆)",
                total_mem > 0,
                f"total={total_mem}, categories={memories}",
            )

    # 3.2 commit 后 session 状态
    console.print("\n[bold]3.2 Session 归档状态[/bold]")
    post_session = inspector.get_session(session_id)
    if post_session:
        commit_count = post_session.get("commit_count", 0)
        check(
            "commit_count >= 1",
            commit_count >= 1,
            f"commit_count={commit_count}",
        )

        total_memories = post_session.get("memories_extracted", {})
        total_mem_count = sum(total_memories.values()) if isinstance(total_memories, dict) else 0
        check(
            "累计提取记忆 > 0",
            total_mem_count > 0,
            f"total={total_mem_count}, categories={total_memories}",
        )

        post_pending = post_session.get("pending_tokens", 0)
        console.print(f"  [dim]commit 后 pending_tokens={post_pending}[/dim]")

    # 3.3 检查归档目录结构
    console.print("\n[bold]3.3 归档目录结构检查[/bold]")
    ctx_after = inspector.get_session_context(session_id)
    if ctx_after:
        has_summary_archive = bool(ctx_after.get("latest_archive_overview"))
        check(
            "context 返回 latest_archive_overview",
            has_summary_archive,
            f"overview={'有' if has_summary_archive else '无'}",
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
        check("context 可调用", False)

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
    gateway_url: str,
    openviking_url: str,
    user_id: str,
    session_id: str,
    delay: float,
    verbose: bool,
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
    console.print(f"[dim]- OV session_id: {session_id}[/dim]")
    console.print()

    inspector = OpenVikingInspector(openviking_url)

    # 4.1 确认 assemble 的数据源 (latest_archive_overview) 就绪
    console.print("[bold]4.1 确认 assemble 数据源[/bold]")
    ctx = inspector.get_session_context(session_id)
    if ctx:
        has_summary_archive = bool(ctx.get("latest_archive_overview"))
        check(
            "context 返回 latest_archive_overview",
            has_summary_archive,
            f"latest_archive_overview={'有' if has_summary_archive else '无'}",
        )
    else:
        check("context 可用", False)

    # 4.2 assemble budget trimming: 用极小 budget 验证裁剪
    console.print("\n[bold]4.2 Assemble budget trimming[/bold]")
    tiny_ctx = inspector.get_session_context(session_id, token_budget=1)
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
        q = item["question"].format(marker=test_marker(user_id))
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
    post_ctx = inspector.get_session_context(session_id)
    if post_ctx:
        post_msg_count = len(post_ctx.get("messages", []))
        check(
            "继续对话后 active messages 增加",
            post_msg_count > 0,
            f"active messages={post_msg_count}",
        )

    return True


# ── Phase 5: SessionId 一致性验证 ────────────────────────────────────────


def run_phase_session_id(
    openviking_url: str,
    user_id: str,
    session_id: str,
    verbose: bool,
) -> bool:
    """Phase 5: SessionId 一致性验证 — 确认整条链路使用统一的 sessionId。"""
    console.print()
    console.rule("[bold]Phase 5: SessionId 一致性验证[/bold]")
    console.print()
    console.print("[dim]验证点:[/dim]")
    console.print("[dim]- 整条链路使用同一个 OV session_id[/dim]")
    console.print("[dim]- 不存在以 sessionKey 变体为 ID 的残留 session[/dim]")
    console.print("[dim]- context 用 session_id 可查到数据[/dim]")
    console.print(f"[dim]- resolved session_id: {session_id}[/dim]")
    console.print()

    inspector = OpenVikingInspector(openviking_url)

    # 5.1 resolved session_id 可查到
    console.print("[bold]5.1 OV session 可查到[/bold]")
    session = inspector.get_session(session_id)
    check(
        f"OV session (id={session_id[:24]}...) 存在",
        session is not None,
        f"user_id={user_id}",
    )

    # 5.2 不存在以 sessionKey 变体为 ID 的 session
    console.print("\n[bold]5.2 无 sessionKey 残留[/bold]")
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

    # 5.3 context 用 session_id 能查到数据
    console.print("\n[bold]5.3 同一 sessionId 查询归档[/bold]")
    ctx = inspector.get_session_context(session_id)
    if ctx:
        has_data = bool(ctx.get("latest_archive_overview")) or len(ctx.get("messages", [])) > 0
        check(
            "context(session_id) 返回数据",
            has_data,
            f"overview={'有' if ctx.get('latest_archive_overview') else '无'}, messages={len(ctx.get('messages', []))}",
        )
    else:
        check("context(session_id) 可调用", False)

    # 5.4 验证 commit 也是用同一 sessionId (session 有 commit_count > 0)
    console.print("\n[bold]5.4 Commit 使用同一 sessionId[/bold]")
    if session:
        cc = session.get("commit_count", 0)
        check(
            "session 有 commit 记录",
            cc > 0,
            f"commit_count={cc}",
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
        q = item["question"].format(marker=test_marker(user_id))
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

    # Phase 2: afterTurn — 返回实际的 OV session_id
    _, resolved_session_id, auto_committed = run_phase_after_turn(openviking_url, user_id, verbose)

    # Phase 3: Commit
    run_phase_commit(openviking_url, resolved_session_id, verbose, auto_committed)

    console.print("\n[yellow]等待 10s 让记忆提取完成...[/yellow]")
    time.sleep(10)

    # Phase 4: Assemble (同用户继续)
    run_phase_assemble(gateway_url, openviking_url, user_id, resolved_session_id, delay, verbose)

    # Phase 5: SessionId 一致性
    run_phase_session_id(openviking_url, user_id, resolved_session_id, verbose)

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
            fail_branch.add(f"[red]FAIL[/red] {a['label']}")

    console.print(tree)

    if failed == 0:
        console.print("\n[green bold]全部通过！端到端链路验证成功。[/green bold]")
    else:
        console.print(f"\n[red bold]有 {failed} 个断言失败，请检查上方详情。[/red bold]")


# ── 入口 ───────────────────────────────────────────────────────────────────


def main():
    global AGENT_ID
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
        "--token",
        default="",
        help="Gateway auth token (默认: 自动从 openclaw.json 发现)",
    )
    parser.add_argument(
        "--agent-id",
        default=AGENT_ID,
        help=f"OpenViking agent ID (默认: {AGENT_ID})",
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

    token = args.token or discover_gateway_token()
    set_gateway_token(token)

    AGENT_ID = args.agent_id

    console.print("[bold]OpenClaw 记忆链路测试[/bold]")
    console.print(f"[yellow]Gateway:[/yellow] {gateway_url}")
    console.print(f"[yellow]OpenViking:[/yellow] {openviking_url}")
    console.print(f"[yellow]User ID:[/yellow] {user_id}")

    if args.phase == "all":
        run_full_test(gateway_url, openviking_url, user_id, args.delay, args.verbose)
    elif args.phase == "chat":
        run_phase_chat(gateway_url, user_id, args.delay, args.verbose)
    elif args.phase == "afterTurn":
        _, _, _ = run_phase_after_turn(openviking_url, user_id, args.verbose)
    elif args.phase == "commit":
        inspector = OpenVikingInspector(openviking_url, agent_id=AGENT_ID)
        sid = inspector.find_session_for_user(user_id) or user_id
        session_info = inspector.get_session(sid)
        ac = (session_info.get("commit_count", 0) > 0) if session_info else False
        run_phase_commit(openviking_url, sid, args.verbose, ac)
    elif args.phase == "assemble":
        inspector = OpenVikingInspector(openviking_url, agent_id=AGENT_ID)
        sid = inspector.find_session_for_user(user_id) or user_id
        run_phase_assemble(gateway_url, openviking_url, user_id, sid, args.delay, args.verbose)
    elif args.phase == "session-id":
        inspector = OpenVikingInspector(openviking_url, agent_id=AGENT_ID)
        sid = inspector.find_session_for_user(user_id) or user_id
        run_phase_session_id(openviking_url, user_id, sid, args.verbose)
    elif args.phase == "recall":
        run_phase_recall(gateway_url, user_id, args.delay, args.verbose)

    # 打印最终断言统计
    if assertions:
        passed = sum(1 for a in assertions if a["ok"])
        failed = sum(1 for a in assertions if not a["ok"])
        total = len(assertions)
        console.print(f"\n[yellow]断言统计: {passed}/{total} 通过[/yellow]")
        if failed:
            sys.exit(1)

    console.print("\n[yellow]测试结束。[/yellow]")


if __name__ == "__main__":
    main()
