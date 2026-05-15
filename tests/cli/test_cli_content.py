# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI content operation tests (read, abstract, overview, download, write, reindex)."""

import os
import tempfile
import time
import uuid

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestContentRead:
    def test_read(self, test_file_uri):
        r = ov(["read", test_file_uri, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov read should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert len(r["stdout"]) > 0, "read output should not be empty"
        assert "CLI Test" in r["stdout"] or "test" in r["stdout"].lower(), (
            f"read output should contain test content, got: {r['stdout'][:200]}"
        )


class TestContentAbstract:
    def test_abstract(self, test_pack_uri):
        r = ov(["abstract", test_pack_uri, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov abstract should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert len(r["stdout"]) > 0, "abstract output should not be empty"


class TestContentOverview:
    def test_overview(self, test_pack_uri):
        r = ov(["overview", test_pack_uri, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov overview should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert len(r["stdout"]) > 0, "overview output should not be empty"


class TestContentDownload:
    def test_get_download(self, test_file_uri, tmp_path):
        local_path = str(tmp_path / "downloaded.txt")
        r = ov(["get", test_file_uri, local_path, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov get should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert os.path.exists(local_path), "downloaded file should exist"
        assert os.path.getsize(local_path) > 0, "downloaded file should not be empty"


class TestContentWrite:
    def test_write_replace(self, test_file_uri):
        time.sleep(15)
        r = None
        for attempt in range(15):
            r = ov(["write", test_file_uri, "--content", "Updated via CLI write.", "--mode", "replace", "--wait", "-o", "json"], timeout=120)
            if r["exit_code"] == 0:
                break
            if "busy" in r["stderr"].lower() or "internal" in r["stderr"].lower():
                time.sleep(20)
                continue
            time.sleep(10)
        assert r["exit_code"] == 0, (
            f"ov write replace should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"

    def test_write_append(self, test_file_uri):
        time.sleep(15)
        r = None
        for attempt in range(15):
            r = ov(["write", test_file_uri, "--content", "\nAppended via CLI.", "--append", "--wait", "-o", "json"], timeout=120)
            if r["exit_code"] == 0:
                break
            if "busy" in r["stderr"].lower() or "internal" in r["stderr"].lower():
                time.sleep(20)
                continue
            time.sleep(10)
        assert r["exit_code"] == 0, (
            f"ov write append should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"


class TestContentReindex:
    def test_reindex(self, test_dir_uri):
        reindex_pack = f"{test_dir_uri}/reindex_{uuid.uuid4().hex[:6]}"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("# Reindex Test\n\nThis is an independent resource for reindex testing.")
            temp_path = f.name
        try:
            r = None
            for attempt in range(5):
                r = ov(["add-resource", temp_path, "--to", reindex_pack, "--wait", "-o", "json"], timeout=120)
                if r["exit_code"] == 0:
                    break
                time.sleep(5)
            assert r["exit_code"] == 0, f"add-resource for reindex failed: {r['stderr'][:300]}"
        finally:
            os.unlink(temp_path)

        time.sleep(15)
        r = None
        for attempt in range(15):
            r = ov(["reindex", reindex_pack, "--wait", "true", "-o", "json"], timeout=120)
            if r["exit_code"] == 0:
                break
            if "INTERNAL" in r["stderr"]:
                time.sleep(20)
                continue
            time.sleep(15)
        assert r["exit_code"] == 0, (
            f"ov reindex should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data is not None and data.get("ok") is True, f"Expected ok=true"
