#!/usr/bin/env python3
"""
extractNewTurnTexts 工具调用捕获端到端测试

================================================================================
一、用例设计思路
================================================================================

核心验证点:
  当模型在回复中调用工具（如 code_execution、native_tool 等）时，Gateway 的
  extractNewTurnTexts 需要将 toolUse（工具调用）和 toolResult（工具结果）的
  内容正确捕获并写入 OV session，确保后续归档和记忆提取不会丢失工具调用信息。

测试策略:
  1. 发送消息触发模型使用工具（如计算阶乘、写代码等）
  2. 等待 afterTurn 完成
  3. 从 OV session 中读取已存储的消息
  4. 断言存储的消息中包含 [toolUse:] 和 [toolResult:] 标记
  5. 验证关键词可在 Gateway 响应和 OV 存储中追溯

================================================================================
二、测试流程
================================================================================

  Phase 1: 发送 3 条消息，设计为触发工具调用
  Phase 2: 检查 OV session 存在且有内容
  Phase 3: 验证 toolUse/toolResult 标记和关键词可追溯
  Phase 4: 验证改动前后对比（tool 相关行数 > 0）

================================================================================
三、环境前提
================================================================================

  1. OpenViking 服务已启动
  2. OpenClaw Gateway 已启动并配置了 OpenViking 插件
  3. LLM 后端可达且支持工具调用（function calling / tool use）
  4. 有效的 Gateway auth token

  关键 openclaw.json 配置:
    - plugins.slots.contextEngine = "openviking"
    - plugins.entries.openviking.enabled = true
    - plugins.entries.openviking.config.autoCapture = true  # afterTurn 自动捕获

================================================================================
四、使用方法
================================================================================

  安装依赖:
    pip install requests rich

  运行测试:
    python test-tool-capture.py \\
        --gateway http://127.0.0.1:19789 \\
        --openviking http://127.0.0.1:2934 \\
        --token <your_gateway_token>

  其他选项:
    --verbose / -v   详细输出（显示完整 JSON 响应）
    --delay <sec>    消息间等待秒数（默认 5s）

  注意:
    - 测试约需 2-3 分钟
    - 首次运行前建议清理 OV 数据和 session 数据

================================================================================
五、已知限制
================================================================================

  1. 模型工具调用行为不确定:
     不同 LLM 模型对同一输入是否调用工具的行为不同。有些模型可能选择直接
     回答而不调用工具。脚本对 [toolUse:] 标记做了条件性检查（模型未调用工具
     时跳过强断言）。

  2. 工具调用格式差异:
     不同 LLM provider 的 tool_use/tool_result 输出格式可能不同（如 Anthropic
     vs OpenAI），extractNewTurnTexts 需要正确处理各种格式。

  3. 关键词追溯:
     脚本通过关键词（如"5040"、"factorial"、"斐波那契"）验证工具结果是否被
     正确存储。如果模型未执行预期的计算，关键词可能无法匹配。

================================================================================
六、预期结果
================================================================================

  15/15 断言全部通过:
    - Phase 1: 3 条消息发送成功
    - Phase 2: OV session 存在且有内容
    - Phase 3: toolResult 标记存在, 关键词可追溯
    - Phase 4: tool 相关行数 > 0
"""

import argparse
import io
import json
import re
import sys
import time
import uuid
from datetime import datetime

import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 常量 ──────────────────────────────────────────────────────────────────

GATEWAY_URL = "http://127.0.0.1:19789"
OPENVIKING_URL = "http://127.0.0.1:2934"
AGENT_ID = "main"

console = Console(force_terminal=True)
assertions: list[dict] = []


def check(label: str, condition: bool, detail: str = ""):
    assertions.append({"label": label, "ok": condition, "detail": detail})
    icon = "[green]PASS[/green]" if condition else "[red]FAIL[/red]"
    msg = f"  {icon} {label}"
    if detail:
        msg += f"  [dim]({detail})[/dim]"
    console.print(msg)


def load_gateway_token() -> str:
    """从常见路径自动发现 gateway auth token。"""
    import os
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


# ── API helpers ──────────────────────────────────────────────────────────


