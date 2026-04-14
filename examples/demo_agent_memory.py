# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Demo for agent-scope memory (trajectory + experience, two-phase pipeline).

What this demo does
-------------------
1. Runs two sessions in the same domain (booking conflict resolution) to test
   the EDIT path: the second conversation should UPDATE the existing experience
   rather than create a new one, and append to source_trajectories.
2. After each session commit, the two-phase pipeline kicks in:
     Phase 1 — extract a `trajectory` memory (summary of what the agent did).
     Phase 2 — consolidate the trajectory into an `experience` memory
               (create new one, or update an existing one, or no-op).
3. Inspects the resulting memory files under:
     viking://agent/<agent_space>/memories/trajectories/
     viking://agent/<agent_space>/memories/experiences/

Prerequisites
-------------
- ~/.openviking/ov.conf has:
    "memory": { "version": "v2", "agent_memory_enabled": true }
- ragfs-python binding built into openviking/lib/:
    make build   (or: .venv/bin/maturin build --release -m crates/ragfs-python/Cargo.toml
                       and extract the .so into openviking/lib/)

Run
---
    .venv/bin/python examples/demo_agent_memory.py
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "demo_agent_memory_data"

os.environ["OPENVIKING_DATA_DIR"] = str(DATA_DIR)
os.environ["OPENVIKING_CONFIG_FILE"] = str(ROOT / "examples" / "ov.conf.example")

# Use project ov.conf if available
_project_conf = Path.home() / ".openviking" / "ov.conf"
if _project_conf.exists():
    os.environ["OPENVIKING_CONFIG_FILE"] = str(_project_conf)

from openviking.client import LocalClient
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import run_async
from openviking_cli.utils.config import OpenVikingConfigSingleton, get_openviking_config

# ── Conversations for testing the EDIT path ──────────────────────────────────
# Both conversations are in the same domain: booking conflict/duplicate resolution.
# Round 1 creates the initial experience.
# Round 2 covers the same domain with additional edge cases — should EDIT/UPDATE
# the existing experience rather than create a new one.

# Round 1: Flight booking hits a duplicate; user chooses to replace the old one.
CONV_A_FLIGHT_DUPLICATE = [
    ("user", "帮我订一张下周三北京到上海的机票，上午出发"),
    ("assistant",
     "好的，我先用 search_flights 工具查询下周三（2026-04-22）北京到上海上午的航班。"),
    ("assistant",
     "[tool_call: search_flights(from=PEK, to=SHA, date=2026-04-22, time=morning)]"
     " 返回了 3 个候选航班：CA1501 07:30、MU5101 08:00、HU7603 09:45。"),
    ("user", "那就订 MU5101 吧"),
    ("assistant",
     "[tool_call: book_flight(flight=MU5101, passenger=user_123)]"
     " 订票失败：该用户已存在一张相同日期的预订 (CA1501)。"),
    ("assistant",
     "检测到重复预订，我先询问你的偏好。你是想取消已有的 CA1501 换成 MU5101，"
     "还是保留现有预订？"),
    ("user", "那就取消 CA1501，改订 MU5101"),
    ("assistant",
     "[tool_call: cancel_booking(booking_id=CA1501-xyz)] 已取消原预订。"),
    ("assistant",
     "[tool_call: book_flight(flight=MU5101, passenger=user_123)] 预订成功，"
     "票号 MU5101-abc。已将新票号发送到你的邮箱。"),
]

# Round 2: Same domain (booking conflict), but with more edge cases:
#   - User wants to keep both bookings (parallel trips)
#   - Cancellation fails due to departure-within-24h policy
#   - Agent surfaces the policy constraint and lets user decide
# Expectation: Phase 2 EDITS the existing booking-conflict experience to cover
# these additional situations, and appends this trajectory to source_trajectories.
CONV_B_FLIGHT_DUPLICATE_EXTRA = [
    ("user", "帮我订 4 月 28 日北京到广州的机票，下午的"),
    ("assistant",
     "[tool_call: search_flights(from=PEK, to=CAN, date=2026-04-28, time=afternoon)]"
     " 找到了 CZ3101 14:00、CA1315 15:30、MF8023 16:20。"),
    ("user", "订 CA1315"),
    ("assistant",
     "[tool_call: book_flight(flight=CA1315, passenger=user_123)]"
     " 订票失败：同一日期已有一张预订 (CZ3101)。"),
    ("assistant",
     "检测到重复预订。这次你想取消 CZ3101 改订 CA1315，还是这两张都要保留？"),
    ("user", "这两张都要，是不同行程"),
    ("assistant",
     "[tool_call: check_multi_booking_policy(date=2026-04-28)]"
     " 确认：同一天多个预订是允许的，系统不强制合并。"),
    ("assistant",
     "[tool_call: book_flight(flight=CA1315, passenger=user_123, override_duplicate=true)]"
     " 预订成功，票号 CA1315-xyz。两张机票均已生效。"),
    ("user", "好的，另外我想取消上周订的 MF7801"),
    ("assistant",
     "[tool_call: cancel_booking(booking_id=MF7801-001)]"
     " 取消失败：距出发时间不足 24 小时，航空公司不允许免费取消。"),
    ("assistant",
     "MF7801 的出发时间距现在不足 24 小时，按航空公司政策无法免费取消。"
     "有两个选择：1) 支付改签费改期；2) 放弃该票（退款仅限税费）。请问你想怎么处理？"),
    ("user", "算了，就不管了"),
    ("assistant",
     "好的，MF7801 保持原状，CA1315 新预订已生效。"),
]

