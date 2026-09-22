"""Opt-in regression on native Linux storage, including when run on Docker Desktop.

Run with VIKINGBOT_TEST_DOCKER=1 and the configured OpenSandbox images available.
A named volume avoids macOS file-sharing permission translation masking the bug.
"""

import os
import socket
import subprocess
import uuid
from datetime import timedelta

import pytest
from vikingbot.config.schema import Config, SessionKey
from vikingbot.sandbox.managed_server import WORKSPACE_GID_LABEL, WORKSPACE_UID_LABEL
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.sandbox.runtime import OpenSandboxRuntime

pytestmark = pytest.mark.skipif(
    os.environ.get("VIKINGBOT_TEST_DOCKER") != "1",
    reason="requires explicit Docker integration run",
)


@pytest.mark.asyncio
async def test_uid_1000_linux_workspace_without_dac_override(tmp_path):
    from opensandbox.config import ConnectionConfig
    from opensandbox.models.sandboxes import PVC, NetworkPolicy, Volume
    from opensandbox.sandbox import Sandbox

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = Config(
        storage_workspace=str(tmp_path),
        sandbox={
            "backend": "opensandbox",
            "backends": {"opensandbox": {"server_url": f"http://127.0.0.1:{port}"}},
        },
    )
    runtime = OpenSandboxRuntime(config)
    manager = SandboxManager(config, config.sandbox_workspace_path, tmp_path / "source")
    volume = "vikingbot-permissions-" + uuid.uuid4().hex
    remote = None

    def docker(*args, check=True):
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=60, check=check
        )

    image = config.sandbox.backends.opensandbox.default_image
    docker("volume", "create", volume)
    try:
        docker(
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            "-v",
            f"{volume}:/workspace",
            image,
            "-c",
            "printf original > /workspace/existing; chown -R 1000:1000 /workspace; "
            "chmod 755 /workspace; chmod 644 /workspace/existing",
        )
        before = docker(
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-v",
            f"{volume}:/workspace",
            image,
            "-c",
            "printf probe > /workspace/root-probe",
            check=False,
        )
        assert before.returncode != 0
        assert "Permission denied" in before.stderr

        # Also exercise the ordinary Bot path with its real host directory owner.
        await runtime.start(manager)
        backend = await manager.get_sandbox(
            SessionKey(type="cli", channel_id="test", chat_id="test")
        )
        await backend.write_file("repeat.txt", "first")
        await backend.write_file("repeat.txt", "second")
        await backend.write_file_bytes("binary.bin", b"\x00\xff")
        workspace = config.opensandbox_workspaces_path / "shared"
        assert (workspace / "repeat.txt").read_text() == "second"
        assert (workspace / "binary.bin").read_bytes() == b"\x00\xff"
        assert (workspace / "repeat.txt").stat().st_mode & 0o777 == 0o644
        assert (workspace / "binary.bin").stat().st_mode & 0o777 == 0o644
        connection = ConnectionConfig(
            domain=runtime.settings.server_url,
            api_key=runtime.settings.api_key,
            use_server_proxy=True,
        )
        for iteration in range(2):
            remote = await Sandbox.create(
                image,
                connection_config=connection,
                timeout=timedelta(seconds=120),
                metadata={WORKSPACE_UID_LABEL: "1000", WORKSPACE_GID_LABEL: "1000"},
                env={"HOME": "/workspace"},
                volumes=[
                    Volume(name="workspace", pvc=PVC(claim_name=volume), mount_path="/workspace")
                ],
                network_policy=NetworkPolicy(default_action="deny"),
            )
            execution = await remote.commands.run("id -u; id -g; grep CapEff /proc/self/status")
            assert execution.error is None
            output = "\n".join(chunk.text for chunk in execution.logs.stdout)
            assert output.splitlines()[:2] == ["1000", "1000"]
            assert "0000000000000000" in output
            assert await remote.files.read_file("/workspace/existing") == (
                "original" if iteration == 0 else "updated"
            )
            await remote.files.write_file("/workspace/existing", "updated", mode=644)
            await remote.files.write_file("/workspace/api-probe", "api", mode=644)
            execution = await remote.commands.run(
                "printf shell > /workspace/shell-probe; "
                "stat -c '%u:%g %a' /workspace/existing /workspace/api-probe /workspace/shell-probe"
            )
            assert execution.error is None
            output = "\n".join(chunk.text for chunk in execution.logs.stdout)
            assert output.splitlines() == ["1000:1000 644"] * 3
            await remote.kill()
            await remote.close()
            remote = None
    finally:
        try:
            if remote is not None:
                try:
                    await remote.kill()
                finally:
                    await remote.close()
            await manager.cleanup_all()
        finally:
            await runtime.stop()
            docker("volume", "rm", volume)
