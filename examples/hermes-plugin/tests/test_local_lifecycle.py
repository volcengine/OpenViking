"""Ownership, restart and installer regressions through the external package."""

import hashlib
import importlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def modules(external_provider):
    home, provider, module, _settings = external_provider("lifecycle")
    ql = importlib.import_module(module.__name__ + ".quick_local")
    lifecycle = importlib.import_module(module.__name__ + ".local_server")
    packages = importlib.import_module(module.__name__ + ".local_packages")
    return home, provider, module, ql, lifecycle, packages


@pytest.fixture
def servers(modules, monkeypatch):
    home, _provider, _module, ql, lifecycle, _packages = modules
    monkeypatch.setattr(ql, "_pm_available", lambda: False)
    available = ql.find_available_port
    first_port = 20000 + int(hashlib.sha256(str(home).encode()).hexdigest()[:8], 16) % 40000
    monkeypatch.setattr(
        ql,
        "find_available_port",
        lambda **kwargs: available(first_port=first_port, attempts=20, **kwargs),
    )
    children = []

    def spawn(endpoint, config, hermes_home, command):
        host, port = ql._endpoint_bind(endpoint)
        process = subprocess.Popen(
            [
                sys.executable,
                str(command),
                "--config",
                str(config),
                "--host",
                host,
                "--port",
                str(port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        children.append(process)
        return process

    monkeypatch.setattr(ql, "_start_validation_server", spawn)

    def create(name):
        server = lifecycle.LocalServer(home.parent / name)
        command = server.paths.server_command
        command.parent.mkdir(parents=True)
        command.write_text(
            """import argparse,json,os,subprocess,sys,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
p=argparse.ArgumentParser();p.add_argument('--config');p.add_argument('--host');p.add_argument('--port',type=int);a=p.parse_args()
c=json.load(open(a.config));key=c['server']['root_api_key']
if c['vlm'].get('crash'):time.sleep(.2);raise SystemExit(9)
if c['vlm'].get('child'):
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
 Path(a.config+'.child').write_text(str(child.pid))
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  ok=self.path=='/health' or self.headers.get('X-API-Key')==key
  self.send_response(200 if ok else 401);self.end_headers()
  self.wfile.write(json.dumps({'status':'ok','healthy':True,'version':'0.4.22','auth_mode':'trusted','root_api_key_required':True}).encode())
# Match asyncio/Uvicorn: Windows listeners do not share an active address.
HTTPServer.allow_reuse_address = os.name != 'nt'
HTTPServer((a.host,a.port),Handler).serve_forever()
""",
            encoding="utf-8",
        )
        config = ql.build_server_config(server.paths, {"model": "first"}, port=1933)
        server.configure(config)
        return server

    yield create
    for process in children:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


def ready(server):
    # Windows connection-refused probes can each take over one second.
    return server.wait_ready(server.start(), lambda _url: (True, ""), timeout=20)


@pytest.mark.parametrize("action", ["start", "restart"])
@pytest.mark.parametrize("memory", [
    {"provider": "openviking", "openviking": {"deployment": "cloud"}},
    {"provider": "openviking", "openviking": {}},
    {"provider": "honcho", "openviking": {"deployment": "quick_local"}},
])
def test_cli_refuses_start_when_quick_local_is_not_selected(servers, modules, monkeypatch, capsys, action, memory):
    import json
    from argparse import Namespace

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    server = servers("inactive-cli")
    home = server.paths.root.parent
    config = home / "config.yaml"
    config.write_text(json.dumps({"memory": memory}), encoding="utf-8")
    before = config.read_bytes()
    cli = importlib.import_module(modules[3].__package__ + ".cli")
    monkeypatch.setattr(cli, "get_hermes_home", lambda: home)
    token = set_hermes_home_override(home)
    try:
        with pytest.raises(SystemExit) as caught:
            cli._run(Namespace(local_action=action))
        assert caught.value.code == 1
        assert "not using Quick Local" in capsys.readouterr().out
        assert server.status()["state"] == "stopped"
        assert config.read_bytes() == before
    finally:
        reset_hermes_home_override(token)


def test_cli_controls_selected_and_retained_server(servers, modules, monkeypatch, capsys):
    import json
    from argparse import Namespace

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    server = servers("selected-cli")
    home = server.paths.root.parent
    config = home / "config.yaml"
    config.write_text(json.dumps({"memory": {"provider": "openviking", "openviking": {
        "deployment": "quick_local"}}}), encoding="utf-8")
    cli = importlib.import_module(modules[3].__package__ + ".cli")
    monkeypatch.setattr(cli, "get_hermes_home", lambda: home)
    token = set_hermes_home_override(home)
    try:
        cli._run(Namespace(local_action="start"))
        assert server.status()["state"] == "ready"
        old_pid = server.status()["pid"]
        cli._run(Namespace(local_action="restart"))
        assert server.status()["state"] == "ready"
        assert server.status()["pid"] != old_pid
        config.write_text(json.dumps({"memory": {"provider": "openviking", "openviking": {
            "deployment": "cloud"}}}), encoding="utf-8")
        cli._run(Namespace(local_action="status"))
        assert "Quick Local: ready" in capsys.readouterr().out
        cli._run(Namespace(local_action="stop"))
        assert server.status()["state"] == "stopped"
    finally:
        reset_hermes_home_override(token)


def test_restart_keeps_data_and_changes_only_owned_process(servers):
    a, b = servers("a"), servers("b")
    ready(a)
    ready(b)
    old_a, old_b = a.status()["pid"], b.status()["pid"]
    a.paths.workspace.mkdir()
    marker = a.paths.workspace / "existing-memory"
    marker.write_text("keep")
    config = a._config()
    config["vlm"]["model"] = "second"
    a.configure(config)
    restarted = ready(a)
    assert not restarted.reused
    assert a.status()["pid"] != old_a
    assert b.status()["pid"] == old_b
    assert marker.read_text() == "keep"
    assert not a.paths.restart_required_marker.exists()


def test_foreign_saved_port_moves_endpoint_without_stopping_foreign_server(servers):
    a, b = servers("a"), servers("b")
    ready(a)
    endpoint = a.status()["endpoint"]
    ready(b)
    a.stop()
    config = b._config()
    config["server"]["port"] = a.ql._endpoint_port(endpoint)
    b.configure(config)
    ready(b)
    foreign_pid = b.status()["pid"]
    moved = ready(a)
    assert moved.endpoint != endpoint
    assert a.ql.connection_config({}, a.paths.root.parent)["url"] == moved.endpoint
    assert b.status()["pid"] == foreign_pid
    assert a.status()["state"] == "ready"


def test_concurrent_starts_of_one_profile_create_one_process(servers):
    a = servers("a")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: a.start(), range(2)))
    assert first.process.pid == second.process.pid
    a.wait_ready(first, lambda _url: (True, ""), timeout=20)


def test_two_profiles_can_recover_a_concurrent_port_race(servers):
    a, b = servers("a"), servers("b")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(ready, (a, b)))
    assert first.endpoint != second.endpoint
    assert a.status()["state"] == b.status()["state"] == "ready"


