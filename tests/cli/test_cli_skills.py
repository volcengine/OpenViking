# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI skills endpoint tests (add-skill, list skills, skill CRUD)."""

import os
import tempfile
import uuid

import pytest
from conftest import ov, ov_add_skill

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
            r = ov_add_skill(temp_path)
            assert r["exit_code"] == 0, (
                f"add-skill from file should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
            )
            data = r["json"]
            assert data is not None, "add-skill should return JSON"
            assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
            assert "result" in data, "'result' field should exist"
            result = data["result"]
            assert "root_uri" in result, (
                f"add-skill result should contain root_uri, got keys: {sorted(result.keys())}"
            )
        finally:
            os.unlink(temp_path)


class TestSkillList:
    def test_list_skills(self):
        r = ov(["skills", "list", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov skills list should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, "Expected ok=true"
        assert "result" in data, "'result' field should exist"
        result = data["result"]
        assert isinstance(result, dict), "'result' should be an object"
        assert isinstance(result.get("skills"), list), "'result.skills' should be a list"


class TestSkillShow:
    def test_show_skill(self):
        list_r = ov(["skills", "list", "-o", "json"])
        if list_r["exit_code"] != 0 or not list_r["json"]:
            pytest.skip("No skills available to test show")
            return
        skills = list_r["json"].get("result", {}).get("skills", [])
        if not isinstance(skills, list) or len(skills) == 0:
            pytest.skip("No skills available to test show")
            return
        for skill_entry in skills:
            if not isinstance(skill_entry, dict):
                continue
            skill_name = skill_entry.get("name", "")
            if not skill_name:
                continue
            r = ov(["skills", "show", skill_name, "--level", "2", "-o", "json"])
            assert r["exit_code"] == 0, (
                f"ov skills show should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
            )
            data = r["json"]
            assert data is not None and data.get("ok") is True, "Expected ok=true"
            assert data.get("result", {}).get("name") == skill_name
            return
        pytest.skip("No named skills available to test show")