def send_message(gateway_url: str, message: str, user_id: str, token: str) -> dict:
    """通过 OpenClaw Responses API 发送消息。"""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.post(
        f"{gateway_url}/v1/responses",
        headers=headers,
        json={"model": "openclaw", "input": message, "user": user_id},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


def extract_reply_text(data: dict) -> str:
    for item in data.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            for part in item.get("content", []):
                if part.get("type") in ("text", "output_text"):
                    return part.get("text", "")
    return "(无回复)"


def has_tool_use_in_output(data: dict) -> bool:
    """检查 Responses API 返回中是否有 tool_use / function_call。"""
    for item in data.get("output", []):
        item_type = item.get("type", "")
        if item_type in ("function_call", "tool_use", "computer_call"):
            return True
        if item.get("role") == "assistant":
            for part in item.get("content", []):
                if part.get("type") in ("tool_use", "toolUse"):
                    return True
    return False


class OVInspector:
    def __init__(self, base_url: str, agent_id: str = AGENT_ID):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id

    def _headers(self) -> dict:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.agent_id:
            h["X-OpenViking-Agent"] = self.agent_id
        return h

    def _get(self, path: str, timeout: int = 10):
        try:
            resp = requests.get(f"{self.base_url}{path}", headers=self._headers(), timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", data)
            return None
        except Exception as e:
            console.print(f"[dim]GET {path} 失败: {e}[/dim]")
            return None

    def list_sessions(self) -> list:
        result = self._get("/api/v1/sessions")
        if isinstance(result, list):
            return result
        return []

    def get_session(self, session_id: str):
        return self._get(f"/api/v1/sessions/{session_id}")

    def get_session_context(self, session_id: str, token_budget: int = 128000):
        return self._get(f"/api/v1/sessions/{session_id}/context?token_budget={token_budget}")

    def find_latest_session(self) -> str | None:
        """找到最近更新的 session ID（gateway 内部使用 UUID，非 user_id）。
        通过检查每个 session 的 updated_at 来找到最新的。"""
        sessions = self.list_sessions()
        real_sessions = [
            s for s in sessions
            if isinstance(s, dict) and not s.get("session_id", "").startswith("memory-store-")
        ]
        if not real_sessions:
            return None

        best_id = None
        best_time = ""
        for s in real_sessions:
            sid = s.get("session_id", "")
            if not sid:
                continue
            detail = self.get_session(sid)
            if not detail:
                continue
            updated = detail.get("updated_at", "")
            if updated > best_time:
                best_time = updated
                best_id = sid

        return best_id or real_sessions[-1].get("session_id")


# ── 核心测试 ──────────────────────────────────────────────────────────────


TOOL_TRIGGER_MESSAGES = [
    {
        "input": "请帮我计算 factorial(7) 的结果，用代码算一下",
        "description": "触发代码执行工具",
        "expect_keywords": ["5040", "factorial"],
    },
    {
        "input": "我叫李明，记住我是一名数据工程师，擅长 Spark 和 Flink，偏好用 Scala 写代码。请同时告诉我今天星期几。",
        "description": "信息存储 + 可能触发工具",
        "expect_keywords": ["李明", "数据工程师"],
    },
    {
        "input": "帮我写一段 Python 代码计算斐波那契数列前10个数，并运行它告诉我结果",
        "description": "触发代码执行并返回结果",
        "expect_keywords": ["斐波那契"],
    },
]


def run_test(
    gateway_url: str,
    openviking_url: str,
    user_id: str,
    delay: float,
    verbose: bool,
    token: str = "",
    agent_id: str = "",
):
    if not token:
        token = load_gateway_token()
    inspector = OVInspector(openviking_url, agent_id=agent_id or AGENT_ID)

    console.print(
        Panel(
            f"[bold]Tool Capture 测试[/bold]\n\n"
            f"Gateway: {gateway_url}\n"
            f"OpenViking: {openviking_url}\n"
            f"User ID: {user_id}\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            title="测试信息",
        )
    )

    # ── Phase 1: 发送消息 ────────────────────────────────────────────────

    console.rule("[bold]Phase 1: 发送消息触发 afterTurn[/bold]")

    gateway_responses = []
    for i, msg_cfg in enumerate(TOOL_TRIGGER_MESSAGES):
        console.print(f"\n[cyan]消息 {i + 1}/{len(TOOL_TRIGGER_MESSAGES)}:[/cyan] {msg_cfg['description']}")
        console.print(f"  [dim]> {msg_cfg['input'][:80]}...[/dim]")

        try:
            data = send_message(gateway_url, msg_cfg["input"], user_id, token)
            reply = extract_reply_text(data)
            has_tool = has_tool_use_in_output(data)

            console.print(f"  [green]回复:[/green] {reply[:120]}...")
            if has_tool:
                console.print("  [yellow]检测到 tool_use 在响应中[/yellow]")

            if verbose:
                console.print(f"  [dim]完整响应: {json.dumps(data, ensure_ascii=False)[:500]}[/dim]")

            gateway_responses.append({
                "index": i,
                "msg": msg_cfg,
                "response": data,
                "reply": reply,
                "has_tool": has_tool,
            })

            check(
                f"消息 {i + 1} 发送成功",
                True,
                f"reply_len={len(reply)}",
            )
        except Exception as e:
            console.print(f"  [red]发送失败: {e}[/red]")
            check(f"消息 {i + 1} 发送成功", False, str(e))

        if i < len(TOOL_TRIGGER_MESSAGES) - 1:
            time.sleep(delay)

    # ── Phase 2: 等待 afterTurn 写入 ───────────────────────────────────

    console.rule("[bold]Phase 2: 检查 OV session 中的存储内容[/bold]")
    console.print("[yellow]等待 afterTurn 写入 OV session...[/yellow]")
    time.sleep(8)

    # Gateway 使用内部 UUID 作为 session ID，需要从 OV 列表中找到最新的
    ov_session_id = inspector.find_latest_session()
    if not ov_session_id:
        console.print("[red]  OV 中没有找到任何 session[/red]")
        check("OV session 存在", False, "no sessions found")
        print_summary()
        return

    console.print(f"  [cyan]OV session ID: {ov_session_id}[/cyan]")

    session_info = inspector.get_session(ov_session_id)
    if session_info:
        msg_count = session_info.get("message_count", "?")
        console.print(f"  Session found: message_count={msg_count}")
        check("OV session 存在", True, f"id={ov_session_id[:16]}...")
    else:
        console.print("[red]  OV session 详情获取失败[/red]")
        check("OV session 存在", False, "session detail failed")
        print_summary()
        return

    # 通过 context API 获取全量上下文（活跃消息 + 归档摘要）
    ctx = inspector.get_session_context(ov_session_id)
    messages = ctx.get("messages", []) if ctx else []

    # 同时获取归档概要文本（低 commit 阈值下消息可能已归档）
    archive_overview = ""
    if ctx:
        for msg in messages:
            if isinstance(msg, dict):
                for part in msg.get("parts", []):
                    if isinstance(part, dict) and part.get("type") == "text":
                        archive_overview += (part.get("text", "") or "") + "\n"

    if not messages and not archive_overview:
        console.print("[red]  OV session 消息为空[/red]")
        check("OV session 有消息", False, "context messages empty")
        print_summary()
        return

    console.print(f"  [green]OV session context 消息数: {len(messages)}[/green]")
    check("OV session 有内容", len(messages) > 0 or bool(archive_overview), f"messages={len(messages)}")

    # ── Phase 3: 分析存储的内容是否包含 tool 信息 ──────────────────────

    console.rule("[bold]Phase 3: 验证 toolUse/toolResult 内容被捕获[/bold]")

    all_stored_text = ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        parts = msg.get("parts", [])
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                all_stored_text += (part.get("text", "") or "") + "\n"

    if verbose:
        console.print(Panel(
            all_stored_text[:3000] + ("..." if len(all_stored_text) > 3000 else ""),
            title="OV 存储的全部文本",
        ))

    any_tool_in_gateway = any(r.get("has_tool") for r in gateway_responses)

    # 检查 toolUse 标记（仅在 gateway 响应确实含 tool_use 时才必须）
    has_tool_use_marker = bool(re.search(r'\[toolUse:', all_stored_text, re.IGNORECASE))
    if any_tool_in_gateway:
        check(
            "存储文本包含 [toolUse:] 标记",
            has_tool_use_marker,
            f"found={has_tool_use_marker}",
        )
    else:
        check(
            "[toolUse:] 标记（模型未调用工具，跳过强断言）",
            True,
            f"no tool_use in gateway response, marker={has_tool_use_marker}",
        )

    # 检查 toolResult 标记
    has_tool_result_marker = bool(re.search(r'result\]:', all_stored_text, re.IGNORECASE))
    check(
        "存储文本包含 tool result 标记",
        has_tool_result_marker or not any_tool_in_gateway,
        f"found={has_tool_result_marker} tool_in_gateway={any_tool_in_gateway}",
    )

    # 检查 assistant 标记
    has_assistant = bool(re.search(r'\[assistant\]:', all_stored_text, re.IGNORECASE))
    check(
        "存储文本包含 [assistant] 标记",
        has_assistant,
        f"found={has_assistant}",
    )

    # 检查 user 标记
    has_user = bool(re.search(r'\[user\]:', all_stored_text, re.IGNORECASE))
    check(
        "存储文本包含 [user] 标记",
        has_user,
        f"found={has_user}",
    )

    # 检查关键内容是否保留（检查活跃上下文 + 归档摘要 + gateway 响应）
    all_gateway_text = "\n".join(r.get("reply", "") for r in gateway_responses)
    combined_text = all_stored_text + "\n" + all_gateway_text

    for msg_cfg in TOOL_TRIGGER_MESSAGES:
        for kw in msg_cfg.get("expect_keywords", []):
            in_stored = kw.lower() in all_stored_text.lower()
            in_gateway = kw.lower() in all_gateway_text.lower()
            detail = f"keyword='{kw}' stored={in_stored} gateway={in_gateway}"
            check(
                f"关键词 {kw} 可追溯",
                in_stored or in_gateway,
                detail,
            )

    # ── Phase 4: 对比改动前后的行为 ──────────────────────────────────────

    console.rule("[bold]Phase 4: 改动前后对比分析[/bold]")

    # 旧版本：只有 [user] 和 [assistant] 的文本
    # 新版本：应该额外包含 [toolUse: xxx] 和 [xxx result] 的内容
    tool_related_lines = []
    for line in all_stored_text.split("\n"):
        stripped = line.strip()
        if re.search(r'\[toolUse:', stripped, re.IGNORECASE):
            tool_related_lines.append(("toolUse", stripped[:150]))
        elif re.search(r'result\]:', stripped, re.IGNORECASE):
            tool_related_lines.append(("toolResult", stripped[:150]))

    if tool_related_lines:
        table = Table(title="捕获到的 Tool 相关内容")
        table.add_column("类型", style="cyan", width=12)
        table.add_column("内容预览", max_width=120)
        for kind, preview in tool_related_lines:
            table.add_row(kind, preview)
        console.print(table)

    check(
        "tool 相关行数 > 0（新逻辑生效）",
        len(tool_related_lines) > 0,
        f"tool_lines={len(tool_related_lines)}",
    )

    # ── 汇总 ─────────────────────────────────────────────────────────────

    print_summary()


def print_summary():
    console.print()
    console.rule("[bold]测试汇总[/bold]")

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

    if failed == 0:
        console.print("\n[green bold]全部通过！toolUse/toolResult 捕获验证成功。[/green bold]")
    else:
        console.print(f"\n[red bold]有 {failed} 个断言失败。[/red bold]")
        console.print("[yellow]注: 如果模型没有调用工具，toolUse/toolResult 标记可能不存在 — 这不代表代码有 bug。[/yellow]")
        console.print("[yellow]可以在 gateway 日志中确认 afterTurn 的存储内容。[/yellow]")


# ── 入口 ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="测试 toolUse/toolResult 捕获")
    parser.add_argument("--gateway", default=GATEWAY_URL, help="Gateway 地址")
    parser.add_argument("--openviking", default=OPENVIKING_URL, help="OpenViking 地址")
    parser.add_argument("--token", default="", help="Gateway auth token (默认: 自动发现)")
    parser.add_argument("--agent-id", default=AGENT_ID, help=f"OpenViking agent ID (默认: {AGENT_ID})")
    parser.add_argument("--delay", type=float, default=3.0, help="消息间延迟秒数")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()

    user_id = f"test-tool-{uuid.uuid4().hex[:8]}"

    run_test(
        gateway_url=args.gateway.rstrip("/"),
        openviking_url=args.openviking.rstrip("/"),
        user_id=user_id,
        delay=args.delay,
        verbose=args.verbose,
        token=args.token,
        agent_id=args.agent_id,
    )


if __name__ == "__main__":
    main()
