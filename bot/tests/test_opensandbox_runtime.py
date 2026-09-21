"""Managed startup must prove the sandbox works before serving requests."""

import asyncio
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from vikingbot.config.schema import Config, SessionKey
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.sandbox.runtime import OpenSandboxRuntime

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def configured(tmp_path, **settings):
    return Config(
        storage_workspace=str(tmp_path),
        sandbox={"backend": "opensandbox", "backends": {"opensandbox": settings}},
    )


@pytest.mark.asyncio
async def test_direct_never_checks_dependencies(tmp_path, monkeypatch):
    runtime = OpenSandboxRuntime(Config(storage_workspace=str(tmp_path)))
    check = Mock(side_effect=AssertionError("must not inspect dependencies"))
    monkeypatch.setattr("vikingbot.sandbox.runtime.importlib.util.find_spec", check)
    await runtime.start(None)
    check.assert_not_called()


@pytest.mark.asyncio
async def test_missing_docker_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.sandbox.runtime.shutil.which", lambda _: None)
    runtime = OpenSandboxRuntime(configured(tmp_path))
    with pytest.raises(RuntimeError, match="Docker is not installed.*https://"):
        await runtime._docker_environment()


@pytest.mark.asyncio
async def test_unreachable_docker_is_not_missing_docker(tmp_path):
    runtime = OpenSandboxRuntime(configured(tmp_path))
    runtime._command = AsyncMock(side_effect=RuntimeError("permission denied"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("vikingbot.sandbox.runtime.shutil.which", lambda _: "/bin/docker")
        with pytest.raises(RuntimeError, match="Docker is not reachable.*permissions"):
            await runtime._docker_environment()


@pytest.mark.asyncio
async def test_docker_desktop_context_is_forwarded_to_server(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr("vikingbot.sandbox.runtime.shutil.which", lambda _: "/bin/docker")
    runtime = OpenSandboxRuntime(configured(tmp_path))
    runtime._command = AsyncMock(side_effect=["linux", "unix:///desktop/docker.sock"])
    env = await runtime._docker_environment()
    assert env["DOCKER_HOST"] == "unix:///desktop/docker.sock"


@pytest.mark.asyncio
async def test_native_windows_containers_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.sandbox.runtime.shutil.which", lambda _: "/bin/docker")
    runtime = OpenSandboxRuntime(configured(tmp_path))
    runtime._command = AsyncMock(return_value="windows")
    with pytest.raises(RuntimeError, match="Linux containers"):
        await runtime._docker_environment()


def test_generated_config_is_private_and_uses_actual_settings(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = configured(tmp_path, server_url=f"http://localhost:{port}", pids_limit=64)
    runtime = OpenSandboxRuntime(config)
    path = runtime._write_config()
    parsed = tomllib.loads(path.read_text())
    assert parsed["server"]["port"] == port
    assert parsed["server"]["api_key"] == runtime.settings.api_key
    assert len(runtime.settings.api_key) >= 32
    assert parsed["runtime"]["type"] == "docker"
    assert parsed["docker"]["network_mode"] == "bridge"
    assert parsed["docker"]["drop_capabilities"] == ["ALL"]
    assert parsed["docker"]["pids_limit"] == 64
    assert parsed["storage"]["allowed_host_paths"] == [
        str(config.opensandbox_workspaces_path.resolve())
    ]
    assert not path.resolve().is_relative_to(config.opensandbox_workspaces_path.resolve())
    assert path.is_relative_to(config.bot_data_path)
    assert not path.is_relative_to(config.workspace_path)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_occupied_port_is_not_reused_or_killed(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        runtime = OpenSandboxRuntime(
            configured(
                tmp_path,
                server_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
            )
        )
        with pytest.raises(RuntimeError, match="occupied"):
            runtime._write_config()
        assert runtime.process is None


def test_managed_server_restricts_workload_and_sidecar_published_ports(monkeypatch):
    import docker
    from vikingbot.sandbox.managed_server import restrict_published_ports

    # Use the real Docker SDK's serialization without connecting to a daemon.
    monkeypatch.setattr(docker.APIClient, "create_host_config", docker.APIClient.create_host_config)
    restrict_published_ports(docker.APIClient)
    client = docker.APIClient(base_url="unix:///unused.sock", version="1.41")
    try:
        config = client.create_host_config(
            network_mode="bridge",
            port_bindings={"44772": ("0.0.0.0", 50123), "8080": ("::", 50124)},
            cap_add=["NET_ADMIN"],
        )
        assert config["PortBindings"] == {
            "44772/tcp": [{"HostIp": "127.0.0.1", "HostPort": "50123"}],
            "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "50124"}],
        }
        assert config["CapAdd"] == ["NET_ADMIN"]
    finally:
        client.close()


def test_managed_server_sets_workspace_owner_only_for_workloads():
    from vikingbot.sandbox.managed_server import (
        WORKSPACE_GID_LABEL,
        WORKSPACE_UID_LABEL,
        use_workspace_owner,
    )

    class Client:
        def create_container(self, **kwargs):
            return kwargs

    use_workspace_owner(Client)
    client = Client()
    workload = client.create_container(
        labels={
            "opensandbox.io/id": "test",
            WORKSPACE_UID_LABEL: "1000",
            WORKSPACE_GID_LABEL: "1001",
        },
        user="root",
    )
    assert workload["user"] == "1000:1001"
    assert "user" not in client.create_container(labels={"opensandbox.io/egress": "test"})
    assert "user" not in client.create_container(name="sandbox-execd-cache")
    for value in ("", "root", "1000:wheel", "-1:1000"):
        with pytest.raises(ValueError, match="UID:GID"):
            client.create_container(
                labels={
                    "opensandbox.io/id": "test",
                    WORKSPACE_UID_LABEL: value,
                    WORKSPACE_GID_LABEL: "1000",
                }
            )


@pytest.mark.asyncio
async def test_binary_files_use_remote_api_and_sdk_errors_are_reported(tmp_path):
    from opensandbox.models.execd import Execution, ExecutionError

    config = configured(tmp_path)
    backend = OpenSandboxBackend(config.sandbox, "shared", tmp_path / "work")
    backend._sandbox = SimpleNamespace(
        files=SimpleNamespace(write_file=AsyncMock()),
        commands=SimpleNamespace(
            run=AsyncMock(
                return_value=Execution(
                    error=ExecutionError(
                        name="CommandExecError", value="exit status 2", timestamp=0
                    )
                )
            )
        ),
    )
    await backend.write_file_bytes("nested/image.png", b"\x00\xff")
    backend._sandbox.files.write_file.assert_awaited_once_with(
        "/workspace/nested/image.png", b"\x00\xff", mode=644
    )
    assert not (tmp_path / "work" / "nested" / "image.png").exists()
    output = await backend.execute("false")
    assert "exit status 2" in output
    assert "Exit code: 1" in output


def probe_manager():
    files = {}

    async def write(path, content):
        files[path] = content

    async def execute(command):
        if command.startswith("cat "):
            return files[command.removeprefix("cat /workspace/")]
        return ""

    sandbox = SimpleNamespace(
        write_file=AsyncMock(side_effect=write),
        read_file=AsyncMock(side_effect=lambda path: files[path]),
        execute=AsyncMock(side_effect=execute),
    )
    return SimpleNamespace(
        get_sandbox=AsyncMock(return_value=sandbox),
        cleanup_session=AsyncMock(),
        cleanup_all=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shared", "per-session", "per-channel"])
async def test_external_server_probes_without_local_docker(tmp_path, monkeypatch, mode):
    config = configured(tmp_path, managed=False)
    config.sandbox.mode = mode
    runtime = OpenSandboxRuntime(config)
    runtime._wait_for_server = AsyncMock()
    runtime._docker_environment = AsyncMock(side_effect=AssertionError("external server"))
    manager = probe_manager()
    await runtime.start(manager)
    runtime._docker_environment.assert_not_called()
    assert manager.cleanup_session.await_count == (0 if mode == "shared" else 1)
    await runtime.stop()


@pytest.mark.asyncio
async def test_failed_probe_cleans_up_and_does_not_fallback(tmp_path):
    config = configured(tmp_path, managed=False)
    runtime = OpenSandboxRuntime(config)
    runtime._wait_for_server = AsyncMock()
    manager = probe_manager()
    manager.get_sandbox.return_value.execute.side_effect = None
    manager.get_sandbox.return_value.execute.return_value = "Exit code: 1"
    runtime.stop = AsyncMock()
    with pytest.raises(RuntimeError, match="command probe failed"):
        await runtime.start(manager)
    manager.cleanup_all.assert_awaited_once()
    runtime.stop.assert_awaited_once()
    assert config.sandbox.backend == "opensandbox"


@pytest.mark.asyncio
async def test_cancelled_startup_stops_owned_server(tmp_path):
    runtime = OpenSandboxRuntime(configured(tmp_path, managed=False))
    runtime._wait_for_server = AsyncMock(side_effect=asyncio.CancelledError)
    runtime.stop = AsyncMock()
    manager = probe_manager()
    with pytest.raises(asyncio.CancelledError):
        await runtime.start(manager)
    manager.cleanup_all.assert_awaited_once()
    runtime.stop.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [True, False])
async def test_backend_creation_mounts_only_managed_workspace(tmp_path, monkeypatch, managed):
    from opensandbox.sandbox import Sandbox

    remote = SimpleNamespace(commands=SimpleNamespace(run=AsyncMock()))
    create = AsyncMock(return_value=remote)
    monkeypatch.setattr(Sandbox, "create", create)
    config = configured(tmp_path, managed=managed)
    settings = config.sandbox.backends.opensandbox
    settings.network.allowed_domains = ["pypi.org"]
    settings.network.denied_domains = ["internal.example"]
    workspace = config.sandbox_workspace_path / "shared"
    backend = OpenSandboxBackend(
        config.sandbox, "shared", workspace, host_workspace=workspace if managed else None
    )
    await backend.start()
    kwargs = create.call_args.kwargs
    if managed:
        from vikingbot.sandbox.managed_server import WORKSPACE_GID_LABEL, WORKSPACE_UID_LABEL

        assert len(kwargs["volumes"]) == 1
        volume = kwargs["volumes"][0]
        assert volume.host.path == str(workspace.resolve())
        assert volume.mount_path == "/workspace"
        assert volume.read_only is False
        owner = workspace.stat()
        assert kwargs["metadata"][WORKSPACE_UID_LABEL] == str(owner.st_uid)
        assert kwargs["metadata"][WORKSPACE_GID_LABEL] == str(owner.st_gid)
        assert kwargs["env"]["HOME"] == "/workspace"
    else:
        assert "volumes" not in kwargs
        assert "metadata" not in kwargs
    assert kwargs["resource"] == {"cpu": "500m", "memory": "1Gi"}
    assert kwargs["network_policy"].default_action == "deny"
    assert [rule.action for rule in kwargs["network_policy"].egress] == ["deny", "allow"]
    assert kwargs["connection_config"].use_server_proxy is True
    assert backend.local_file_path("secret") is None


@pytest.mark.asyncio
async def test_concurrent_first_use_creates_one_sandbox(tmp_path):
    config = Config(storage_workspace=str(tmp_path))
    manager = SandboxManager(config, tmp_path / "work", tmp_path / "source")
    sandbox = object()

    async def create(*_):
        await asyncio.sleep(0)
        return sandbox

    manager._create_sandbox = AsyncMock(side_effect=create)
    key = SessionKey(type="cli", channel_id="test", chat_id="test")
    results = await asyncio.gather(*(manager.get_sandbox(key) for _ in range(8)))
    assert results == [sandbox] * 8
    manager._create_sandbox.assert_awaited_once()


@pytest.mark.asyncio
async def test_managed_workspace_survives_container_recreation_without_overwrite(tmp_path):
    config = configured(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "SOUL.md").write_text("initial")
    manager = SandboxManager(config, config.sandbox_workspace_path, source)

    class MountedBackend:
        def __init__(self, config, workspace_id, workspace, *, host_workspace):
            assert workspace == host_workspace
            self.workspace = workspace

        async def start(self):
            self.workspace.mkdir(parents=True, exist_ok=True)

        async def stop(self):
            pass

    manager._backend_cls = MountedBackend
    key = SessionKey(type="cli", channel_id="test", chat_id="test")
    first = await manager.get_sandbox(key)
    assert first.workspace == config.opensandbox_workspaces_path / "shared"
    assert (first.workspace / "SOUL.md").read_text() == "initial"
    (first.workspace / "SOUL.md").write_text("customized")
    (first.workspace / "result.txt").write_text("keep me")
    await manager.cleanup_all()
    second = await manager.get_sandbox(key)
    assert second is not first
    assert (second.workspace / "SOUL.md").read_text() == "customized"
    assert (second.workspace / "result.txt").read_text() == "keep me"


@pytest.mark.asyncio
@pytest.mark.parametrize("escape", ["parent", "symlink"])
async def test_managed_workspace_cannot_mount_server_credentials(tmp_path, escape):
    config = configured(tmp_path)
    root = config.opensandbox_workspaces_path
    root.mkdir(parents=True)
    credentials = root.parent / "gateway-private"
    credentials.mkdir()
    if escape == "symlink":
        workspace = root / "shared"
        workspace.symlink_to(credentials, target_is_directory=True)
    else:
        workspace = credentials
    manager = SandboxManager(config, root, tmp_path / "source")
    with pytest.raises(ValueError, match="host workspace"):
        await manager._create_sandbox("shared", workspace)


@pytest.mark.asyncio
async def test_managed_service_uses_generated_config_and_is_stopped(tmp_path, monkeypatch):
    config = configured(tmp_path)
    runtime = OpenSandboxRuntime(config)
    runtime._docker_environment = AsyncMock(return_value={"DOCKER_HOST": "unix:///docker.sock"})
    runtime._command = AsyncMock(return_value="image exists")
    runtime._wait_for_server = AsyncMock()
    process = SimpleNamespace(returncode=None, terminate=Mock(), kill=Mock(), wait=AsyncMock())
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(
        "vikingbot.sandbox.runtime.shutil.which", lambda _: "/bin/opensandbox-server"
    )
    monkeypatch.setattr("vikingbot.sandbox.runtime.asyncio.create_subprocess_exec", spawn)
    await runtime.start(probe_manager())
    argv = spawn.call_args.args
    assert argv[:4] == (sys.executable, "-m", "vikingbot.sandbox.managed_server", "--config")
    assert spawn.call_args.kwargs["start_new_session"] == (os.name != "nt")
    if os.name == "nt":
        import subprocess

        assert spawn.call_args.kwargs["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    path = Path(argv[4])
    assert path.exists()
    assert config.sandbox.backends.opensandbox.api_key
    await runtime.stop()
    process.terminate.assert_called_once()
    process.wait.assert_awaited_once()
    assert not path.exists()
    assert (runtime.runtime_dir / "server.log").exists()


@pytest.mark.asyncio
async def test_health_probe_requires_authenticated_lifecycle_api(tmp_path, monkeypatch):
    import httpx

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200 if request.url.path == "/health" else 401)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("vikingbot.sandbox.runtime.httpx.AsyncClient", lambda **_: client)
    runtime = OpenSandboxRuntime(configured(tmp_path, managed=False, api_key="test-key"))
    with pytest.raises(RuntimeError, match="authentication failed"):
        await runtime._wait_for_server()
    assert requests[-1].headers["OPEN-SANDBOX-API-KEY"] == "test-key"


@pytest.mark.parametrize("fail", [False, True])
def test_gateway_preflights_before_serving_and_always_cleans_up(tmp_path, monkeypatch, fail):
    import typer
    from vikingbot.cli import commands

    config = configured(tmp_path)
    events = []
    manager = SimpleNamespace(cleanup_all=AsyncMock())
    agent = SimpleNamespace(sandbox_manager=manager, run=AsyncMock(), close_mcp=AsyncMock())
    server = SimpleNamespace(started=True)

    async def start(_):
        events.append("sandbox")
        if fail:
            raise RuntimeError("Docker unavailable")

    async def serve():
        events.append("http")

    runtime = SimpleNamespace(start=AsyncMock(side_effect=start), stop=AsyncMock())
    server.serve = AsyncMock(side_effect=serve)
    monkeypatch.setattr(commands, "ensure_config", lambda _: config)
    monkeypatch.setattr(commands, "validate_openviking_auth", lambda _: None)
    monkeypatch.setattr(commands, "_abort_if_port_in_use", lambda *_: None)
    monkeypatch.setattr(commands, "_init_bot_data", lambda _: None)
    monkeypatch.setattr(commands, "prepare_cron", lambda *_: None)
    monkeypatch.setattr(commands, "prepare_agent_loop", lambda *_: agent)
    monkeypatch.setattr(
        commands, "prepare_channel", lambda *_, **__: SimpleNamespace(start_all=AsyncMock())
    )
    monkeypatch.setattr(
        commands, "prepare_heartbeat", lambda *_: SimpleNamespace(start=AsyncMock(), stop=Mock())
    )
    monkeypatch.setattr("uvicorn.Server", lambda _: server)
    monkeypatch.setattr("vikingbot.sandbox.runtime.OpenSandboxRuntime", lambda _: runtime)
    monkeypatch.setattr(
        "vikingbot.compile.service.BotCompileService",
        lambda **_: SimpleNamespace(start=AsyncMock(), close=AsyncMock()),
    )
    if fail:
        with pytest.raises(typer.Exit):
            commands.gateway(port=None, host="127.0.0.1", verbose=False, config_path=None)
        assert events == ["sandbox"]
    else:
        commands.gateway(port=None, host="127.0.0.1", verbose=False, config_path=None)
        assert events == ["sandbox", "http"]
    runtime.stop.assert_awaited_once()
    manager.cleanup_all.assert_awaited_once()
