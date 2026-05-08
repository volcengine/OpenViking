# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
End-to-end test for agent memory (trajectory + experience two-phase pipeline).

What this test covers
---------------------
1. Two sessions in the same domain (flight booking conflict resolution).
2. After each session commit, the two-phase pipeline runs:
     Phase 1 — extract a `trajectory` memory (timestamped filename).
     Phase 2 — consolidate the trajectory into an `experience` memory.
3. Round 1 is expected to CREATE the experience.
4. Round 2 covers the same domain with additional edge cases and is expected
   to EDIT/UPDATE the existing experience (not create a duplicate).

Prerequisites
-------------
- ~/.openviking/ov.conf has:
    "memory": { "version": "v2", "agent_memory_enabled": true }

Run
---
    .venv/bin/pytest tests/integration/test_agent_memory_e2e.py -v -s -m integration
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid
import os
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from openviking.client.local import LocalClient
from openviking.core.namespace import to_agent_space
from openviking.server.identity import AccountNamespacePolicy
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.utils.content import deserialize_metadata
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import run_async
from openviking_cli.utils.config import OpenVikingConfigSingleton, get_openviking_config

logger = logging.getLogger(__name__)

# ── Conversation fixtures ─────────────────────────────────────────────────────

# Round 1: Flight booking hits a duplicate; user chooses to replace the old one.
# Expectation: Phase 2 should CREATE a new experience for booking-conflict handling.
CONV_A_FLIGHT_DUPLICATE: List[Tuple[str, str]] = [
    ("user", "帮我订一张下周三北京到上海的机票，上午出发"),
    (
        "assistant",
        "好的，我先用 search_flights 工具查询下周三（2026-04-22）北京到上海上午的航班。",
    ),
    (
        "assistant",
        "[tool_call: search_flights(from=PEK, to=SHA, date=2026-04-22, time=morning)]"
        " 返回了 3 个候选航班：CA1501 07:30、MU5101 08:00、HU7603 09:45。",
    ),
    ("user", "那就订 MU5101 吧"),
    (
        "assistant",
        "[tool_call: book_flight(flight=MU5101, passenger=user_123)]"
        " 订票失败：该用户已存在一张相同日期的预订 (CA1501)。",
    ),
    (
        "assistant",
        "检测到重复预订，我先询问你的偏好。你是想取消已有的 CA1501 换成 MU5101，"
        "还是保留现有预订？",
    ),
    ("user", "那就取消 CA1501，改订 MU5101"),
    ("assistant", "[tool_call: cancel_booking(booking_id=CA1501-xyz)] 已取消原预订。"),
    (
        "assistant",
        "[tool_call: book_flight(flight=MU5101, passenger=user_123)] 预订成功，"
        "票号 MU5101-abc。已将新票号发送到你的邮箱。",
    ),
]

