# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Skill management tests"""

from pathlib import Path
from types import SimpleNamespace

from openviking import AsyncOpenViking
from openviking.client import LocalClient
from openviking.server.identity import RequestContext, Role
from openviking.telemetry import get_current_telemetry
from openviking_cli.session.user_id import UserIdentifier


class TestAddSkill:
    """Test add_skill"""

    async def test_add_skill_from_file(self, client: AsyncOpenViking, temp_dir: Path):
        """Test adding skill from file"""
        # Create skill file in SKILL.md format
        skill_file = temp_dir / "test_skill.md"
        skill_file.write_text(
            """---
name: test-skill
description: A test skill for unit testing
tags:
  - test
  - unit-test
---

# Test Skill

## Description
This is a test skill for unit testing OpenViking skill management.

## Usage
Use this skill when you need to test skill functionality.

## Instructions
1. Step one: Initialize the skill
2. Step two: Execute the skill
3. Step three: Verify the result
"""
        )

        result = await client.add_skill(data=skill_file)

        assert "uri" in result
        assert "viking://agent/skills/" in result["uri"]

    async def test_add_skill_from_string(self, client: AsyncOpenViking):
        """Test adding skill from string"""
        skill_content = """---
name: string-skill
description: A skill created from string
tags:
  - test
---

# String Skill

## Instructions
This skill was created from a string.
"""
        result = await client.add_skill(data=skill_content)

        assert "uri" in result
        assert "viking://agent/skills/" in result["uri"]

    async def test_add_skill_with_wait_returns_queue_status(self, client: AsyncOpenViking):
        """Test local SDK add_skill(wait=True) preserves queue_status and binds telemetry."""
        del client
        queue_status = {
            "Semantic": {"processed": 0, "error_count": 0, "errors": []},
            "Embedding": {"processed": 1, "error_count": 0, "errors": []},
        }
        seen: dict[str, object] = {}

        async def _fake_add_skill(**kwargs):
            telemetry = get_current_telemetry()
            seen["enabled"] = telemetry.enabled
            seen["telemetry_id"] = telemetry.telemetry_id
            seen["kwargs"] = kwargs
            return {
                "uri": "viking://agent/skills/waited-skill",
                "queue_status": queue_status,
            }

        local_client = LocalClient.__new__(LocalClient)
        local_client._ctx = RequestContext(
            user=UserIdentifier.the_default_user(),
            role=Role.USER,
        )
        local_client._service = SimpleNamespace(
            resources=SimpleNamespace(add_skill=_fake_add_skill)
        )

        result = await LocalClient.add_skill(
            local_client,
            data={"name": "waited-skill", "content": "# Waited Skill"},
            wait=True,
            telemetry=False,
        )

        assert result["uri"] == "viking://agent/skills/waited-skill"
        assert result["queue_status"] == queue_status
        assert seen["enabled"] is True
        assert str(seen["telemetry_id"]).startswith("tm_")
        assert seen["kwargs"]["wait"] is True

    async def test_add_skill_from_mcp_tool(self, client: AsyncOpenViking):
        """Test adding skill from MCP Tool format"""
        mcp_tool = {
            "name": "mcp_test_tool",
            "description": "A test MCP tool",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The search query"}},
                "required": ["query"],
            },
        }
        result = await client.add_skill(data=mcp_tool)

        assert "uri" in result

    async def test_add_skill_from_directory(self, client: AsyncOpenViking, temp_dir: Path):
        """Test adding skill from directory"""
        # Create skill directory
        skill_dir = temp_dir / "dir_skill"
        skill_dir.mkdir()

        # Create SKILL.md
        (skill_dir / "SKILL.md").write_text(
            """---
name: dir-skill
description: A skill from directory
tags:
  - directory
---

# Directory Skill

## Instructions
This skill was loaded from a directory.
"""
        )

        # Create auxiliary file
        (skill_dir / "reference.md").write_text("# Reference\nAdditional reference content.")

        result = await client.add_skill(data=skill_dir)

        assert "uri" in result
        assert "viking://agent/skills/" in result["uri"]


class TestSkillSearch:
    """Test skill search"""

    async def test_find_skill(self, client: AsyncOpenViking, temp_dir: Path):
        """Test searching skills"""
        # Add skill first
        skill_file = temp_dir / "search_skill.md"
        skill_file.write_text(
            """---
name: search-test-skill
description: A skill for testing search functionality
tags:
  - search
  - test
---

# Search Test Skill

## Instructions
Use this skill to test search functionality.
"""
        )
        await client.add_skill(data=skill_file)

        # Search skills
        result = await client.find(query="search functionality")

        assert hasattr(result, "skills")
