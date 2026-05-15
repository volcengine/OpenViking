# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI search operation tests (find, search, grep, glob)."""

import time

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestSearchFind:
    def test_find_basic(self):
        r = ov(["find", "test", "-o", "json", "-n", "5"], timeout=180)
        assert r["exit_code"] == 0, (
            f"ov find should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"ov find should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
        assert "result" in data, "'result' field should exist"
        result = data["result"]
        assert "memories" in result, "'memories' field should exist in result"
        assert "resources" in result, "'resources' field should exist in result"
        assert "skills" in result, "'skills' field should exist in result"
        assert isinstance(result["resources"], list), "'resources' should be a list"

    def test_find_with_uri(self, test_dir_uri):
        r = ov(["find", "test", "-u", test_dir_uri, "-o", "json", "-n", "5"], timeout=180)
        assert r["exit_code"] == 0, (
            f"ov find with uri should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"


class TestSearchSearch:
    def test_search_basic(self):
        r = ov(["search", "test", "-o", "json", "-n", "5"], timeout=180)
        assert r["exit_code"] == 0, (
            f"ov search should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"ov search should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
        assert "result" in data, "'result' field should exist"

    def test_search_with_session(self, test_session_id):
        r = ov(["search", "test", "--session-id", test_session_id, "-o", "json", "-n", "5"], timeout=180)
        assert r["exit_code"] == 0, (
            f"ov search with session should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"


class TestSearchGrep:
    def test_grep_basic(self, test_pack_uri):
        r = None
        for attempt in range(5):
            r = ov(["grep", "-u", test_pack_uri, "CLI", "-o", "json", "-n", "10"], timeout=120)
            if r["exit_code"] == 0:
                break
            time.sleep(10)
        assert r["exit_code"] == 0, (
            f"ov grep should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"ov grep should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"

    def test_grep_case_insensitive(self, test_pack_uri):
        r = None
        for attempt in range(5):
            r = ov(["grep", "-u", test_pack_uri, "-i", "cli", "-o", "json", "-n", "10"], timeout=120)
            if r["exit_code"] == 0:
                break
            time.sleep(10)
        assert r["exit_code"] == 0, (
            f"ov grep --ignore-case should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"


class TestSearchGlob:
    def test_glob_basic(self):
        r = ov(["glob", "**/*.md", "-o", "json", "-n", "10"])
        assert r["exit_code"] == 0, (
            f"ov glob should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"ov glob should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
        assert "result" in data, "'result' field should exist"
        result = data["result"]
        if isinstance(result, dict):
            assert "matches" in result, f"'matches' field should exist in glob result dict, got keys: {list(result.keys())}"
            assert isinstance(result["matches"], list), "'matches' should be a list"
            assert "count" in result, "'count' field should exist in glob result"
            assert isinstance(result["count"], int), "'count' should be an integer"
        else:
            assert isinstance(result, list), f"'result' should be dict or list, got {type(result)}"

    def test_glob_with_uri(self, test_dir_uri):
        r = None
        for attempt in range(5):
            r = ov(["glob", "*.md", "-u", test_dir_uri, "-o", "json"])
            if r["exit_code"] == 0:
                break
            time.sleep(10)
        assert r["exit_code"] == 0, (
            f"ov glob with uri should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
