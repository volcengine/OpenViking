# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI resource import contract."""

import pytest
from conftest import ov, ov_add_resource

pytestmark = pytest.mark.cli_remote


class TestAddResource:
    def test_add_resource_local_file(self, test_dir_uri, tmp_path):
        content = "CLI test: add resource via local file upload"
        source = tmp_path / "import.txt"
        source.write_text(content)
        target = f"{test_dir_uri}/imported"
        imported = ov_add_resource(str(source), target, "--reason", "CLI test reason")
        assert imported["exit_code"] == 0, imported["stderr"]
        assert imported["json"]["ok"] is True, imported["stdout"]
        assert imported["json"]["result"]["root_uri"] == target

        read = ov(["read", f"{target}/import.md", "-o", "json"])
        assert read["exit_code"] == 0, read["stderr"]
        assert read["json"]["ok"] is True, read["stdout"]
        assert read["json"]["result"].strip() == content
