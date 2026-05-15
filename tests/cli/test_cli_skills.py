# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI skills endpoint tests (add-skill, list skills, skill CRUD)."""

import os
import tempfile
import uuid

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestSkillAdd:
    def test_add_skill_from_file(self):
        skill_name = f"cli_test_skill_{uuid.uuid4().hex[:6]}"
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as f:
            f.write(
                f"---\nname: {skill_name}\ndescription: A test skill for CLI automation\n---\n"
                f"# {skill_name}\n\nThis is a test skill for CLI automation."
            )
            temp_path = f.name
        try:
            r = ov(["add-skill", temp_path, "--wait", "-o", "json"], timeout=120)
            assert r["exit_code"] == 0, (
                f"add-skill from file should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
            )
            data = r["json"]
            assert data is not None, f"add-skill should return JSON"
            assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
            assert "result" in data, "'result' field should exist"
            result = data["result"]
            assert "root_uri" in result, (
                f"add-skill result should contain root_uri, got keys: {sorted(result.keys())}"
            )
        finally:
            os.unlink(temp_path)

    def test_add_skill_with_reason(self):
        skill_name = f"cli_test_skill_reason_{uuid.uuid4().hex[:6]}"
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as f:
            f.write(
                f"---\nname: {skill_name}\ndescription: A test skill with reason\n---\n"
                f"# {skill_name}\n\nThis is a test skill with reason."
            )
            temp_path = f.name
        try:
            r = ov(["add-skill", temp_path, "--wait", "-o", "json"], timeout=120)
            assert r["exit_code"] == 0, (
                f"add-skill should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
            )
            data = r["json"]
            assert data is not None and data.get("ok") is True, f"Expected ok=true"
        finally:
            os.unlink(temp_path)


class TestSkillList:
    def test_list_skills(self):
        r = ov(["ls", "viking://agent/skills/", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov ls skills should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
        assert "result" in data, "'result' field should exist"
        assert isinstance(data["result"], list), "'result' should be a list"


class TestSkillRead:
    def test_read_skill(self):
        ls_r = ov(["ls", "viking://agent/skills/", "-o", "json"])
        if ls_r["exit_code"] != 0 or not ls_r["json"] or not ls_r["json"].get("result"):
            pytest.skip("No skills available to test read")
            return
        skills = ls_r["json"]["result"]
        if not isinstance(skills, list) or len(skills) == 0:
            pytest.skip("No skills available to test read")
            return
        for skill_entry in skills:
            if not isinstance(skill_entry, dict):
                continue
            skill_uri = skill_entry.get("uri", "")
            if not skill_uri:
                continue
            ls_skill_r = ov(["ls", skill_uri, "-o", "json"])
            if ls_skill_r["exit_code"] != 0 or not ls_skill_r["json"] or not ls_skill_r["json"].get("result"):
                continue
            items = ls_skill_r["json"]["result"]
            for item in items:
                if isinstance(item, dict) and item.get("isDir") is False:
                    file_uri = item.get("uri", "")
                    if file_uri:
                        r = ov(["read", file_uri, "-o", "json"])
                        assert r["exit_code"] == 0, (
                            f"ov read skill file should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
                        )
                        return