@pytest.mark.parametrize("alter", ["pid", "created", "command", "port"])
def test_process_record_cannot_authorize_stopping_a_different_process(servers, alter):
    a, b = servers("a"), servers("b")
    ready(a)
    ready(b)
    record = a._read_record()
    foreign = b._read_record()
    record[alter] = foreign[alter] if alter != "created" else record[alter] + 1
    # Both servers can use the same port only before recovery. Ensure port differs.
    if alter == "port":
        record["port"] += 100
    assert a._verified_process(record) is None
    assert b.status()["state"] == "ready"


def test_early_server_exit_fails_promptly_and_clears_process_record(servers):
    a = servers("a")
    config = a._config()
    config["vlm"]["crash"] = True
    a.configure(config)
    started = a.start()
    assert started.process.wait(timeout=10) == 9
    health = MagicMock()
    with pytest.raises(a.ql.QuickLocalSetupError, match="did not become ready"):
        a.wait_ready(started, health, timeout=600)
    health.assert_not_called()
    assert not a.record_path.exists()


def test_temporary_validation_stop_also_stops_launcher_child(servers):
    import psutil

    a = servers("a")
    config = a._config()
    config["vlm"]["child"] = True
    a.configure(config)
    started = ready(a)
    child = psutil.Process(int(a.paths.server_config.with_name("ov.conf.child").read_text()))
    assert child.is_running()
    assert a.ql._stop_process(started.process)
    assert started.process.poll() is not None
    assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE


