# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from openviking.session.memory_extractor import MemoryExtractor, ToolSkillCandidateMemory
    from openviking_cli.exceptions import NotFoundError
except Exception:  # pragma: no cover - fallback for minimal local test env
    MemoryExtractor = None
    ToolSkillCandidateMemory = None

    class NotFoundError(Exception):
        pass


pytestmark = pytest.mark.skipif(
    MemoryExtractor is None or ToolSkillCandidateMemory is None,
    reason="openviking.session.memory_extractor not available in this test env",
)


def _ctx(agent_space: str = "agent_space_1"):
    return SimpleNamespace(
        account_id="acc_1",
        user=SimpleNamespace(agent_space_name=lambda: agent_space),
    )


def _tool_candidate(
    tool_name: str = "tool_x",
    call_time: int = 2,
    success_time: int = 2,
    content: str = "some guidelines",
):
    return ToolSkillCandidateMemory(
        category=MagicMock(value="tools"),
        abstract="a",
        overview="o",
        content=content,
        source_session="s",
        user="u",
        language="zh-CN",
        tool_name=tool_name,
        skill_name="",
        call_time=call_time,
        success_time=success_time,
        duration_ms=10,
        prompt_tokens=1,
        completion_tokens=1,
    )


def _skill_candidate(
    skill_name: str = "skill_x",
    content: str = "some guidelines",
):
    return ToolSkillCandidateMemory(
        category=MagicMock(value="skills"),
        abstract="a",
        overview="o",
        content=content,
        source_session="s",
        user="u",
        language="zh-CN",
        tool_name="",
        skill_name=skill_name,
        call_time=1,
        success_time=1,
        duration_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
    )


