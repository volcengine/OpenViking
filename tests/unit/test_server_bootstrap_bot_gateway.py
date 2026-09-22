# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0


import json
from types import SimpleNamespace

import pytest

import openviking.server.bootstrap as bootstrap
from openviking_cli.utils.config.consts import OPENVIKING_CLI_CONFIG_ENV


class _FakeProcess:
    pid = 12345

    def poll(self):
        return None


def test_start_vikingbot_gateway_forces_localhost_host(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["process_options"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap, "_wait_for_bot_ready", lambda *_: None)

    process = bootstrap._start_vikingbot_gateway(enable_logging=False, log_dir="/tmp/logs")

    assert process is not None
    assert captured["cmd"][:2] == ["vikingbot", "gateway"]
    assert captured["process_options"]["start_new_session"] == (bootstrap.os.name != "nt")
    assert "--host" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--host") + 1] == "127.0.0.1"
    assert "--port" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--port") + 1] == str(
        bootstrap.VIKINGBOT_DEFAULT_PORT
    )


def test_start_vikingbot_gateway_uses_custom_port(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["process_options"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap, "_wait_for_bot_ready", lambda *_: None)

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        port=19990,
    )

    assert process is not None
    assert captured["cmd"][captured["cmd"].index("--host") + 1] == "127.0.0.1"
    assert captured["cmd"][captured["cmd"].index("--port") + 1] == "19990"


def test_start_vikingbot_gateway_prefers_colocated_ovcli_conf(monkeypatch, tmp_path):
    captured = {}
    config_path = tmp_path / "ov.conf"
    cli_config_path = tmp_path / "ovcli.conf"
    config_path.write_text("{}", encoding="utf-8")
    cli_config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["process_options"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap, "_wait_for_bot_ready", lambda *_: None)
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        config_path=str(config_path),
    )

    assert process is not None
    assert captured["env"][OPENVIKING_CLI_CONFIG_ENV] == str(cli_config_path)
    assert captured["cmd"][captured["cmd"].index("--config") + 1] == str(config_path)


def test_start_vikingbot_gateway_preserves_explicit_cli_config_env(monkeypatch, tmp_path):
    captured = {}
    config_path = tmp_path / "ov.conf"
    colocated_cli_config = tmp_path / "ovcli.conf"
    explicit_cli_config = tmp_path / "custom-ovcli.conf"
    config_path.write_text("{}", encoding="utf-8")
    colocated_cli_config.write_text("{}", encoding="utf-8")
    explicit_cli_config.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["process_options"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap, "_wait_for_bot_ready", lambda *_: None)
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(explicit_cli_config))

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        config_path=str(config_path),
    )

    assert process is not None
    assert captured["env"][OPENVIKING_CLI_CONFIG_ENV] == str(explicit_cli_config)


def test_start_vikingbot_gateway_passes_managed_server_runtime(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None, **kwargs):
        captured["env"] = env
        captured["process_options"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap, "_wait_for_bot_ready", lambda *_: None)

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        managed_server_url="http://127.0.0.1:1940",
    )

    assert process is not None
    assert captured["env"]["VIKINGBOT_WITH_OPENVIKING_SERVER"] == "1"
    assert captured["env"]["VIKINGBOT_MANAGED_OV_SERVER_URL"] == "http://127.0.0.1:1940"


def test_start_vikingbot_gateway_allows_slow_module_probe(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: None)

    def _fake_run(cmd, capture_output=None, timeout=None):
        captured["probe_cmd"] = cmd
        captured["probe_timeout"] = timeout
        return type("Result", (), {"returncode": 0})()

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_run)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap, "_wait_for_bot_ready", lambda *_: None)

    process = bootstrap._start_vikingbot_gateway(enable_logging=False, log_dir="/tmp/logs")

    assert process is not None
    assert captured["probe_cmd"][1:] == ["-m", "vikingbot", "--help"]
    assert captured["probe_timeout"] == 15
    assert captured["cmd"][1:4] == ["-m", "vikingbot", "gateway"]


def test_readiness_waits_for_matching_child_pid(monkeypatch, tmp_path):
    status = tmp_path / "status.json"
    process = SimpleNamespace(pid=123, poll=lambda: None)
    status.write_text(json.dumps({"pid": 999, "status": "ready"}))
    polls = []

    def advance(_):
        polls.append(1)
        state = "starting" if len(polls) == 1 else "ready"
        status.write_text(json.dumps({"pid": 123, "status": state}))

    monkeypatch.setattr(bootstrap.time, "sleep", advance)
    bootstrap._wait_for_bot_ready(process, status)
    assert len(polls) == 2


def test_readiness_reports_sandbox_failure(tmp_path):
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"pid": 123, "status": "failed", "error": "Docker missing"}))
    process = SimpleNamespace(pid=123, poll=lambda: None)
    with pytest.raises(RuntimeError, match="Docker missing"):
        bootstrap._wait_for_bot_ready(process, status)


def test_readiness_rejects_early_exit(tmp_path):
    process = SimpleNamespace(pid=123, poll=lambda: 1, returncode=1)
    with pytest.raises(RuntimeError, match="exited before becoming ready"):
        bootstrap._wait_for_bot_ready(process, tmp_path / "status.json")


def test_failed_readiness_terminates_owned_child(monkeypatch, tmp_path):
    from unittest.mock import Mock

    process = Mock(pid=123)
    process.poll.return_value = None
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/bin/vikingbot")
    monkeypatch.setattr(bootstrap.subprocess, "Popen", lambda *_, **__: process)

    def fail(*_):
        raise TimeoutError("startup timeout")

    monkeypatch.setattr(bootstrap, "_wait_for_bot_ready", fail)
    assert bootstrap._start_vikingbot_gateway(False, str(tmp_path)) is None
    process.terminate.assert_called_once()
    process.wait.assert_called_once()
