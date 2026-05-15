# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI resource operation tests (add-resource, add-skill)."""

import os
import tempfile
import uuid

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestAddResource:
    def test_add_resource_local_file(self, test_dir_uri):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("CLI test: add resource via local file upload")
            temp_path = f.name
        try:
            to_uri = f"{test_dir_uri}/res_{uuid.uuid4().hex[:6]}"
            r = ov(["add-resource", temp_path, "--to", to_uri, "--wait", "-o", "json"], timeout=120)
            assert r["exit_code"] == 0, (
                f"add-resource should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
            )
            data = r["json"]
            assert data is not None, f"add-resource should return JSON, got stdout: {r['stdout'][:200]}"
            assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
            assert "result" in data, "'result' field should exist"
            result = data["result"]
            assert "root_uri" in result, (
                f"result should contain root_uri, got keys: {sorted(result.keys())}"
            )
            assert result["root_uri"] == to_uri, (
                f"root_uri should match to_uri, expected {to_uri}, got {result['root_uri']}"
            )
        finally:
            os.unlink(temp_path)


class TestAddSkill:
    def test_add_skill_from_file(self):
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as f:
            f.write("---\nname: cli_test_skill\ndescription: A test skill for CLI automation\n---\n# CLI Test Skill\n\nThis is a test skill for CLI automation.")
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

    def test_add_resource_with_reason(self, test_dir_uri):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("CLI test: add resource with reason")
            temp_path = f.name
        try:
            to_uri = f"{test_dir_uri}/reason_{uuid.uuid4().hex[:6]}"
            r = ov(["add-resource", temp_path, "--to", to_uri, "--reason", "CLI test reason", "--wait", "-o", "json"], timeout=120)
            assert r["exit_code"] == 0, (
                f"add-resource with reason should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
            )
            data = r["json"]
            assert data is not None, f"add-resource should return JSON"
            assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
        finally:
            os.unlink(temp_path)
