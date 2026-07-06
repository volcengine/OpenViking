# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for subagent prompt skill loading."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.agent.subagent import SubagentManager  # noqa: E402
from vikingbot.bus.queue import MessageBus  # noqa: E402


def _write_skill(workspace: Path, name: str, content: str) -> None:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_subagent_prompt_loads_local_skills(tmp_path):
    _write_skill(
        tmp_path,
        "always-skill",
        """---
description: Always active instructions
always: true
---
# Always Skill

Always-loaded instruction.
""",
    )
    _write_skill(
        tmp_path,
        "normal-skill",
        """---
description: Normal on-demand instructions
---
# Normal Skill

Read this only when needed.
""",
    )

    manager = SubagentManager(
        provider=SimpleNamespace(get_default_model=lambda: "fake-model"),
        workspace=tmp_path,
        bus=MessageBus(),
        config=SimpleNamespace(),
    )

    prompt = manager._build_subagent_prompt("inspect local files")

    assert "# Active Skills" in prompt
    assert "### Skill: always-skill" in prompt
    assert "Always-loaded instruction." in prompt
    assert "description: Always active instructions" not in prompt
    assert "# Skills" in prompt
    assert "<name>normal-skill</name>" in prompt
    assert "<description>Normal on-demand instructions</description>" in prompt
    assert "<location>skills/normal-skill/SKILL.md</location>" in prompt


@pytest.mark.asyncio
async def test_subagent_prompt_loads_skills_from_session_workspace(tmp_path):
    source_workspace = tmp_path / "source"
    session_workspace = tmp_path / "sandboxes" / "session"
    _write_skill(
        source_workspace,
        "global-skill",
        """---
description: Global instructions
always: true
---
# Global Skill

Global-loaded instruction.
""",
    )
    _write_skill(
        session_workspace,
        "session-skill",
        """---
description: Session instructions
always: true
---
# Session Skill

Session-loaded instruction.
""",
    )

    class FakeSandboxManager:
        def __init__(self):
            self.created_for = []

        async def get_sandbox(self, session_key):
            self.created_for.append(session_key)
            return SimpleNamespace()

        def get_workspace_path(self, session_key):
            return session_workspace

    sandbox_manager = FakeSandboxManager()
    manager = SubagentManager(
        provider=SimpleNamespace(get_default_model=lambda: "fake-model"),
        workspace=source_workspace,
        bus=MessageBus(),
        config=SimpleNamespace(),
        sandbox_manager=sandbox_manager,
    )
    session_key = SimpleNamespace()

    prompt_workspace = await manager._get_session_workspace(session_key)
    prompt = manager._build_subagent_prompt("inspect local files", workspace=prompt_workspace)

    assert sandbox_manager.created_for == [session_key]
    assert f"Your workspace is at: {session_workspace}" in prompt
    assert "### Skill: session-skill" in prompt
    assert "Session-loaded instruction." in prompt
    assert "Global-loaded instruction." not in prompt
