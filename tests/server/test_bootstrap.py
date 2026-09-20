# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import openviking.server.app as app_module
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
        lambda config, **kwargs: "app",
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
        lambda config, **kwargs: "app",
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


def _stub_main_args(monkeypatch, **overrides):
    """Stub the CLI args bootstrap.main() reads, keeping tests focused."""
    values = {
        "host": None,
        "port": None,
        "config": None,
        "workers": None,
        "bot": False,
        "with_bot": False,
        "bot_port": bootstrap.VIKINGBOT_DEFAULT_PORT,
        "enable_bot_logging": None,
        "bot_log_dir": "/tmp/bot-logs",
    }
    values.update(overrides)
    monkeypatch.setattr(
        bootstrap.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(**values),
    )


def _stub_managed_config(monkeypatch, config):
    """Common patches so bootstrap.main() runs without a real server."""
    monkeypatch.setattr(bootstrap, "load_server_config", lambda config_path: config)
    monkeypatch.setattr(bootstrap, "create_app", lambda config, **kwargs: "app")
    monkeypatch.setattr(bootstrap, "configure_uvicorn_logging", lambda: None)
    monkeypatch.setattr(
        OpenVikingConfigSingleton,
        "initialize",
        classmethod(lambda cls, config_path: None),
    )
    monkeypatch.setattr(bootstrap.uvicorn, "run", lambda *args, **kwargs: None)


def test_main_does_not_spawn_a_gateway_for_an_external_bot_url(monkeypatch, capsys):
    config = ServerConfig(
        host="127.0.0.1",
        port=1933,
        bot_api_url="http://127.0.0.1:18790",
        bot_gateway_token="gateway-token",
    )
    spawned: list[object] = []
    abort_checks: list[int] = []

    _stub_managed_config(monkeypatch, config)
    _stub_main_args(monkeypatch)
    monkeypatch.setattr(
        bootstrap, "_abort_if_port_in_use", lambda port, label: abort_checks.append(port)
    )
    monkeypatch.setattr(
        bootstrap,
        "_start_vikingbot_gateway",
        lambda *args, **kwargs: spawned.append(args) or object(),
    )
    monkeypatch.setattr(bootstrap, "_is_bot_gateway_reachable", lambda url, timeout=0.5: True)

    bootstrap.main()

    assert spawned == []
    assert abort_checks == []
    assert "external" in capsys.readouterr().out


def test_main_warns_when_the_external_gateway_is_unreachable(monkeypatch, capsys):
    config = ServerConfig(
        host="127.0.0.1",
        port=1933,
        bot_api_url="http://127.0.0.1:18790",
        bot_gateway_token="gateway-token",
    )

    _stub_managed_config(monkeypatch, config)
    _stub_main_args(monkeypatch)
    monkeypatch.setattr(bootstrap, "_is_bot_gateway_reachable", lambda url, timeout=0.5: False)

    bootstrap.main()

    assert "no VikingBot gateway is answering" in capsys.readouterr().err


def test_is_bot_gateway_reachable_rejects_unparseable_urls():
    assert bootstrap._is_bot_gateway_reachable("") is False
    assert bootstrap._is_bot_gateway_reachable("127.0.0.1:18790") is False


def test_main_enables_bot_logging_when_with_bot_comes_from_config(monkeypatch):
    config = ServerConfig(host="127.0.0.1", port=1933, with_bot=True)
    captured: dict[str, object] = {}
    bot_process = object()

    monkeypatch.setattr(bootstrap, "load_server_config", lambda config_path: config)
    monkeypatch.setattr(bootstrap, "create_app", lambda config, **kwargs: "app")
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