# ── Commented-out previous conversations (kept for reference) ─────────────────

# CONV_1_BOOK_FLIGHT = [
#     ("user", "帮我订一张下周三北京到上海的机票，上午出发"),
#     ("assistant",
#      "好的，我先用 search_flights 工具查询下周三（2026-04-22）北京到上海上午的航班。"),
#     ("assistant",
#      "[tool_call: search_flights(from=PEK, to=SHA, date=2026-04-22, time=morning)]"
#      " 返回了 3 个候选航班：CA1501 07:30、MU5101 08:00、HU7603 09:45。"),
#     ("user", "那就订 MU5101 吧"),
#     ("assistant",
#      "[tool_call: book_flight(flight=MU5101, passenger=user_123)]"
#      " 订票失败：该用户已存在一张相同日期的预订 (CA1501)。"),
#     ("assistant",
#      "检测到重复预订，我先询问你的偏好。你是想取消已有的 CA1501 换成 MU5101，"
#      "还是保留现有预订？"),
#     ("user", "那就取消 CA1501，改订 MU5101"),
#     ("assistant",
#      "[tool_call: cancel_booking(booking_id=CA1501-xyz)] 已取消原预订。"),
#     ("assistant",
#      "[tool_call: book_flight(flight=MU5101, passenger=user_123)] 预订成功，"
#      "票号 MU5101-abc。已将新票号发送到你的邮箱。"),
# ]
#
# CONV_2_BOOK_HOTEL_DUPLICATE = [
#     ("user", "帮我订 4 月 25 日上海的酒店，一晚"),
#     ("assistant",
#      "[tool_call: search_hotels(city=Shanghai, checkin=2026-04-25, nights=1)]"
#      " 找到了 5 家候选酒店。"),
#     ("user", "订 Grand Central 那家"),
#     ("assistant",
#      "[tool_call: book_hotel(hotel=GrandCentral, date=2026-04-25)]"
#      " 订房失败：同一日期已有一张酒店预订 (RiverView)。"),
#     ("assistant",
#      "又是重复预订的情况。按上次的处理方式，我先问你：是取消 RiverView "
#      "换成 Grand Central，还是保留 RiverView？"),
#     ("user", "取消 RiverView，换 Grand Central"),
#     ("assistant",
#      "[tool_call: cancel_booking(booking_id=RiverView-001)] 已取消。"),
#     ("assistant",
#      "[tool_call: book_hotel(hotel=GrandCentral, date=2026-04-25)] 预订成功。"),
# ]
#
# CONV_3_DEBUG_ASYNCIO = [
#     ("user", "我的 asyncio 任务在 cancel 之后没有退出，pytest 一直卡住"),
#     ("assistant", "先看代码，我用 read_file 读一下相关测试。"),
#     ("assistant",
#      "[tool_call: read_file(path=tests/test_worker.py)] 看到 task.cancel() "
#      "后直接 assert，没有 await 被取消的任务。"),
#     ("assistant",
#      "asyncio 里 cancel() 只是请求取消，需要 await 任务并捕获 CancelledError，"
#      "否则事件循环不会让出。建议改成 `await task` + try/except CancelledError。"),
#     ("user", "改完之后还是偶尔 hang"),
#     ("assistant",
#      "[tool_call: run_tests(path=tests/test_worker.py, timeout=10)] 复现了 hang。"
#      "查看 pytest 配置，发现没有设置 asyncio_mode = auto，同步 fixture "
#      "没有进入 event loop。"),
#     ("assistant",
#      "在 pyproject.toml 里加 `[tool.pytest.ini_options] asyncio_mode = \"auto\"` "
#      "后，测试稳定通过。"),
#     ("user", "完美，问题解决了，谢谢"),
# ]


