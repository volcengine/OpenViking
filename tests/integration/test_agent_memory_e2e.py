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

import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import List, Tuple

import pytest

from openviking.client import LocalClient
from openviking_cli.exceptions import NotFoundError
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


async def _wait_for_task(client: LocalClient, task_id: str, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        task = await client.get_task(task_id) or {}
        status = task.get("status") if isinstance(task, dict) else getattr(task, "status", None)
        if status in {"completed", "failed", "cancelled"}:
            if status != "completed":
                raise RuntimeError(f"Task failed: {task}")
            return
        await asyncio.sleep(1)
    raise TimeoutError(f"Task timed out: {task_id}")


async def _run_conversation(client: LocalClient, turns: List[Tuple[str, str]]) -> None:
    session = await client.create_session()
    session_id = session["session_id"]
    logger.info(f"  session_id = {session_id[:8]}...")
    for role, content in turns:
        await client.add_message(session_id=session_id, role=role, content=content)
    logger.info(f"  Committing {len(turns)} messages...")
    result = await client.commit_session(session_id=session_id)
    task_id = (
        result.get("task_id") if isinstance(result, dict) else getattr(result, "task_id", None)
    )
    if task_id:
        await _wait_for_task(client, task_id)
        logger.info(f"  Done (task {task_id[:8]})")


async def _list_non_overview_entries(client: LocalClient, uri: str) -> List[dict]:
    try:
        entries = await client.ls(uri, simple=False) or []
    except (NotFoundError, Exception):
        return []
    return [
        e
        for e in entries
        if not (
            e.get("name", "") if isinstance(e, dict) else getattr(e, "name", "")
        ).endswith(".overview.md")
    ]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_data_dir():
    """Create a fresh temporary data directory for each test."""
    d = tempfile.mkdtemp(prefix="ov_agent_memory_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def agent_memory_config_check():
    """Skip if agent_memory_enabled is not set in config."""
    OpenVikingConfigSingleton._instance = None
    config = get_openviking_config()
    if not getattr(config.memory, "agent_memory_enabled", False):
        pytest.skip("agent_memory_enabled is not set in config — skipping agent memory tests")
    if config.memory.version != "v2":
        pytest.skip("memory.version != v2 — skipping agent memory tests")


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
class TestAgentMemoryE2E:
    """End-to-end tests for the agent memory two-phase extraction pipeline."""

    async def test_trajectory_and_experience_extraction(
        self, tmp_data_dir: Path, agent_memory_config_check
    ):
        """
        Two sessions in the same booking-conflict domain.

        Assertions:
        - After Round 1: ≥1 trajectory file; exactly 1 experience file.
        - After Round 2: ≥2 trajectory files (timestamped, no duplicates);
          still exactly 1 experience file (EDIT path, not CREATE).
        """
        import os

        os.environ["OPENVIKING_DATA_DIR"] = str(tmp_data_dir)
        OpenVikingConfigSingleton._instance = None

        client = LocalClient(path=str(tmp_data_dir))
        await client.initialize()

        try:
            agent_space = client.service.user.agent_space_name()
            trajectories_dir = f"viking://agent/{agent_space}/memories/trajectories"
            experiences_dir = f"viking://agent/{agent_space}/memories/experiences"

            # ── Round 1: CREATE ───────────────────────────────────────────────
            logger.info("Round 1: flight booking duplicate — expect CREATE experience")
            await _run_conversation(client, CONV_A_FLIGHT_DUPLICATE)

            traj_after_r1 = await _list_non_overview_entries(client, trajectories_dir)
            exp_after_r1 = await _list_non_overview_entries(client, experiences_dir)

            logger.info(f"After Round 1: {len(traj_after_r1)} trajectories, {len(exp_after_r1)} experiences")
            for e in traj_after_r1:
                logger.info(f"  trajectory: {e.get('name') if isinstance(e, dict) else getattr(e, 'name', '')}")
            for e in exp_after_r1:
                logger.info(f"  experience: {e.get('name') if isinstance(e, dict) else getattr(e, 'name', '')}")

            assert len(traj_after_r1) >= 1, "Expected at least 1 trajectory after Round 1"
            assert len(exp_after_r1) == 1, (
                f"Expected exactly 1 experience after Round 1, got {len(exp_after_r1)}"
            )

            # Wait briefly so the embedding queue can index Round 1 memories
            await asyncio.sleep(10)

            # ── Round 2: EDIT ─────────────────────────────────────────────────
            logger.info("Round 2: booking conflict extra cases — expect EDIT experience")
            await _run_conversation(client, CONV_B_FLIGHT_DUPLICATE_EXTRA)

            traj_after_r2 = await _list_non_overview_entries(client, trajectories_dir)
            exp_after_r2 = await _list_non_overview_entries(client, experiences_dir)

            logger.info(f"After Round 2: {len(traj_after_r2)} trajectories, {len(exp_after_r2)} experiences")
            for e in traj_after_r2:
                logger.info(f"  trajectory: {e.get('name') if isinstance(e, dict) else getattr(e, 'name', '')}")
            for e in exp_after_r2:
                logger.info(f"  experience: {e.get('name') if isinstance(e, dict) else getattr(e, 'name', '')}")

            assert len(traj_after_r2) >= 2, (
                "Expected at least 2 trajectories after Round 2 (one per session)"
            )

            # All trajectory filenames must carry a timestamp suffix (_YYYYMMDDHHmmss)
            import re

            timestamp_re = re.compile(r"_\d{14}\.md$")
            for entry in traj_after_r2:
                name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", "")
                assert timestamp_re.search(name), (
                    f"Trajectory filename missing timestamp suffix: {name}"
                )

            # No duplicate trajectory names (base name without timestamp should differ
            # OR timestamps make them unique — either way no two files should be identical)
            traj_names = [
                entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", "")
                for entry in traj_after_r2
            ]
            assert len(set(traj_names)) == len(traj_names), (
                f"Duplicate trajectory filenames detected: {traj_names}"
            )

            # Experience count must still be 1 — Round 2 should EDIT, not CREATE
            assert len(exp_after_r2) == 1, (
                f"Expected exactly 1 experience after Round 2 (EDIT path), got {len(exp_after_r2)}"
            )

        finally:
            await client.close()

    async def test_no_agent_only_schemas_in_user_memory(self, tmp_data_dir: Path):
        """
        Verify that trajectory/experience schemas are filtered out from
        SessionExtractContextProvider (user memory path).
        """
        from openviking.session.memory.session_extract_context_provider import (
            SessionExtractContextProvider,
        )

        provider = SessionExtractContextProvider(messages=[])
        schemas = provider.get_memory_schemas(ctx=None)
        schema_types = [s.memory_type for s in schemas]

        assert "trajectory" not in schema_types, (
            "trajectory schema must not appear in user memory extraction"
        )
        assert "experience" not in schema_types, (
            "experience schema must not appear in user memory extraction"
        )
