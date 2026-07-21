# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import os
from types import SimpleNamespace

import openviking.server.bootstrap as bootstrap
from openviking.server.config import ServerConfig
from openviking.utils.agfs_utils import resolve_queuefs_mount_point
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from openviking_cli.utils.config.storage_config import StorageConfig


def test_main_keeps_config_host_when_cli_host_is_omitted(monkeypatch):
    config = ServerConfig(host="127.0.0.1", port=1933)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        bootstrap,
        "load_server_config",
        lambda config_path: config,
    )
    monkeypatch.setattr(
        bootstrap,
        "create_app",
        lambda config: "app",
    )
    monkeypatch.setattr(
        bootstrap,
        "configure_uvicorn_logging",
        lambda: None,
    )
    monkeypatch.setattr(
        bootstrap,
        "OpenVikingConfigSingleton",
        SimpleNamespace(initialize=lambda config_path: None),
        raising=False,
    )
    monkeypatch.setattr(
        bootstrap.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            host=None,
            port=None,
            config=None,
            workers=None,
            bot=False,
            with_bot=False,
            bot_url="http://localhost:18790",
            enable_bot_logging=None,
            bot_log_dir="/tmp/bot-logs",
        ),
    )
    monkeypatch.setattr(
        bootstrap.uvicorn,
        "run",
        lambda app, host, port, log_config=None, **kwargs: captured.update(
            {"app": app, "host": host, "port": port, "log_config": log_config, **kwargs}
        ),
    )

    bootstrap.main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 1933


def test_main_coerces_cli_host_all_to_none(monkeypatch):
    config = ServerConfig(host="127.0.0.1", port=1933)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        bootstrap,
        "load_server_config",
        lambda config_path: config,
    )
    monkeypatch.setattr(
        bootstrap,
        "create_app",
        lambda config: "app",
    )
    monkeypatch.setattr(
        bootstrap,
        "configure_uvicorn_logging",
        lambda: None,
    )
    monkeypatch.setattr(
        bootstrap,
        "OpenVikingConfigSingleton",
        SimpleNamespace(initialize=lambda config_path: None),
        raising=False,
    )
    monkeypatch.setattr(
        bootstrap.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            host="all",
            port=None,
            config=None,
            workers=None,
            bot=False,
            with_bot=False,
            bot_url="http://localhost:18790",
            enable_bot_logging=None,
            bot_log_dir="/tmp/bot-logs",
        ),
    )
    monkeypatch.setattr(
        bootstrap.uvicorn,
        "run",
        lambda app, host, port, log_config=None, **kwargs: captured.update(
            {"app": app, "host": host, "port": port, "log_config": log_config, **kwargs}
        ),
    )

    bootstrap.main()

    assert captured["host"] is None
    assert captured["port"] == 1933


def test_main_enables_bot_logging_when_with_bot_comes_from_config(monkeypatch):
    config = ServerConfig(host="127.0.0.1", port=1933, with_bot=True)
    captured: dict[str, object] = {}
    bot_process = object()

    monkeypatch.setattr(bootstrap, "load_server_config", lambda config_path: config)
    monkeypatch.setattr(bootstrap, "create_app", lambda config: "app")
    monkeypatch.setattr(bootstrap, "configure_uvicorn_logging", lambda: None)
    monkeypatch.setattr(
        OpenVikingConfigSingleton,
        "initialize",
        classmethod(lambda cls, config_path: None),
    )
    monkeypatch.setattr(
        bootstrap.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            host=None,
            port=None,
            config=None,
            workers=None,
            bot=False,
            with_bot=False,
            bot_port=bootstrap.VIKINGBOT_DEFAULT_PORT,
            enable_bot_logging=None,
            bot_log_dir="/tmp/bot-logs",
        ),
    )
    monkeypatch.setattr(bootstrap, "_abort_if_port_in_use", lambda port, label: None)

    def _fake_start(enable_logging, log_dir, port, **kwargs):
        captured.update(
            {
                "enable_logging": enable_logging,
                "log_dir": log_dir,
                "port": port,
            }
        )
        return bot_process

    monkeypatch.setattr(bootstrap, "_start_vikingbot_gateway", _fake_start)
    monkeypatch.setattr(bootstrap, "_stop_vikingbot_gateway", lambda process: None)
    monkeypatch.setattr(bootstrap.uvicorn, "run", lambda *args, **kwargs: None)

    bootstrap.main()

    assert captured == {
        "enable_logging": True,
        "log_dir": "/tmp/bot-logs",
        "port": bootstrap.VIKINGBOT_DEFAULT_PORT,
    }


def test_resolve_queuefs_mount_point_defaults_to_shared():
    config = StorageConfig()

    assert resolve_queuefs_mount_point(config) == "/queue"


def test_resolve_queuefs_mount_point_worker_mode_uses_process_index(monkeypatch):
    monkeypatch.setattr(
        "openviking.utils.agfs_utils.multiprocessing.current_process",
        lambda: SimpleNamespace(_identity=(3,)),
    )
    config = StorageConfig(agfs={"queuefs": {"mode": "worker"}})

    assert resolve_queuefs_mount_point(config) == "/queue/worker-2"


def test_resolve_queuefs_mount_point_worker_mode_falls_back_to_pid(monkeypatch):
    monkeypatch.setattr(
        "openviking.utils.agfs_utils.multiprocessing.current_process",
        lambda: SimpleNamespace(_identity=()),
    )
    monkeypatch.setattr(os, "getpid", lambda: 43210)
    config = StorageConfig(agfs={"queuefs": {"mode": "worker"}})

    assert resolve_queuefs_mount_point(config) == "/queue/worker-43210"