def test_restore_repairs_private_modes_without_touching_external_paths(modules, tmp_path):
    home, _p, _m, ql, lifecycle, _packages = modules
    server = lifecycle.LocalServer(home)
    server.configure(ql.build_server_config(server.paths, {"model": "first"}))
    server.paths.root.chmod(0o755)
    server.paths.server_config.chmod(0o644)
    server.paths.ovcli_config.chmod(0o644)
    ql.connection_config({}, home)
    if os.name != "nt":
        assert server.paths.root.stat().st_mode & 0o777 == 0o700
        assert server.paths.server_config.stat().st_mode & 0o777 == 0o600
        assert server.paths.ovcli_config.stat().st_mode & 0o777 == 0o600
    outside = tmp_path / "outside"
    outside.write_text("unrelated")
    server.paths.ovcli_config.unlink()
    try:
        server.paths.ovcli_config.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ql.QuickLocalSetupError, match="private files"):
        ql.connection_config({}, home)
    assert outside.read_text() == "unrelated"


@pytest.mark.parametrize("error", [OSError("spawn"), RuntimeError("installer")])
def test_unexpected_start_failure_clears_flag_and_reaches_cli(modules, monkeypatch, error):
    home, provider, _module, ql, lifecycle, _packages = modules
    provider._hermes_home = str(home)
    provider._endpoint = "http://127.0.0.1:1933"
    monkeypatch.setattr(
        provider, "_profile_config_and_env", lambda: ({"deployment": ql.DEPLOYMENT}, {})
    )
    monkeypatch.setattr(lifecycle.LocalServer, "start", MagicMock(side_effect=error))
    warnings = []
    provider._handle_runtime_openviking_unreachable(warning_callback=warnings.append)
    assert not provider._runtime_start_pending
    assert len(warnings) == 1 and type(error).__name__ in warnings[0]
    provider._handle_runtime_openviking_unreachable(warning_callback=warnings.append)
    assert len(warnings) == 2


def test_cold_import_timeout_does_not_trigger_reinstallation(modules, monkeypatch):
    home, _p, _m, ql, _life, _packages = modules
    paths = ql.managed_paths(home)
    monkeypatch.setattr(ql, "_pm_available", lambda: False)
    paths.runtime_python.parent.mkdir(parents=True)
    paths.runtime_python.touch()
    paths.server_command.touch()
    monkeypatch.setattr(
        ql.subprocess, "run", MagicMock(side_effect=subprocess.TimeoutExpired("probe", 120))
    )
    engine = ql.QuickLocalSetup(health_check=lambda _url: (False, ""))
    install = MagicMock()
    monkeypatch.setattr(engine, "_install_with_uv", install)
    with pytest.raises(ql.QuickLocalSetupError, match="not reinstalled"):
        engine._ensure_openviking_installed(paths)
    install.assert_not_called()


@pytest.mark.parametrize("failure", [1, subprocess.TimeoutExpired("embedding", 660)])
def test_failed_native_embedding_does_not_activate_setup(modules, monkeypatch, failure):
    home, _p, _m, ql, _life, _packages = modules
    monkeypatch.setattr(ql, "_validate_vlm", lambda *_args: None)
    monkeypatch.setattr(ql, "_pm_available", lambda: False)
    monkeypatch.setattr(ql, "_private_child_env", lambda target: {"HERMES_HOME": str(target)})
    run = MagicMock()
    if isinstance(failure, Exception):
        run.side_effect = failure
    else:
        run.return_value.returncode = failure
    monkeypatch.setattr(ql.subprocess, "run", run)
    start = MagicMock()
    monkeypatch.setattr(ql, "_start_validation_server", start)
    paths = ql.managed_paths(home)
    engine = ql.QuickLocalSetup(health_check=lambda _url: (True, ""))
    with pytest.raises(ql.QuickLocalSetupError, match="embedding"):
        engine._validate_generated_config(
            paths=paths,
            endpoint="http://127.0.0.1:1933",
            server_config=ql.build_server_config(paths, {"model": "test"}),
        )
    start.assert_not_called()
    assert not paths.server_config.exists() and not paths.ovcli_config.exists()
    assert run.call_args.kwargs["timeout"] == ql._HEALTH_TIMEOUT_SECONDS
    assert run.call_args.kwargs["env"]["HERMES_HOME"] == str(home)
    assert "math.isfinite" in run.call_args.args[0][-1]


