# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import os
from types import SimpleNamespace

import openviking.server.bootstrap as bootstrap
from openviking.server.config import ServerConfig
from openviking.utils.agfs_utils import resolve_queuefs_mount_point
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
