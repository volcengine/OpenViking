# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI relations operation tests (relations, link, unlink)."""

import os
import tempfile
import uuid

import pytest

from conftest import ov

pytestmark = pytest.mark.cli_remote


class TestRelations:
    def test_relations(self, test_pack_uri):
        r = ov(["relations", test_pack_uri, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov relations should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        data = r["json"]
        assert data.get("ok") is True, f"Expected ok=true, got {data.get('ok')}"
        assert "result" in data, "'result' field should exist"


class TestLinkUnlink:
    def test_link_and_unlink(self, test_dir_uri, test_pack_uri):
        second_pack_uri = f"{test_dir_uri}/link_{uuid.uuid4().hex[:6]}"
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("# Link Target\n\nThis is a link target file.")
            temp_path = f.name
        try:
            ov(["add-resource", temp_path, "--to", second_pack_uri, "--wait", "-o", "json"], timeout=120)
        finally:
            os.unlink(temp_path)

        link_r = ov(["link", test_pack_uri, second_pack_uri, "--reason", "CLI test link", "-o", "json"])
        assert link_r["exit_code"] == 0, (
            f"ov link should exit 0, got {link_r['exit_code']}: {link_r['stderr'][:300]}"
        )
        link_data = link_r["json"]
        assert link_data.get("ok") is True, f"Expected ok=true, got {link_data.get('ok')}"

        unlink_r = ov(["unlink", test_pack_uri, second_pack_uri, "-o", "json"])
        assert unlink_r["exit_code"] == 0, (
            f"ov unlink should exit 0, got {unlink_r['exit_code']}: {unlink_r['stderr'][:300]}"
        )
        unlink_data = unlink_r["json"]
        assert unlink_data.get("ok") is True, f"Expected ok=true, got {unlink_data.get('ok')}"

        ov(["rm", second_pack_uri, "-r", "-o", "json"])