# ── Helpers ──────────────────────────────────────────────────────────────────

def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def wait_for_task(client: LocalClient, task_id: str, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        task = run_async(client.get_task(task_id)) or {}
        status = task.get("status") if isinstance(task, dict) else getattr(task, "status", None)
        if status in {"completed", "failed", "cancelled"}:
            if status != "completed":
                raise RuntimeError(f"Task failed: {task}")
            return
        time.sleep(1)
    raise TimeoutError(f"Task timed out: {task_id}")


def wait_embeddings(secs: int = 10) -> None:
    """Sleep briefly so the async embedding queue can index the previous round's memories."""
    print(f"  Waiting {secs}s for embedding queue...")
    time.sleep(secs)


def run_conversation(client: LocalClient, turns: list) -> None:
    session = run_async(client.create_session())
    session_id = session["session_id"]
    print(f"  session_id = {session_id[:8]}...")
    for role, content in turns:
        run_async(client.add_message(session_id=session_id, role=role, content=content))
    print(f"  Committing {len(turns)} messages (triggers trajectory + experience extraction)...")
    result = run_async(client.commit_session(session_id=session_id))
    task_id = result.get("task_id") if isinstance(result, dict) else getattr(result, "task_id", None)
    if task_id:
        wait_for_task(client, task_id)
        print(f"  Done (task {task_id[:8]})")
    else:
        print(f"  Done (no task_id returned)")


def _list_entries(client: LocalClient, uri: str) -> list:
    try:
        return run_async(client.ls(uri, simple=False)) or []
    except (NotFoundError, Exception):
        return []


def print_memory_dir(client: LocalClient, label: str, uri: str) -> None:
    print(f"\n[{label}] {uri}")
    entries = _list_entries(client, uri)
    file_entries = [
        e for e in entries
        if not (e.get("name", "") if isinstance(e, dict) else getattr(e, "name", "")).endswith(".overview.md")
    ]
    if not file_entries:
        print("  (empty)")
        return
    for entry in file_entries:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", "")
        if not name:
            continue
        file_uri = f"{uri}/{name}"
        try:
            content = run_async(client.read(file_uri)) or ""
        except Exception as e:
            print(f"  (read failed: {e})")
            continue
        print(f"\n--- {file_uri} ---")
        for i, line in enumerate(content.splitlines()):
            if i >= 40:
                print("  ...")
                break
            print(f"  {line}")


def print_memory_snapshot(
    client: LocalClient, label: str, agent_space: str
) -> None:
    section(f"Memory snapshot after {label}")
    trajectories_dir = f"viking://agent/{agent_space}/memories/trajectories"
    experiences_dir = f"viking://agent/{agent_space}/memories/experiences"
    print_memory_dir(client, "TRAJECTORIES", trajectories_dir)
    print_memory_dir(client, "EXPERIENCES", experiences_dir)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)

    OpenVikingConfigSingleton._instance = None
    print("memory.version:", get_openviking_config().memory.version)
    print("agent_memory_enabled:", get_openviking_config().memory.agent_memory_enabled)

    client = LocalClient(path=str(DATA_DIR))
    run_async(client.initialize())

    try:
        agent_space = client.service.user.agent_space_name()
        print(f"agent_space = {agent_space}")

        # Round 1: flight booking hits a duplicate; user replaces the old one.
        # Expectation: Phase 2 should CREATE a new experience for booking-conflict handling.
        section("Round 1: flight booking duplicate — CREATE experience")
        run_conversation(client, CONV_A_FLIGHT_DUPLICATE)
        print_memory_snapshot(client, "Round 1", agent_space)
        wait_embeddings()  # let embedding queue index Round 1 memories before next search

        # Round 2: same domain (booking conflict) with extra situations:
        #   - user wants to keep both bookings
        #   - cancellation blocked by 24h policy
        # Expectation: Phase 2 should EDIT/UPDATE the existing experience and
        #              append this trajectory to source_trajectories.
        section("Round 2: booking conflict extra cases — EDIT experience")
        run_conversation(client, CONV_B_FLIGHT_DUPLICATE_EXTRA)
        print_memory_snapshot(client, "Round 2", agent_space)

        print("\nDone.")
    finally:
        run_async(client.close())


if __name__ == "__main__":
    main()
