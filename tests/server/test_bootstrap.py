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
        bootstrap,
        "_run_restartable_server",
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
        bootstrap,
        "_run_restartable_server",
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
    monkeypatch.setattr(bootstrap, "_run_restartable_server", lambda *args, **kwargs: False)

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


def test_restartable_server_drains_uvicorn_before_returning(monkeypatch):
    from fastapi import FastAPI

    app = FastAPI()
    server = SimpleNamespace(should_exit=False)

    def run():
        assert app.state.restart_controller.status()["supported"]
        app.state.restart_controller.request()
        app.state.restart_controller.stop()
        assert server.should_exit

    server.run = run
    monkeypatch.setattr(bootstrap.uvicorn, "Server", lambda config: server)
    assert bootstrap._run_restartable_server(app, host="127.0.0.1", port=1933)


@pytest.mark.parametrize("app_startup_fails", [False, True])
def test_main_restart_cleans_up_bot_and_preserves_launch_command(monkeypatch, app_startup_fails):
    import sys

    config = ServerConfig(host="127.0.0.1", port=1933, with_bot=True)
    steps = []
    monkeypatch.setattr(sys, "argv", ["openviking-server", "--with-bot", "--bot-port", "19000"])
    original_argv = [sys.executable, "/venv/bin/openviking-server", *sys.argv[1:]]
    monkeypatch.setattr(sys, "orig_argv", original_argv)
    monkeypatch.setattr(bootstrap, "load_server_config", lambda path: config)
    monkeypatch.setattr(bootstrap, "resolve_config_path", lambda *a: Path("/tmp/explicit.conf"))
    monkeypatch.setattr(
        OpenVikingConfigSingleton, "initialize", classmethod(lambda cls, **kw: None)
    )
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.detect_ollama_in_config", lambda c: (False, "", 0)
    )
    monkeypatch.setattr(bootstrap, "configure_uvicorn_logging", lambda: None)
    monkeypatch.setattr(bootstrap, "create_app", lambda *a, **kw: "app")
    monkeypatch.setattr(bootstrap, "_abort_if_port_in_use", lambda *a: None)
    monkeypatch.setattr(bootstrap, "_start_vikingbot_gateway", lambda *a, **kw: "bot")
    monkeypatch.setattr(
        bootstrap, "_stop_vikingbot_gateway", lambda bot: steps.append(("stop", bot))
    )
    monkeypatch.setattr(bootstrap, "_run_restartable_server", lambda *a, **kw: True)
    monkeypatch.setattr(
        os, "execv", lambda executable, args: steps.append(("exec", executable, args))
    )
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", "/tmp/explicit.conf")
    if app_startup_fails:
        def fail_app(*args, **kwargs):
            raise RuntimeError("application initialization failed")

        monkeypatch.setattr(bootstrap, "create_app", fail_app)
        with pytest.raises(RuntimeError, match="application initialization failed"):
            bootstrap.main()
        assert steps == [("stop", "bot")]
        return
    bootstrap.main()
    assert steps == [("stop", "bot"), ("exec", sys.executable, original_argv)]
    assert os.environ["OPENVIKING_CONFIG_FILE"] == "/tmp/explicit.conf"


@pytest.mark.parametrize("failure", ["bind", "lifespan", "bootstrap", "success", "retry_failure"])
def test_restart_recovery_through_real_exec(tmp_path, monkeypatch, failure):
    """Exercise socket/lifespan failures and a single recovery with real execv."""
    import hashlib
    import json
    import socket
    import subprocess
    import sys
    import textwrap

    from openviking.server.restart import RestartRecovery

    path = tmp_path / "ov.conf"
    original = b'{"port":0,"stage":"original"}\n'
    monkeypatch.setattr(OpenVikingConfigSingleton, "_config_file", path)
    monkeypatch.setattr(OpenVikingConfigSingleton, "_config_file_content", original)
    monkeypatch.delenv("OPENVIKING_RESTART_RECOVERY", raising=False)
    monkeypatch.delenv("OPENVIKING_RESTART_ROLLED_BACK", raising=False)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        path.write_text(json.dumps({"port": occupied.getsockname()[1], "stage": failure}))
        # A normal save backup may contain an intermediate, never-started draft.
        path.with_name("ov.conf.studio.bak").write_bytes(b'{"stage":"intermediate"}')
        RestartRecovery().prepare(hashlib.sha256(path.read_bytes()).hexdigest())
        backup = Path(json.loads(os.environ["OPENVIKING_RESTART_RECOVERY"])["backup"])
        assert backup.read_bytes() == original
        if os.name != "nt":
            assert backup.stat().st_mode & 0o777 == 0o600
        script = tmp_path / "restart_probe.py"
        script.write_text(
            textwrap.dedent("""\
            import asyncio, json, sys
            from contextlib import asynccontextmanager
            from pathlib import Path
            from fastapi import FastAPI
            import openviking.server.bootstrap as bootstrap

            def run(recovery):
                path = Path(sys.argv[1])
                config = json.loads(path.read_text())
                if config['stage'] in ('bootstrap', 'retry_failure') or (sys.argv[2] == 'retry_failure' and recovery.rolled_back):
                    raise SystemExit(1)
                @asynccontextmanager
                async def lifespan(app):
                    if config['stage'] == 'lifespan':
                        raise RuntimeError('startup failed')
                    asyncio.get_running_loop().call_later(0.2, lambda: setattr(app.state.restart_controller, 'requested', True))
                    asyncio.get_running_loop().call_later(0.3, lambda: app.state.restart_controller.stop())
                    yield
                app = FastAPI(lifespan=lifespan)
                bootstrap._run_restartable_server(app, recovery=recovery, host='127.0.0.1',
                    port=config['port'] if config['stage'] == 'bind' else 0, log_config=None)
                print('RECOVERED' if recovery.rolled_back else 'APPLIED')
            bootstrap._main = run
            bootstrap.main()
        """)
        )
        result = subprocess.run(
            [sys.executable, str(script), str(path), failure],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=40,
        )
    if failure == "retry_failure":
        assert result.returncode != 0
        assert result.stderr.count("retrying once") == 1
        assert path.read_bytes() == original
    else:
        assert result.returncode == 0, result.stderr
        assert ("APPLIED" if failure == "success" else "RECOVERED") in result.stdout
        assert (path.read_bytes() == original) == (failure != "success")
    assert not backup.exists()


def test_restart_recovery_does_not_overwrite_external_changes(tmp_path, monkeypatch):
    import hashlib

    from openviking.server.restart import RestartRecovery

    path = tmp_path / "ov.conf"
    path.write_bytes(b"new configuration")
    monkeypatch.setattr(OpenVikingConfigSingleton, "_config_file", path)
    monkeypatch.setattr(OpenVikingConfigSingleton, "_config_file_content", b"startup configuration")
    monkeypatch.delenv("OPENVIKING_RESTART_RECOVERY", raising=False)
    RestartRecovery().prepare(hashlib.sha256(path.read_bytes()).hexdigest())
    recovery = RestartRecovery()
    path.write_bytes(b"external edit")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        recovery.rollback()
    assert path.read_bytes() == b"external edit"
