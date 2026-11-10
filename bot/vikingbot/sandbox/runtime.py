"""Gateway-owned OpenSandbox lifecycle. Never starts a container runtime itself."""

import asyncio
import importlib.util
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from loguru import logger

from vikingbot.config.schema import Config, SessionKey


def docker_install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "Install and start Docker Desktop for Mac: https://docs.docker.com/desktop/setup/install/mac-install/"
    if system == "Windows" or "microsoft" in platform.release().lower():
        return "Install Docker Desktop, enable WSL2/Linux containers and WSL integration: https://docs.docker.com/desktop/setup/install/windows-install/"
    return "Install and start Docker Engine: https://docs.docker.com/engine/install/"


class OpenSandboxRuntime:
    """One server per Gateway; session and Compile backends only create sandboxes."""

    def __init__(self, config: Config):
        self.config = config
        self.settings = config.sandbox.backends.opensandbox
        self.process = None
        self.log_file = None
        self.runtime_dir: Path | None = None

    async def _command(self, *argv: str, env=None, timeout=30, output_file=None) -> str:
        process = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=output_file or asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        output = (stdout or b"").decode(errors="replace").strip()
        if process.returncode:
            raise RuntimeError(output[-2000:] or f"Command failed: {argv[0]} (see OpenSandbox log)")
        return output

    async def _docker_environment(self) -> dict[str, str]:
        if not shutil.which("docker"):
            raise RuntimeError(f"Docker is not installed. {docker_install_hint()}")
        env = os.environ.copy()
        try:
            kind = await self._command("docker", "info", "--format", "{{.OSType}}", env=env)
        except (RuntimeError, asyncio.TimeoutError) as exc:
            raise RuntimeError(
                "Docker is not reachable. Start Docker and check socket permissions / Docker context "
                f"with `docker info`. {docker_install_hint()}\n{exc}"
            ) from exc
        if kind != "linux":
            raise RuntimeError(
                "OpenSandbox requires Linux containers; switch Docker to Linux containers."
            )
        # docker-py does not read the CLI's selected context (notably Docker Desktop).
        if env.get("DOCKER_CONTEXT") or not env.get("DOCKER_HOST"):
            endpoint = await self._command(
                "docker",
                "context",
                "inspect",
                "--format",
                "{{.Endpoints.docker.Host}}",
                env=env,
            )
            env["DOCKER_HOST"] = endpoint
        if not env["DOCKER_HOST"].startswith(("unix://", "npipe://")):
            raise RuntimeError(
                "Managed OpenSandbox requires a local Docker socket. For remote Docker, run "
                "OpenSandbox on that host and set managed=false with its server_url."
            )
        return env

    def _write_config(self) -> Path:
        url = urlsplit(self.settings.server_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"localhost", "127.0.0.1"}
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or url.username
        ):
            raise ValueError(
                "Managed OpenSandbox server_url must be http://127.0.0.1:<port> or localhost."
            )
        port = url.port or 80
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(
                    f"OpenSandbox port {port} is occupied; choose another server_url port."
                ) from exc
        parent = self.config.bot_data_path / "runtime" / "opensandbox"
        parent.mkdir(parents=True, exist_ok=True)
        mount_root = self.config.opensandbox_workspaces_path
        if mount_root.is_symlink():
            raise ValueError("OpenSandbox workspaces directory must not be a symlink")
        mount_root.mkdir(parents=True, exist_ok=True)
        self.runtime_dir = Path(tempfile.mkdtemp(prefix="gateway-", dir=parent))
        self.settings.api_key = self.settings.api_key or secrets.token_urlsafe(32)
        self.settings.server_url = f"http://127.0.0.1:{port}"

        # JSON string encoding is also valid TOML basic-string encoding.
        def q(value):
            return json.dumps(str(value), ensure_ascii=True)

        content = (
            f'[server]\nhost = "127.0.0.1"\nport = {port}\napi_key = {q(self.settings.api_key)}\n'
            f'[runtime]\ntype = "docker"\nexecd_image = {q(self.settings.execd_image)}\n'
            '[docker]\nnetwork_mode = "bridge"\nhost_ip = "127.0.0.1"\n'
            'no_new_privileges = true\ndrop_capabilities = ["ALL"]\n'
            f"pids_limit = {self.settings.pids_limit}\n"
            f"[egress]\nimage = {q(self.settings.egress_image)}\n"
            # Newer servers can explicitly use host-mapped ports when proxying.
            "[proxy]\nresolve_internal = false\n"
            # Only dedicated workspaces are mountable; Server credentials are siblings.
            f"[storage]\nallowed_host_paths = [{q(mount_root.resolve())}]\n"
        )
        path = self.runtime_dir / "sandbox.toml"
        with open(
            path, "x", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)
        ) as f:
            f.write(content)
        return path

    async def _wait_for_server(self) -> None:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            while True:
                if self.process is not None and self.process.returncode is not None:
                    raise RuntimeError(
                        f"OpenSandbox Server exited; inspect {self.runtime_dir / 'server.log'}"
                    )
                try:
                    response = await client.get(self.settings.server_url.rstrip("/") + "/health")
                    if response.status_code == 200:
                        # Do not accept an unauthenticated health response as proof the key works.
                        response = await client.get(
                            self.settings.server_url.rstrip("/") + "/v1/sandboxes",
                            headers={"OPEN-SANDBOX-API-KEY": self.settings.api_key},
                        )
                        if response.status_code in {401, 403}:
                            raise RuntimeError("OpenSandbox authentication failed; check api_key.")
                        if response.status_code == 200:
                            return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)

    async def start(self, manager) -> None:
        if self.config.sandbox.backend != "opensandbox":
            return
        logger.info("[SANDBOX] Checking OpenSandbox dependencies")
        if importlib.util.find_spec("opensandbox") is None:
            raise RuntimeError(
                'Missing OpenSandbox SDK. Install Bot dependencies: pip install "openviking[bot]"'
            )
        try:
            await asyncio.wait_for(self._initialize(manager), self.settings.startup_timeout)
        except BaseException as exc:
            try:
                await manager.cleanup_all()
            finally:
                await self.stop()
            if isinstance(exc, asyncio.TimeoutError):
                raise RuntimeError(
                    f"OpenSandbox startup exceeded {self.settings.startup_timeout}s. "
                    f"Check Docker, image pulls and server logs under {self.runtime_dir}; "
                    "increase bot.sandbox.backends.opensandbox.startup_timeout if needed."
                ) from exc
            raise

    async def _initialize(self, manager) -> None:
        if self.settings.managed:
            try:
                distribution("opensandbox-server")
            except PackageNotFoundError as exc:
                raise RuntimeError(
                    'Missing opensandbox-server. Install: pip install "openviking[bot]"'
                ) from exc
            env = await self._docker_environment()
            path = self._write_config()
            self.log_file = (self.runtime_dir / "server.log").open("ab", buffering=0)
            for image in (
                self.settings.default_image,
                self.settings.execd_image,
                self.settings.egress_image,
            ):
                logger.info(
                    "[SANDBOX] Preparing image {} (first pull can take several minutes)",
                    image,
                )
                try:
                    await self._command("docker", "image", "inspect", image, env=env)
                except RuntimeError:
                    await self._command(
                        "docker",
                        "pull",
                        image,
                        env=env,
                        timeout=self.settings.startup_timeout,
                        output_file=self.log_file,
                    )
            logger.info("[SANDBOX] Starting managed server at {}", self.settings.server_url)
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "vikingbot.sandbox.managed_server",
                "--config",
                str(path),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=self.log_file,
                stderr=asyncio.subprocess.STDOUT,
                # Ctrl+C targets the foreground process group. Keep this service
                # alive until Gateway has deleted its sandboxes through the API.
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        await self._wait_for_server()
        logger.info("[SANDBOX] Creating sandbox and verifying command/file operations")
        key = SessionKey(type="startup", channel_id="probe", chat_id=secrets.token_hex(8))
        sandbox = await manager.get_sandbox(key)
        marker = secrets.token_hex(16)
        filename = f".bot-startup-{marker}"
        try:
            await sandbox.write_file(filename, marker)
            if (await sandbox.read_file(filename)).strip() != marker:
                raise RuntimeError("OpenSandbox file read/write probe failed")
            if (await sandbox.execute(f"cat /workspace/{filename}")).strip() != marker:
                raise RuntimeError("OpenSandbox command probe failed")
            await sandbox.execute(f"rm /workspace/{filename}")
        finally:
            if self.config.sandbox.mode != "shared":
                await manager.cleanup_session(key)
        logger.info("[SANDBOX] OpenSandbox ready")

    async def stop(self) -> None:
        if self.process is not None:
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 10)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process = None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
        # Preserve diagnostics, remove the generated credential-bearing config.
        if self.runtime_dir is not None:
            (self.runtime_dir / "sandbox.toml").unlink(missing_ok=True)
