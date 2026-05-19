# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI compatibility checks: binary availability, version, GLIBC, config, server connectivity."""

import json
import os
import platform
import subprocess

import pytest

from conftest import CLI_BIN, CLI_COMPATIBLE, CLI_CONFIG_PATH, _env, ov

pytestmark = pytest.mark.cli_remote


class TestCLIBinaryAvailability:
    def test_binary_exists(self):
        assert CLI_BIN is not None, (
            "openviking CLI binary should be found. "
            "Install via: curl -fsSL http://openviking.tos-cn-beijing.volces.com/cli/install.sh | bash"
        )

    def test_binary_is_executable(self):
        assert os.path.isfile(CLI_BIN), f"CLI binary path should be a file: {CLI_BIN}"
        assert os.access(CLI_BIN, os.X_OK), f"CLI binary should be executable: {CLI_BIN}"


class TestCLIVersion:
    def test_version_command(self):
        r = ov(["version"])
        assert r["exit_code"] == 0, (
            f"ov version should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert len(r["stdout"]) > 0, "version output should not be empty"

    def test_version_flag(self):
        r = ov(["--version"])
        assert r["exit_code"] == 0, (
            f"ov --version should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert len(r["stdout"]) > 0, "--version output should not be empty"

    def test_version_format(self):
        r = ov(["version", "-o", "json"])
        if r["exit_code"] == 0 and r["json"] is not None:
            data = r["json"]
            assert data.get("ok") is True or "version" in str(data).lower(), (
                f"version JSON should contain version info, got: {r['stdout'][:200]}"
            )


class TestCLIGlibcCompat:
    def test_no_glibc_error(self):
        result = subprocess.run(
            [CLI_BIN, "version"], capture_output=True, text=True, timeout=10, env=_env(),
        )
        assert "GLIBC" not in result.stderr, (
            f"CLI should not have GLIBC compatibility issues. stderr: {result.stderr[:300]}"
        )


class TestCLIConfigCompat:
    def test_config_file_exists(self):
        assert os.path.isfile(CLI_CONFIG_PATH), (
            f"CLI config file should exist at {CLI_CONFIG_PATH}"
        )

    def test_config_file_valid_json(self):
        with open(CLI_CONFIG_PATH, "r") as f:
            config = json.load(f)
        assert "url" in config, "config should contain 'url' field"
        assert "api_key" in config, "config should contain 'api_key' field"
        assert "timeout" in config, "config should contain 'timeout' field"

    def test_config_url_set(self):
        with open(CLI_CONFIG_PATH, "r") as f:
            config = json.load(f)
        assert config["url"], "config 'url' should not be empty"


class TestCLIServerConnectivity:
    def test_health_check(self):
        r = ov(["health", "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov health should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )

    def test_server_reachable(self):
        r = ov(["health", "-o", "json"])
        assert r["exit_code"] != 3, (
            "CLI exit code 3 means connection error - server is unreachable"
        )


class TestCLIOutputFormats:
    def test_json_output(self, test_dir_uri):
        r = ov(["ls", test_dir_uri, "-o", "json"])
        assert r["exit_code"] == 0, (
            f"ov ls -o json should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )
        assert r["json"] is not None, "JSON output should be parseable"

    def test_table_output(self, test_dir_uri):
        r = ov(["ls", test_dir_uri, "-o", "table"])
        assert r["exit_code"] == 0, (
            f"ov ls -o table should exit 0, got {r['exit_code']}: {r['stderr'][:300]}"
        )


class TestCLIExitCodes:
    def test_success_exit_code(self):
        r = ov(["health", "-o", "json"])
        assert r["exit_code"] == 0, "successful command should exit with code 0"

    def test_error_exit_code_for_invalid_uri(self):
        r = ov(["stat", "viking://nonexistent/path/xyz", "-o", "json"])
        assert r["exit_code"] != 0, "invalid URI should return non-zero exit code"


class TestCLIPlatformCompat:
    def test_platform_info(self):
        system = platform.system()
        machine = platform.machine()
        assert system in ("Linux", "Darwin", "Windows"), (
            f"Unsupported platform: {system}"
        )
        if system == "Linux":
            assert machine in ("x86_64", "aarch64"), (
                f"Unsupported Linux architecture: {machine}"
            )
        elif system == "Darwin":
            assert machine in ("x86_64", "arm64"), (
                f"Unsupported macOS architecture: {machine}"
            )
