# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0


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

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    process = bootstrap._start_vikingbot_gateway(enable_logging=False, log_dir="/tmp/logs")

    assert process is not None
    assert captured["cmd"][:2] == ["vikingbot", "gateway"]
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

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

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

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        config_path=str(config_path),
    )

    assert process is not None
    assert captured["env"][OPENVIKING_CLI_CONFIG_ENV] == str(cli_config_path)


def test_start_vikingbot_gateway_preserves_explicit_cli_config_env(monkeypatch, tmp_path):
    captured = {}
    config_path = tmp_path / "ov.conf"
    colocated_cli_config = tmp_path / "ovcli.conf"
    explicit_cli_config = tmp_path / "custom-ovcli.conf"
    config_path.write_text("{}", encoding="utf-8")
    colocated_cli_config.write_text("{}", encoding="utf-8")
    explicit_cli_config.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)
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

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

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

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_run)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    process = bootstrap._start_vikingbot_gateway(enable_logging=False, log_dir="/tmp/logs")

    assert process is not None
    assert captured["probe_cmd"][1:] == ["-m", "vikingbot", "--help"]
    assert captured["probe_timeout"] == 15
    assert captured["cmd"][1:4] == ["-m", "vikingbot", "gateway"]
