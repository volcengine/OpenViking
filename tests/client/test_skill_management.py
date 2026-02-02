# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Skill management tests"""

from pathlib import Path

from openviking import AsyncOpenViking


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