def test_platform_without_reviewed_binary_requires_explicit_consent(modules, monkeypatch):
    _home, _p, _m, ql, _life, packages = modules
    monkeypatch.setattr(packages.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(packages.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(packages.platform, "mac_ver", lambda: ("15.0", (), ""))
    with pytest.raises(ql.SourceBuildRequired, match="explicitly allow"):
        packages.install_requirements()
    assert "llama-cpp-python==0.3.30" in packages.install_requirements(allow_source_build=True)


def test_broken_pm_import_never_calls_legacy_uv_shim(modules, monkeypatch):
    import builtins

    home, _p, _m, ql, _life, _packages = modules
    monkeypatch.setattr(ql, "openviking_install_satisfies_requirement", lambda _paths: False)
    monkeypatch.setattr(ql, "_pm_available", lambda: True)
    original = builtins.__import__

    def import_module(name, *args, **kwargs):
        if name == "pm.client":
            raise ImportError("broken transitive PM dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)
    engine = ql.QuickLocalSetup(health_check=lambda _url: (False, ""))
    fallback = MagicMock()
    monkeypatch.setattr(engine, "_install_with_uv", fallback)
    with pytest.raises(ImportError, match="transitive"):
        engine._ensure_openviking_installed(ql.managed_paths(home))
    fallback.assert_not_called()


@pytest.mark.parametrize("served_profiles", [False, True])
def test_legacy_installer_uses_profile_child_environment(modules, monkeypatch, served_profiles):
    from hermes_cli import managed_uv
    from tools.environments import local as subprocess_env

    home, _p, _m, ql, _life, _packages = modules
    source_env = {
        "PATH": os.defpath,
        "HERMES_HOME": "launch-profile",
        "OPENVIKING_API_KEY": "profile-key",
        "PYTHONPATH": "host-pythonpath",
        "PYTHONHOME": "host-pythonhome",
        "VIRTUAL_ENV": "host-runtime",
    }
    child_env = MagicMock(return_value=source_env.copy())
    if served_profiles:
        source_env["HERMES_HOME"] = str(home)
        child_env.return_value = source_env.copy()
        monkeypatch.setattr(subprocess_env, "served_profile_child_env", child_env, raising=False)
    else:
        monkeypatch.delattr(subprocess_env, "served_profile_child_env", raising=False)
        monkeypatch.setattr(subprocess_env, "hermes_subprocess_env", child_env)
    monkeypatch.setattr(managed_uv, "resolve_uv", lambda: "uv")
    repair = MagicMock(side_effect=AssertionError("must not repair the Hermes runtime"))
    monkeypatch.setattr(managed_uv, "ensure_uv", repair)
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(ql.subprocess, "run", run)
    engine = ql.QuickLocalSetup(health_check=lambda _url: (False, ""))
    engine._install_with_uv(ql.managed_paths(home), ["reviewed-package"])
    if served_profiles:
        child_env.assert_called_once_with(target_home=home)
    else:
        child_env.assert_called_once_with()
    assert run.call_count == 2
    for call in run.call_args_list:
        env = call.kwargs["env"]
        assert env["HERMES_HOME"] == str(home)
        assert env["OPENVIKING_API_KEY"] == "profile-key"
        assert not any(key in env for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"))
    assert source_env["PYTHONPATH"] == "host-pythonpath"


def test_pending_recovery_identity_survives_managed_port_move(modules, monkeypatch):
    home, provider, _module, ql, _life, _packages = modules
    provider._hermes_home = str(home)
    monkeypatch.setattr(
        provider, "_profile_config_and_env", lambda: ({"deployment": ql.DEPLOYMENT}, {})
    )
    provider._client = MagicMock(
        _conn_snapshot=("http://127.0.0.1:1933", "key", "default", "default", "hermes")
    )
    first = provider._capture_commit_scope()
    provider._client = MagicMock(
        _conn_snapshot=("http://127.0.0.1:1934", "key", "default", "default", "hermes")
    )
    moved = provider._capture_commit_scope()
    assert first.connection_key == moved.connection_key
    assert first.marker_id != moved.marker_id
    provider._client = MagicMock(
        _conn_snapshot=("http://127.0.0.1:1934", "key", "default", "other", "hermes")
    )
    assert provider._capture_commit_scope().connection_key != moved.connection_key


@pytest.mark.parametrize("consent", [False, True])
def test_source_build_prompt_defaults_to_cancel(modules, monkeypatch, consent):
    home, _provider, _module, ql, lifecycle, _packages = modules
    setup_module = importlib.import_module(ql.__package__ + "._setup")
    server = lifecycle.LocalServer(home)
    server.configure(ql.build_server_config(server.paths, {"model": "test"}))
    engine = MagicMock()
    error = ql.SourceBuildRequired("A source build needs native development tools.")
    engine.provision.side_effect = [
        error,
        ql.QuickLocalSetupResult(server.paths, "http://127.0.0.1:1933", False),
    ]
    monkeypatch.setattr(ql, "QuickLocalSetup", lambda **_kwargs: engine)
    select = MagicMock(return_value=int(consent))
    config = {"memory": {}}
    provider_config = {}
    result = setup_module._run_quick_local_setup(
        config=config,
        provider_config=provider_config,
        env_path=home / ".env",
        select=select,
        cancelled=object(),
    )
    assert result is consent
    assert select.call_args.kwargs["default"] == 0
    assert engine.provision.call_count == (2 if consent else 1)
    if consent:
        assert engine.allow_source_build is True
        assert provider_config["deployment"] == ql.DEPLOYMENT
    else:
        assert provider_config == {}


def test_port_probe_rejects_active_listener(modules):
    import socket

    _home, _p, _m, ql, _life, _packages = modules
    with socket.socket() as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", 0))
        port = listener.getsockname()[1]
        listener.listen()
        assert not ql._can_bind_local_port("127.0.0.1", port)
    assert ql._can_bind_local_port("127.0.0.1", port)


@pytest.mark.parametrize(
    "system,machine",
    [("Darwin", "arm64"), ("Linux", "x86_64"), ("Linux", "aarch64"), ("Windows", "AMD64")],
)
def test_supported_binary_requirements_keep_native_embedding_hash(
    modules, monkeypatch, system, machine
):
    _home, _p, _m, ql, _life, packages = modules
    monkeypatch.setattr(packages.platform, "system", lambda: system)
    monkeypatch.setattr(packages.platform, "machine", lambda: machine)
    monkeypatch.setattr(packages.platform, "mac_ver", lambda: ("14.0", (), ""))
    monkeypatch.setattr(packages.platform, "libc_ver", lambda: ("glibc", "2.31"))
    requirements = packages.install_requirements()
    assert requirements[0] == ql.OPENVIKING_REQUIREMENT
    assert "#sha256=" in requirements[1]
    assert "metal" in requirements[1] if system == "Darwin" else "metal" not in requirements[1]


@pytest.mark.parametrize("action", ["kill", "stop"])
def test_live_provider_recovers_a_stopped_server_in_the_same_session(servers, modules, action):
    import time

    import psutil

    _home, provider, _module, ql, _life, _packages = modules
    server = servers("live-provider")
    ready(server)
    callbacks = []
    provider._profile_config_and_env = lambda: ({"deployment": ql.DEPLOYMENT}, {})
    provider.initialize(
        "retained-session",
        hermes_home=str(server.paths.root.parent),
        platform="cli",
        status_callback=callbacks.append,
        warning_callback=callbacks.append,
    )
    old_client = provider._ensure_client()
    old_pid = server.status()["pid"]
    assert old_client is not None
    if action == "kill":
        psutil.Process(old_pid).kill()
    else:
        server.stop()
    assert provider._ensure_client() is None
    deadline = time.monotonic() + 20
    while provider._ensure_client() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert provider._client is not None and provider._client is not old_client
    assert server.status()["pid"] != old_pid
    assert any("Starting Quick Local" in message for message in callbacks)


def test_configure_refuses_unverifiable_server_before_changing_live_files(servers, monkeypatch):
    server = servers("unverified")
    ready(server)
    before = {
        path: path.read_bytes() for path in (server.paths.server_config, server.paths.ovcli_config)
    }
    monkeypatch.setattr(server, "_verified_process", lambda _record: None)
    monkeypatch.setattr(server, "_adopt", lambda *_args: None)
    config = server._config()
    config["server"]["root_api_key"] = "rotated-test-key"
    with pytest.raises(server.ql.QuickLocalSetupError, match="cannot verify"):
        server.configure(config)
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not server.paths.restart_required_marker.exists()
    assert server.ql.server_belongs_to_profile(server.paths, server.status()["endpoint"])


def test_configure_adopts_a_lost_record_before_changing_the_key(servers):
    import psutil

    server = servers("lost-record")
    ready(server)
    old_pid = server.status()["pid"]
    old_family = {old_pid, *(p.pid for p in psutil.Process(old_pid).children(recursive=True))}
    server.record_path.unlink()
    config = server._config()
    config["server"]["root_api_key"] = "rotated-test-key"
    server.configure(config)
    # Windows venv redirectors expose a launcher and a child with the same
    # command. Either verified member can own the recovered record.
    assert server._read_record()["pid"] in old_family
    ready(server)
    assert server.status()["pid"] not in old_family
    assert server.ql.server_belongs_to_profile(server.paths, server.status()["endpoint"])


def test_new_managed_ports_leave_the_openviking_default_free(modules, monkeypatch):
    _home, _p, _m, ql, _life, _packages = modules
    monkeypatch.setattr(ql, "_can_bind_local_port", lambda *_args: True)
    assert ql.find_available_port() == 1934
    # Existing Quick Local profiles retain their original port.
    assert ql.find_available_port(preferred_endpoint="http://127.0.0.1:1933") == 1933


def test_retained_provider_recovers_a_port_taken_by_another_profile(servers, modules):
    import time

    _h, provider, _module, ql, _life, _packages = modules
    a, b = servers("retained-a"), servers("retained-b")
    ready(a)
    old_endpoint = a.status()["endpoint"]
    provider._profile_config_and_env = lambda: ({"deployment": ql.DEPLOYMENT}, {})
    provider.initialize("retained-port", hermes_home=str(a.paths.root.parent), platform="cli")
    assert provider._ensure_client() is not None
    a.stop()
    config = b._config()
    config["server"]["port"] = ql._endpoint_port(old_endpoint)
    b.configure(config)
    ready(b)
    foreign_pid = b.status()["pid"]
    assert provider._ensure_client() is None
    deadline = time.monotonic() + 20
    while provider._ensure_client() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert provider._client is not None
    assert provider._endpoint != old_endpoint
    assert provider._endpoint == ql.connection_config({}, a.paths.root.parent)["url"]
    assert b.status()["pid"] == foreign_pid


def test_switching_away_stops_only_the_previous_managed_server(servers, modules, capsys):
    _h, _p, _m, ql, _life, _packages = modules
    setup = importlib.import_module(ql.__package__ + "._setup")
    a, b = servers("switch-a"), servers("switch-b")
    ready(a)
    ready(b)
    b_pid = b.status()["pid"]
    config = {"memory": {}}
    provider_config = {"deployment": ql.DEPLOYMENT}
    a.paths.workspace.mkdir()
    marker = a.paths.workspace / "retained-memory"
    marker.write_text("keep")
    setup._save_hermes_only_config(
        config=config,
        provider_config=provider_config,
        env_path=a.paths.root.parent / ".env",
        values={"endpoint": "https://custom.example", "api_key": "synthetic-key"},
    )
    assert a.status()["state"] == "stopped"
    assert b.status()["pid"] == b_pid
    assert marker.read_text() == "keep"
    assert "data is retained" in capsys.readouterr().out


def test_unconfigured_status_does_not_create_managed_storage(modules, monkeypatch, capsys):
    from argparse import Namespace

    home, _p, _m, ql, _life, _packages = modules
    cli = importlib.import_module(ql.__package__ + ".cli")
    monkeypatch.setattr(cli, "get_hermes_home", lambda: home)
    cli._run(Namespace(local_action="status"))
    assert "not configured" in capsys.readouterr().out
    assert not ql.managed_paths(home).root.exists()


def test_changed_native_pins_reinstall_an_existing_runtime(modules, monkeypatch):
    home, _p, _m, ql, _life, packages = modules
    paths = ql.managed_paths(home)
    paths.root.mkdir()
    (paths.root / "runtime-requirements.json").write_text('["older-pins"]')
    monkeypatch.setattr(ql, "openviking_install_satisfies_requirement", lambda _paths: True)
    monkeypatch.setattr(ql, "_pm_available", lambda: False)
    monkeypatch.setattr(packages, "verified_requirements", lambda values, _cache: values)
    monkeypatch.setattr(
        packages, "resolve_server_requirement", lambda **_kw: "openviking[local-embed]==0.4.23"
    )
    engine = ql.QuickLocalSetup(health_check=lambda _url: (False, ""))
    install = MagicMock()
    monkeypatch.setattr(engine, "_install_with_uv", install)
    assert engine._ensure_openviking_installed(paths)
    requirements = packages.install_requirements()
    requirements[0] = packages.resolve_server_requirement()
    install.assert_called_once_with(paths, requirements)
    assert not engine._ensure_openviking_installed(paths)
    assert install.call_count == 1


def test_legacy_install_without_uv_fails_without_repairing_hermes(modules, monkeypatch):
    import hermes_constants
    from hermes_cli import managed_uv

    home, _p, _m, ql, _life, _packages = modules
    monkeypatch.setattr(hermes_constants, "get_default_hermes_root", lambda: home)
    monkeypatch.setattr(managed_uv, "resolve_uv", lambda: None)
    monkeypatch.setattr(ql.shutil, "which", lambda _name: None)
    repair = MagicMock(side_effect=SystemExit("must not rebuild Hermes"))
    monkeypatch.setattr(managed_uv, "ensure_uv", repair)
    engine = ql.QuickLocalSetup(health_check=lambda _url: (False, ""))
    with pytest.raises(ql.QuickLocalSetupError, match="needs uv"):
        engine._install_with_uv(ql.managed_paths(home), ["isolated-package"])
    repair.assert_not_called()


def test_lost_record_adoption_survives_a_selected_runtime_upgrade(servers, modules, monkeypatch):
    import psutil

    _home, _p, _m, ql, _life, _packages = modules
    server = servers("old-runtime")
    ready(server)
    old_pid = server.status()["pid"]
    old_family = {old_pid, *(p.pid for p in psutil.Process(old_pid).children(recursive=True))}
    server.record_path.unlink()
    old_command = server.paths.server_command
    new_command = server.paths.runtime / "new-generation/bin/openviking-server"
    new_command.parent.mkdir(parents=True)
    new_command.write_bytes(old_command.read_bytes())
    monkeypatch.setattr(ql.QuickLocalPaths, "server_command", property(lambda _self: new_command))
    config = server._config()
    config["server"]["root_api_key"] = "rotated-test-key"
    server.configure(config, runtime_changed=True)
    assert server._read_record()["pid"] in old_family
    assert server._read_record()["command"] == str(old_command)
    ready(server)
    assert server.status()["pid"] not in old_family
    assert server._read_record()["command"] == str(new_command)


def test_stop_from_another_process_accepts_a_terminated_child(servers, modules):
    """A CLI cannot reap a server whose parent is a running Hermes process."""
    import json
    from pathlib import Path

    _home, _p, module, _ql, _life, _packages = modules
    server = servers("external-stop")
    ready(server)
    script = f"""
import importlib.util,sys
from pathlib import Path
sys.path[:0] = {json.dumps(sys.path)}
spec=importlib.util.spec_from_file_location("external_stop_provider", {json.dumps(module.__file__)}, submodule_search_locations=[{json.dumps(str(Path(module.__file__).parent))}])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
from external_stop_provider.local_server import LocalServer
assert LocalServer(Path({json.dumps(str(server.paths.root.parent))})).stop()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