# Round 2: Same domain (booking conflict) with additional edge cases:
#   - User wants to keep both bookings (parallel trips)
#   - Cancellation fails due to departure-within-24h policy
# Expectation: Phase 2 should EDIT the existing experience and append this
# trajectory to source_trajectories.
CONV_B_FLIGHT_DUPLICATE_EXTRA: List[Tuple[str, str]] = [
    ("user", "帮我订 4 月 28 日北京到广州的机票，下午的"),
    (
        "assistant",
        "[tool_call: search_flights(from=PEK, to=CAN, date=2026-04-28, time=afternoon)]"
        " 找到了 CZ3101 14:00、CA1315 15:30、MF8023 16:20。",
    ),
    ("user", "订 CA1315"),
    (
        "assistant",
        "[tool_call: book_flight(flight=CA1315, passenger=user_123)]"
        " 订票失败：同一日期已有一张预订 (CZ3101)。",
    ),
    (
        "assistant",
        "检测到重复预订。这次你想取消 CZ3101 改订 CA1315，还是这两张都要保留？",
    ),
    ("user", "这两张都要，是不同行程"),
    (
        "assistant",
        "[tool_call: check_multi_booking_policy(date=2026-04-28)]"
        " 确认：同一天多个预订是允许的，系统不强制合并。",
    ),
    (
        "assistant",
        "[tool_call: book_flight(flight=CA1315, passenger=user_123, override_duplicate=true)]"
        " 预订成功，票号 CA1315-xyz。两张机票均已生效。",
    ),
    ("user", "好的，另外我想取消上周订的 MF7801"),
    (
        "assistant",
        "[tool_call: cancel_booking(booking_id=MF7801-001)]"
        " 取消失败：距出发时间不足 24 小时，航空公司不允许免费取消。",
    ),
    (
        "assistant",
        "MF7801 的出发时间距现在不足 24 小时，按航空公司政策无法免费取消。"
        "有两个选择：1) 支付改签费改期；2) 放弃该票（退款仅限税费）。请问你想怎么处理？",
    ),
    ("user", "算了，就不管了"),
    ("assistant", "好的，MF7801 保持原状，CA1315 新预订已生效。"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _wait_for_task(client: LocalClient, task_id: str, timeout_s: int = 600) -> None:
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


def _run_conversation(client: LocalClient, turns: List[Tuple[str, str]]) -> None:
    session = run_async(client.create_session())
    session_id = session["session_id"]
    logger.info(f"  session_id = {session_id[:8]}...")
    for role, content in turns:
        run_async(client.add_message(session_id=session_id, role=role, content=content))
    logger.info(f"  Committing {len(turns)} messages...")
    result = run_async(client.commit_session(session_id=session_id))
    task_id = result.get("task_id") if isinstance(result, dict) else getattr(result, "task_id", None)
    if task_id:
        _wait_for_task(client, task_id)
        logger.info(f"  Done (task {task_id[:8]})")


def _list_non_overview_entries(client: LocalClient, uri: str) -> List[dict]:
    try:
        entries = run_async(client.ls(uri, simple=False)) or []
    except Exception:
        return []
    _INTERNAL_SUFFIXES = (".overview.md", ".abstract.md")
    return [
        e
        for e in entries
        if not any(
            (e.get("name", "") if isinstance(e, dict) else getattr(e, "name", "")).endswith(s)
            for s in _INTERNAL_SUFFIXES
        )
    ]


def _entry_uri(entry: dict) -> str:
    if isinstance(entry, dict):
        return str(entry.get("uri", ""))
    return str(getattr(entry, "uri", ""))


def _collect_source_trajectories(client: LocalClient, exp_entries: List[dict]) -> List[str]:
    all_uris: List[str] = []
    for entry in exp_entries:
        exp_uri = _entry_uri(entry)
        if not exp_uri:
            continue
        raw = run_async(client.read(exp_uri)) or ""
        metadata = deserialize_metadata(raw) or {}
        source = metadata.get("source_trajectories", [])
        if isinstance(source, list):
            all_uris.extend(str(u).strip() for u in source if str(u).strip())
        elif isinstance(source, str):
            all_uris.extend(line.strip() for line in source.splitlines() if line.strip())
    return list(dict.fromkeys(all_uris))


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def agent_memory_config_check():
    """Skip unless agent_memory_enabled and memory.version == v2."""
    OpenVikingConfigSingleton._instance = None
    config = get_openviking_config()
    if not getattr(config.memory, "agent_memory_enabled", False):
        pytest.skip("agent_memory_enabled is not set in config — skipping agent memory tests")
    if config.memory.version != "v2":
        pytest.skip("memory.version != v2 — skipping agent memory tests")


@pytest.fixture()
def local_test_env() -> Dict[str, object]:
    policy = AccountNamespacePolicy(
        isolate_user_scope_by_agent=False,
        isolate_agent_scope_by_user=False,
    )
    local_path = Path.cwd() / ".tmp_agent_memory_e2e" / uuid.uuid4().hex[:8]
    local_path.mkdir(parents=True, exist_ok=True)
    try:
        yield {
            "path": str(local_path),
            "account_id": "default",
            "policy": policy,
        }
    finally:
        shutil.rmtree(local_path, ignore_errors=True)


def _build_client(env: Dict[str, object], user_id: str, agent_id: str = "travelbot") -> LocalClient:
    client = LocalClient(
        path=str(env["path"]),
        user=UserIdentifier(str(env["account_id"]), user_id, agent_id),
    )
    client._ctx.namespace_policy = env["policy"]
    run_async(client.initialize())
    return client


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("RUN_AGENT_MEMORY_TESTS") != "1",
    reason="set RUN_AGENT_MEMORY_TESTS=1 to run agent memory e2e tests",
)
@pytest.mark.integration
class TestAgentMemoryE2E:
    """End-to-end tests for the agent memory two-phase extraction pipeline."""

    def test_trajectory_and_experience_extraction(self, agent_memory_config_check, local_test_env):
        """
        Two sessions in the same booking-conflict domain.

        Assertions:
        - After Round 1: ≥1 trajectory file; exactly 1 experience file (CREATE path).
        - After Round 2: trajectory count grows; still exactly 1 experience file (EDIT path, not CREATE).
        """
        policy = AccountNamespacePolicy(
            isolate_user_scope_by_agent=False,
            isolate_agent_scope_by_user=False,
        )
        agent_space = to_agent_space(policy, "alice", "travelbot")
        trajectories_dir = f"viking://agent/{agent_space}/memories/trajectories"
        experiences_dir = f"viking://agent/{agent_space}/memories/experiences"

        client = _build_client(local_test_env, user_id="alice", agent_id="travelbot")
        try:
            logger.info("Round 1: flight booking duplicate (expect CREATE experience)")
            _run_conversation(client, CONV_A_FLIGHT_DUPLICATE)

            traj_after_r1 = _list_non_overview_entries(client, trajectories_dir)
            exp_after_r1 = _list_non_overview_entries(client, experiences_dir)
            assert traj_after_r1, "should have trajectory memories after round 1"
            assert len(exp_after_r1) == 1, "should have exactly 1 experience after round 1 (CREATE path)"

            logger.info("Round 2: booking conflict extra cases (expect EDIT experience)")
            _run_conversation(client, CONV_B_FLIGHT_DUPLICATE_EXTRA)

            traj_after_r2 = _list_non_overview_entries(client, trajectories_dir)
            exp_after_r2 = _list_non_overview_entries(client, experiences_dir)
            assert len(traj_after_r2) > len(traj_after_r1), "trajectory count should grow after round 2"
            assert len(exp_after_r2) == 1, "experience count must remain 1 after round 2 (EDIT path, not CREATE)"

            traj_uris_r2 = {_entry_uri(e) for e in traj_after_r2 if _entry_uri(e)}
            source_trajectories = _collect_source_trajectories(client, exp_after_r2)
            assert source_trajectories, "experience metadata should include source_trajectories"
            assert any(uri in traj_uris_r2 for uri in source_trajectories), (
                "source_trajectories should reference extracted trajectories"
            )
        finally:
            run_async(client.close())

class TestAgentMemorySchemas:
    """Unit tests for agent memory schema filtering — no integration environment needed."""

    def test_no_agent_only_schemas_in_user_memory(self):
        """
        Verify that trajectory/experience schemas are filtered out from
        SessionExtractContextProvider (user memory path).
        """
        provider = SessionExtractContextProvider(messages=[])
        schemas = provider.get_memory_schemas(ctx=None)
        schema_types = [s.memory_type for s in schemas]

        assert "trajectories" not in schema_types, (
            "trajectories schema must not appear in user memory extraction"
        )
        assert "experiences" not in schema_types, (
            "experiences schema must not appear in user memory extraction"
        )

