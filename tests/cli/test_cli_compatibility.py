# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Verify the version, output formats, and error reporting of the CLI binary."""

import json

import pytest
from conftest import ov, ov_mkdir, ov_rm

pytestmark = pytest.mark.cli_remote


class TestCLIVersion:
    def test_version_command(self):
        flag = ov(["--version"])
        assert flag["exit_code"] == 0, flag["stderr"]
        program, _, cli_version = flag["stdout"].partition(" ")
        assert program == "openviking" and cli_version

        result = ov(["version"])
        assert result["exit_code"] == 0, result["stderr"]
        versions = dict(line.split(":", 1) for line in result["stdout"].splitlines())
        assert versions["CLI"].strip() == cli_version
        assert versions["Server"].strip()


class TestCLIServerConnectivity:
    def test_health_check(self):
        result = ov(["health", "-o", "json"])
        assert result["exit_code"] == 0, result["stderr"]


class TestCLIOutputFormats:
    def test_json_and_table_output(self, test_dir_uri):
        name = "cli_output_child"
        child = f"{test_dir_uri}/{name}"
        created = ov_mkdir(child)
        assert created["exit_code"] == 0, created["stderr"]
        try:
            structured = ov(["ls", test_dir_uri, "-o", "json"])
            assert structured["exit_code"] == 0, structured["stderr"]
            assert structured["json"]["ok"] is True
            assert child in {entry["uri"] for entry in structured["json"]["result"]}

            table = ov(["ls", test_dir_uri, "-o", "table"])
            assert table["exit_code"] == 0, table["stderr"]
            assert table["json"] is None
            assert name in table["stdout"]
        finally:
            ov_rm(child)


class TestCLIExitCodes:
    def test_error_exit_code_for_invalid_uri(self, test_dir_uri):
        cases = [
            ("viking://nonexistent/path/xyz", "INVALID_URI"),
            (f"{test_dir_uri}/missing", "NOT_FOUND"),
        ]
        for uri, code in cases:
            result = ov(["stat", uri, "-o", "json"])
            assert result["exit_code"] != 0, uri
            assert result["stdout"] == "", uri
            envelope = json.loads(result["stderr"])
            assert envelope["ok"] is False
            assert envelope["error"]["code"] == code