def test_bot_alias_propagates_resolved_config_to_workers(monkeypatch):
    config = ServerConfig(host="127.0.0.1", port=1933)
    captured = {}
    original_parse_args = bootstrap.argparse.ArgumentParser.parse_args

    def parse_bot_alias(parser):
        args = original_parse_args(parser, ["--bot", "--workers", "2", "--bot-port", "19000"])
        captured["with_bot"] = args.with_bot
        return args

    monkeypatch.setattr(bootstrap.argparse.ArgumentParser, "parse_args", parse_bot_alias)
    monkeypatch.setattr(bootstrap, "load_server_config", lambda config_path: config)
    monkeypatch.setattr(bootstrap, "create_app", lambda config, **kwargs: "parent-app")
    monkeypatch.setattr(bootstrap, "configure_uvicorn_logging", lambda: None)
    monkeypatch.setattr(bootstrap, "_abort_if_port_in_use", lambda port, label: None)
    monkeypatch.setattr(bootstrap, "_start_vikingbot_gateway", lambda *args, **kwargs: object())
    monkeypatch.setattr(bootstrap, "_stop_vikingbot_gateway", lambda process: None)
    monkeypatch.setattr(
        OpenVikingConfigSingleton,
        "initialize",
        classmethod(lambda cls, config_path: None),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.detect_ollama_in_config",
        lambda config: (False, "127.0.0.1", 11434),
    )
    monkeypatch.setattr(
        bootstrap.uvicorn,
        "run",
        lambda app, **kwargs: captured.update({"app": app, **kwargs}),
    )

    with monkeypatch.context() as worker_env:
        worker_env.delenv(app_module.WORKER_WITH_BOT_ENV, raising=False)
        worker_env.delenv(app_module.WORKER_BOT_API_URL_ENV, raising=False)

        bootstrap.main()

        assert captured["with_bot"] is True
        assert captured["app"] == "openviking.server.app:create_worker_app"
        assert os.environ[app_module.WORKER_WITH_BOT_ENV] == "1"
        assert os.environ[app_module.WORKER_BOT_API_URL_ENV] == "http://127.0.0.1:19000"


def test_main_prints_config_diagnostics_on_validation_error(monkeypatch, capsys):
    """An unknown top-level config field must exit 1 with actionable diagnostics."""
    config = ServerConfig(host="127.0.0.1", port=1933)

    monkeypatch.setattr(bootstrap, "resolve_config_path", lambda *a, **k: Path("/tmp/ov.conf"))
    monkeypatch.setattr(bootstrap, "load_server_config", lambda *a, **k: config)

    def failing_initialize(cls, config_path):
        raise ValueError("Unknown config field 'claude_code' in OpenVikingConfig")

    monkeypatch.setattr(
        OpenVikingConfigSingleton,
        "initialize",
        classmethod(failing_initialize),
    )
    monkeypatch.setattr(
        bootstrap.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            host=None,
            port=None,
            config="/tmp/ov.conf",
            workers=None,
            bot=False,
            with_bot=False,
            bot_url="http://localhost:18790",
            enable_bot_logging=None,
            bot_log_dir="/tmp/bot-logs",
        ),
    )

    with pytest.raises(SystemExit) as exc:
        bootstrap.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "/tmp/ov.conf" in err
    assert "Unknown config field 'claude_code'" in err
    assert "openviking-server doctor" in err
    assert "examples/ov.conf.example" in err


def test_worker_factory_replays_bot_overrides(monkeypatch):
    config = ServerConfig(with_bot=False, bot_api_url="http://from-config")
    captured = {}
    monkeypatch.setenv(app_module.WORKER_WITH_BOT_ENV, "1")
    monkeypatch.setenv(app_module.WORKER_BOT_API_URL_ENV, "http://127.0.0.1:19000")
    monkeypatch.setattr(
        app_module,
        "resolve_config_path",
        lambda config_path, env_var, default_name: "/tmp/worker-ov.conf",
    )
    monkeypatch.setattr(
        app_module,
        "load_server_config",
        lambda config_path: captured.update({"load_path": config_path}) or config,
    )
    monkeypatch.setattr(
        app_module,
        "create_app",
        lambda worker_config, config_path: captured.update(
            {
                "app_config": worker_config,
                "app_config_path": config_path,
            }
        )
        or worker_config,
    )

    worker_config = app_module.create_worker_app()

    assert worker_config.with_bot is True
    assert worker_config.bot_api_url == "http://127.0.0.1:19000"
    assert captured == {
        "load_path": "/tmp/worker-ov.conf",
        "app_config": config,
        "app_config_path": "/tmp/worker-ov.conf",
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


def test_configure_default_executor_uses_configured_size(monkeypatch):
    created: dict[str, object] = {}

    class _Executor:
        def __init__(self, *, max_workers, thread_name_prefix):
            created["max_workers"] = max_workers
            created["thread_name_prefix"] = thread_name_prefix

    class _Loop:
        def set_default_executor(self, executor):
            created["executor"] = executor

    monkeypatch.setattr(app_module, "ThreadPoolExecutor", _Executor)
    monkeypatch.setattr(app_module.asyncio, "get_running_loop", lambda: _Loop())

    app_module._configure_default_executor(ServerConfig(executor_threads=48))

    assert created["max_workers"] == 48
    assert created["thread_name_prefix"] == "openviking-asyncio"
    assert created["executor"].__class__ is _Executor


def test_configure_default_executor_keeps_python_default_when_zero(monkeypatch):
    monkeypatch.setattr(
        app_module.asyncio,
        "get_running_loop",
        lambda: pytest.fail("event loop should not be touched when the setting is zero"),
    )

    app_module._configure_default_executor(ServerConfig())
