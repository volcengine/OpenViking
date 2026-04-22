# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import openviking.server.bootstrap as bootstrap


class _FakeProcess:
    pid = 12345

    def poll(self):
        return None


def test_start_vikingbot_gateway_forces_localhost_host(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
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

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
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
