# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""CLI tests that use real HTTP requests."""

import json
import os
import tempfile

from typer.testing import CliRunner

from openviking_cli.cli.main import app

runner = CliRunner()


def _make_ovcli_conf(url: str, tmp_dir: str) -> str:
    """Create a temporary ovcli.conf and return its path."""
    conf_path = os.path.join(tmp_dir, "ovcli.conf")
    with open(conf_path, "w") as f:
        json.dump({"url": url, "api_key": None}, f)
    return conf_path


def _run_cli(args, server_url, env=None, expected_exit_code=0):
    """Run a CLI command and optionally parse JSON output.

    Args:
        args: CLI arguments
        server_url: OpenViking server URL
        env: Extra environment variables
        expected_exit_code: Expected exit code (default 0)

    Returns:
        Parsed JSON payload if exit_code is 0 and output is valid JSON,
        otherwise the raw CliRunner Result.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        conf_path = _make_ovcli_conf(server_url, tmp_dir)
        merged_env = {"OPENVIKING_CLI_CONFIG_FILE": conf_path}
        if env:
            merged_env.update(env)
        result = runner.invoke(app, ["--json", *args], env=merged_env)
    assert result.exit_code == expected_exit_code, (
        f"Expected exit_code={expected_exit_code}, got {result.exit_code}\n"
        f"args={args}\n{result.output}"
    )
    if expected_exit_code != 0:
        return result
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return result
    assert payload["ok"] is True
    return payload


def test_requires_ovcli_conf():
    result = runner.invoke(
        app,
        ["find", "hello"],
        env={"OPENVIKING_CLI_CONFIG_FILE": "/tmp/nonexistent/ovcli.conf"},
    )

    assert result.exit_code == 2
    assert "ovcli.conf" in result.output


def test_cli_version():
    """--version should print version string and exit 0."""
    result = runner.invoke(app, ["--version"], env={})
    assert result.exit_code == 0
    assert "openviking" in result.output


def test_cli_help_smoke():
    """All commands should print help without errors."""
    commands = [
        # root
        ["--help"],
        # content
        ["read", "--help"],
        ["abstract", "--help"],
        ["overview", "--help"],
        # debug
        ["status", "--help"],
        ["health", "--help"],
        # filesystem
        ["ls", "--help"],
        ["tree", "--help"],
        ["mkdir", "--help"],
        ["rm", "--help"],
        ["mv", "--help"],
        ["stat", "--help"],
        # pack
        ["export", "--help"],
        ["import", "--help"],
        # relations
        ["relations", "--help"],
        ["link", "--help"],
        ["unlink", "--help"],
        # resources
        ["add-resource", "--help"],
        ["add-skill", "--help"],
        # search
        ["find", "--help"],
        ["search", "--help"],
        ["grep", "--help"],
        ["glob", "--help"],
        # serve
        ["serve", "--help"],
        # system
        ["wait", "--help"],
        # observer group
        ["observer", "--help"],
        ["observer", "queue", "--help"],
        ["observer", "vikingdb", "--help"],
        ["observer", "vlm", "--help"],
        ["observer", "system", "--help"],
        # session group
        ["session", "--help"],
        ["session", "new", "--help"],
        ["session", "list", "--help"],
        ["session", "get", "--help"],
        ["session", "delete", "--help"],
        ["session", "add-message", "--help"],
        ["session", "commit", "--help"],
    ]

    for args in commands:
        result = runner.invoke(app, args, env={})
        assert result.exit_code == 0, "command failed: {}\n{}".format(" ".join(args), result.output)


def test_cli_connection_refused():
    """Connecting to a non-existent server should exit with code 3."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        conf_path = _make_ovcli_conf("http://127.0.0.1:19999", tmp_dir)
        result = runner.invoke(
            app,
            ["--json", "health"],
            env={"OPENVIKING_CLI_CONFIG_FILE": conf_path},
        )
    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CONNECTION_ERROR"


def test_cli_real_requests(openviking_server, tmp_path):
    server_url = openviking_server

    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("OpenViking CLI real request test")

    # -- add resource --
    add_payload = _run_cli(["add-resource", str(sample_file)], server_url)
    root_uri = add_payload["result"].get("root_uri")
    assert root_uri
    assert isinstance(root_uri, str)

    file_uri = root_uri
    if file_uri.endswith("/"):
        file_uri = f"{file_uri}{sample_file.name}"

    # -- debug / system --
    health_payload = _run_cli(["health"], server_url)
    assert "result" in health_payload

    _run_cli(["status"], server_url)
    _run_cli(["observer", "queue"], server_url)
    _run_cli(["observer", "vikingdb"], server_url)
    _run_cli(["observer", "vlm"], server_url)
    _run_cli(["observer", "system"], server_url)

    # -- filesystem --
    ls_payload = _run_cli(["ls", "viking://resources/"], server_url)
    assert isinstance(ls_payload["result"], list)

    _run_cli(["tree", "viking://resources/"], server_url)
    _run_cli(["stat", file_uri], server_url)
    _run_cli(["read", "viking://resources/.abstract.md"], server_url)
    _run_cli(["abstract", "viking://resources"], server_url)
    _run_cli(["overview", "viking://resources"], server_url)

    # -- search --
    _run_cli(["grep", root_uri, "OpenViking"], server_url)
    _run_cli(["glob", "*.txt", "--uri", root_uri], server_url)
    _run_cli(["search", "OpenViking", "--uri", root_uri], server_url)

    # -- mkdir / mv / rm --
    mkdir_uri = "viking://resources/cli-temp-dir"
    moved_uri = "viking://resources/cli-temp-dir-moved"
    _run_cli(["mkdir", mkdir_uri], server_url)
    _run_cli(["mv", mkdir_uri, moved_uri], server_url)
    _run_cli(["rm", moved_uri], server_url)

    # -- relations --
    _run_cli(["relations", file_uri], server_url)
    _run_cli(["link", file_uri, root_uri, "--reason", "ref"], server_url)
    _run_cli(["unlink", file_uri, root_uri], server_url)

    # -- pack --
    export_path = tmp_path / "openviking_cli_test.ovpack"
    _run_cli(["export", file_uri, str(export_path)], server_url)
    _run_cli(
        ["import", str(export_path), "viking://resources/ovpack-import", "--no-vectorize"],
        server_url,
    )

    # -- session lifecycle --
    session_payload = _run_cli(["session", "new"], server_url)
    session_id = session_payload["result"]["session_id"]
    assert isinstance(session_id, str) and len(session_id) > 0

    list_payload = _run_cli(["session", "list"], server_url)
    assert isinstance(list_payload["result"], list)

    get_payload = _run_cli(["session", "get", session_id], server_url)
    assert get_payload["result"]["session_id"] == session_id

    _run_cli(
        [
            "session",
            "add-message",
            session_id,
            "--role",
            "user",
            "--content",
            "hello",
        ],
        server_url,
    )
    _run_cli(["session", "delete", session_id], server_url)


def test_cli_nonexistent_resource(openviking_server):
    """Accessing a non-existent resource should fail with exit code 1."""
    result = _run_cli(
        ["stat", "viking://resources/does-not-exist-xyz"],
        openviking_server,
        expected_exit_code=1,
    )
    assert result.exit_code == 1
