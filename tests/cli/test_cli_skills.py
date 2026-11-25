# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI Skill import and retrieval contract."""

import uuid

import pytest
from conftest import ov, ov_add_skill

pytestmark = pytest.mark.cli_remote


class TestSkillAdd:
    def test_add_skill_from_file(self, tmp_path):
        name = f"cli_test_skill_{uuid.uuid4().hex[:6]}"
        description = "A test skill for CLI automation"
        content = "Keep this instruction in the installed skill."
        source = tmp_path / "local-instructions.md"
        source.write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{content}\n")

        imported = ov_add_skill(str(source))
        assert imported["exit_code"] == 0, imported["stderr"]
        assert imported["json"]["ok"] is True, imported["stdout"]
        assert imported["json"]["result"]["root_uri"].endswith(f"/skills/{name}")

        listed = ov(["skills", "list", "-o", "json"])
        assert listed["exit_code"] == 0, listed["stderr"]
        assert listed["json"]["ok"] is True, listed["stdout"]
        assert any(skill["name"] == name for skill in listed["json"]["result"]["skills"])

        shown = ov(["skills", "show", name, "--level", "2", "-o", "json"])
        assert shown["exit_code"] == 0, shown["stderr"]
        assert shown["json"]["ok"] is True, shown["stdout"]
        assert shown["json"]["result"]["name"] == name
        assert content in shown["json"]["result"]["content"]