@pytest.mark.asyncio
async def test_merge_tool_memory_read_failure_skips_write(monkeypatch):
    extractor = MemoryExtractor()
    fs = SimpleNamespace(
        read_file=AsyncMock(side_effect=RuntimeError("read failed")),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    ctx = _ctx()
    candidate = _tool_candidate()
    result = await extractor._merge_tool_memory("tool_x", candidate, ctx)
    assert result is None
    fs.write_file.assert_not_called()


@pytest.mark.asyncio
async def test_merge_tool_memory_not_found_allows_create(monkeypatch):
    extractor = MemoryExtractor()
    fs = SimpleNamespace(
        read_file=AsyncMock(side_effect=NotFoundError("missing", "file")),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    ctx = _ctx()
    candidate = _tool_candidate(content="guide")
    result = await extractor._merge_tool_memory("tool_x", candidate, ctx)
    assert result is not None
    fs.write_file.assert_called_once()


@pytest.mark.asyncio
async def test_merge_tool_memory_monotonic_violation_skips_write(monkeypatch):
    extractor = MemoryExtractor()
    fs = SimpleNamespace(
        read_file=AsyncMock(
            return_value="总调用次数: 10\n成功率: 100.0%\n平均耗时: 1ms\n平均Token: 1\n"
        ),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    monkeypatch.setattr(
        extractor,
        "_merge_tool_statistics",
        lambda existing, new: {**existing, "total_calls": existing["total_calls"] - 1},
    )

    ctx = _ctx()
    candidate = _tool_candidate(call_time=1, content="guide")
    result = await extractor._merge_tool_memory("tool_x", candidate, ctx)
    assert result is None
    fs.write_file.assert_not_called()


@pytest.mark.asyncio
async def test_merge_skill_memory_read_failure_skips_write(monkeypatch):
    extractor = MemoryExtractor()
    fs = SimpleNamespace(
        read_file=AsyncMock(side_effect=RuntimeError("read failed")),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    ctx = _ctx()
    candidate = _skill_candidate()
    result = await extractor._merge_skill_memory("skill_x", candidate, ctx)
    assert result is None
    fs.write_file.assert_not_called()


@pytest.mark.asyncio
async def test_merge_skill_memory_not_found_allows_create(monkeypatch):
    extractor = MemoryExtractor()
    fs = SimpleNamespace(
        read_file=AsyncMock(side_effect=NotFoundError("missing", "file")),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    ctx = _ctx()
    candidate = _skill_candidate(content="guide")
    result = await extractor._merge_skill_memory("skill_x", candidate, ctx)
    assert result is not None
    fs.write_file.assert_called_once()


@pytest.mark.asyncio
async def test_merge_skill_memory_monotonic_violation_skips_write(monkeypatch):
    extractor = MemoryExtractor()
    fs = SimpleNamespace(
        read_file=AsyncMock(return_value="总执行次数: 10\n成功率: 100.0%\n"),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    monkeypatch.setattr(
        extractor,
        "_merge_skill_statistics",
        lambda existing, new: {**existing, "total_executions": existing["total_executions"] - 1},
    )

    ctx = _ctx()
    candidate = _skill_candidate(content="guide")
    result = await extractor._merge_skill_memory("skill_x", candidate, ctx)
    assert result is None
    fs.write_file.assert_not_called()


@pytest.mark.asyncio
async def test_merge_tool_memory_old_format_upgrades_to_reme(monkeypatch):
    extractor = MemoryExtractor()
    monkeypatch.setattr(extractor, "_get_tool_static_description", lambda name: "static desc")
    monkeypatch.setattr(extractor, "_merge_memory_bundle", AsyncMock(return_value=None))

    existing = (
        "## 工具信息\n"
        "- **名称**: tool_x\n\n"
        "## 调用统计\n"
        "- **总调用次数**: 10\n"
        "- **成功率**: 50.0%（5 成功，5 失败）\n"
        "- **平均耗时**: 1ms\n"
        "- **平均Token**: 2\n\n"
        "## 使用指南\n"
        "old guide\n"
    )
    fs = SimpleNamespace(
        read_file=AsyncMock(return_value=existing),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    ctx = _ctx()
    candidate = _tool_candidate(call_time=2, success_time=2, content="new guide\nBest for: docs")
    result = await extractor._merge_tool_memory("tool_x", candidate, ctx)
    assert result is not None

    written = fs.write_file.call_args.kwargs["content"]
    assert "Tool: tool_x" in written
    assert "Tool Memory Context:" in written
    assert "Based on 12 historical calls:" in written
    assert "- Best for: docs" in written
    assert "Guidelines:" in written
    assert "old guide" in written


@pytest.mark.asyncio
async def test_merge_tool_memory_content_format_parses_and_merges(monkeypatch):
    extractor = MemoryExtractor()
    monkeypatch.setattr(extractor, "_get_tool_static_description", lambda name: "static desc")
    monkeypatch.setattr(extractor, "_merge_memory_bundle", AsyncMock(return_value=None))

    existing = (
        "Tool: tool_x\n\n"
        "Static Description:\n"
        '"static desc"\n\n'
        "Tool Memory Context:\n"
        "Based on 3 historical calls:\n"
        "- Success rate: 66.7% (2 successful, 1 failed)\n"
        "- Avg time: 2.0s, Avg tokens: 100\n"
        "- Best for: docs\n"
        "- Optimal params: N/A\n"
        "- Common failures: N/A\n"
        "- Recommendation: N/A\n\n"
        "Guidelines:\n"
        "old guide\n"
    )
    fs = SimpleNamespace(
        read_file=AsyncMock(return_value=existing),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    ctx = _ctx()
    candidate = _tool_candidate(call_time=1, success_time=1, content="new guide")
    result = await extractor._merge_tool_memory("tool_x", candidate, ctx)
    assert result is not None

    written = fs.write_file.call_args.kwargs["content"]
    assert "Based on 4 historical calls:" in written
    assert "Success rate:" in written
    assert "Guidelines:" in written


@pytest.mark.asyncio
async def test_merge_skill_memory_old_format_upgrades_to_aligned(monkeypatch):
    extractor = MemoryExtractor()
    monkeypatch.setattr(extractor, "_merge_memory_bundle", AsyncMock(return_value=None))

    existing = (
        "## 技能信息\n"
        "- **名称**: skill_x\n\n"
        "## 执行统计\n"
        "- **总执行次数**: 10\n"
        "- **成功率**: 80.0%（8 成功，2 失败）\n\n"
        "## 使用指南\n"
        "old guide\n"
    )
    fs = SimpleNamespace(
        read_file=AsyncMock(return_value=existing),
        write_file=AsyncMock(),
    )
    monkeypatch.setattr("openviking.session.memory_extractor.get_viking_fs", lambda: fs)

    ctx = _ctx()
    candidate = _skill_candidate(content="new guide\nRecommended flow: a->b")
    result = await extractor._merge_skill_memory("skill_x", candidate, ctx)
    assert result is not None

    written = fs.write_file.call_args.kwargs["content"]
    assert "Skill: skill_x" in written
    assert "Skill Memory Context:" in written
    assert "Based on 11 historical executions:" in written
    assert "- Recommended flow: a->b" in written
    assert "Guidelines:" in written
