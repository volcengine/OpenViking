# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI filesystem operation tests (ls, tree, stat, mkdir, rm, mv)."""

import os
import tempfile
import time
import uuid

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestFsLs:
    def test_ls_root(self):
        r = ov(["ls", "viking://", "-o", "json"])
        if r["exit_code"] != 0:
            r = ov(["ls", "viking://resources", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov ls should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"ov ls should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
        assert "result" in data, "'result' field should exist"
        assert isinstance(data["result"], list), "'result' should be a list"

    def test_ls_resources(self):
        r = ov(["ls", "viking://resources", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov ls viking://resources should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"

    def test_ls_simple(self):
        r = ov(["ls", "viking://resources", "-s", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov ls --simple should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )

    def test_ls_recursive(self):
        r = ov(["ls", "viking://resources", "-r", "-o", "json", "-n", "5"])
        assert r["exit_code"] == 0, (
            f"ov ls --recursive should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )


class TestFsTree:
    def test_tree_resources(self, test_dir_uri):
        r = ov(["tree", test_dir_uri, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov tree should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
        assert "result" in data, "'result' field should exist"


class TestFsStat:
    def test_stat_root(self):
        r = ov(["stat", "viking://", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov stat should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None, f"ov stat should return JSON, got stdout: {r['stdout'][:200]}"
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
        assert "result" in data, "'result' field should exist"
        result = data["result"]
        assert "isDir" in result, f"'isDir' should exist in stat result, got keys: {list(result.keys())}"
        assert result["isDir"] is True, "root should be a directory"

    def test_stat_file(self, test_file_uri):
        r = ov(["stat", test_file_uri, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov stat on file should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
        result = data["result"]
        assert result.get("isDir") is False, "file stat should have isDir=false"


class TestFsMkdir:
    def test_mkdir(self, test_dir_uri):
        sub_dir = f"{test_dir_uri}/mkdir_test"
        r = ov(["mkdir", sub_dir, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov mkdir should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert "Directory created" in r["stdout"] or (r["json"] and r["json"].get("ok")), (
            f"mkdir should confirm creation, got: {r['stdout'][:200]}"
        )
        ov(["rm", sub_dir, "-r", "-o", "json"])

    def test_mkdir_with_description(self, test_dir_uri):
        sub_dir = f"{test_dir_uri}/mkdir_desc"
        r = ov(["mkdir", sub_dir, "--description", "CLI test directory", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov mkdir with description should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        ov(["rm", sub_dir, "-r", "-o", "json"])


class TestFsRm:
    def test_rm_directory(self, test_dir_uri):
        sub_dir = f"{test_dir_uri}/rm_test"
        ov(["mkdir", sub_dir, "-o", "json"])
        time.sleep(2)
        r = None
        for attempt in range(5):
            r = ov(["rm", sub_dir, "-r", "-o", "json"])
            if r["exit_code"] == 0:
                break
            time.sleep(3)
        assert r["exit_code"] == 0, (
            f"ov rm should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert "Removed" in r["stdout"] or (r["json"] and r["json"].get("ok")), (
            f"rm should confirm removal, got: {r['stdout'][:200]}"
        )


class TestFsMv:
    def test_mv_directory(self, test_dir_uri):
        src_uri = f"{test_dir_uri}/mv_src_{uuid.uuid4().hex[:6]}"
        dst_uri = f"{test_dir_uri}/mv_dst_{uuid.uuid4().hex[:6]}"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("move me")
            temp_path = f.name
        try:
            ov(["add-resource", temp_path, "--to", src_uri, "--wait", "-o", "json"], timeout=120)
        finally:
            os.unlink(temp_path)
        time.sleep(15)
        r = None
        for attempt in range(20):
            r = ov(["mv", src_uri, dst_uri, "-o", "json"])
            if r["exit_code"] == 0:
                break
            time.sleep(15)
        assert r["exit_code"] == 0, (
            f"ov mv should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        ov(["rm", dst_uri, "-r", "-o", "json"])
