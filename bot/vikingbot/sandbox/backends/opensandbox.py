"""OpenSandbox backend implementation using official SDK."""

import os
import subprocess
import asyncio
import time
import atexit
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from vikingbot.sandbox.base import SandboxBackend, SandboxNotStartedError
from vikingbot.sandbox.backends import register_backend


from vikingbot.config.schema import SandboxConfig, SessionKey

# Global to track the opensandbox-server process
_OSB_SERVER_PROCESS: "subprocess.Popen | None" = None


def _is_kubernetes_env() -> bool:
    if "KUBERNETES_SERVICE_HOST" in os.environ:
        return True
    if Path("/var/run/secrets/kubernetes.io/serviceaccount").exists():
        return True
    return False


async def _wait_for_server(url: str, timeout: int = 60) -> bool:
    logger.info("Waiting for OpenSandbox server at {}...", url)
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                health_url = f"{url}/health"
                logger.debug("Checking health at: {}", health_url)
                response = await client.get(health_url)
                logger.debug("Health check response: {} - {}", response.status_code, response.text)
                if response.status_code == 200:
                    logger.info("OpenSandbox server is ready!")
                    return True
        except Exception as e:
            logger.debug("Health check failed: {}", e)
        await asyncio.sleep(1)
    logger.warning("OpenSandbox server not ready after {}s", timeout)
    return False


def _start_opensandbox_server() -> "subprocess.Popen | None":
    global _OSB_SERVER_PROCESS

    if _OSB_SERVER_PROCESS is not None:
        if _OSB_SERVER_PROCESS.poll() is None:
            logger.info("OpenSandbox server already running")
            return _OSB_SERVER_PROCESS
        else:
            logger.warning("OpenSandbox server process died, restarting")
            _OSB_SERVER_PROCESS = None

    try:
        config_path = Path.home() / ".sandbox.toml"
        if not config_path.exists():
            logger.info("Initializing OpenSandbox config at {}", config_path)
            try:
                result = subprocess.run(
                    ["opensandbox-server", "init-config", str(config_path), "--example", "docker"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    logger.warning("Failed to init config with --example, trying without...")
                    result = subprocess.run(
                        ["opensandbox-server", "init-config", str(config_path)],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        logger.warning("Failed to init config, stderr: {}", result.stderr)
            except Exception as e:
                logger.warning("Failed to run init-config: {}", e)

        logger.info("Starting OpenSandbox server...")
        _OSB_SERVER_PROCESS = subprocess.Popen(
            ["opensandbox-server"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        return _OSB_SERVER_PROCESS
    except FileNotFoundError:
        logger.error("opensandbox-server command not found.")
        logger.error("Please start it manually first: opensandbox-server")
        return None
    except Exception as e:
        logger.error("Failed to start OpenSandbox server: {}", e)
        return None


def cleanup_opensandbox_server():
    global _OSB_SERVER_PROCESS
    if _OSB_SERVER_PROCESS is not None and _OSB_SERVER_PROCESS.poll() is None:
        logger.info("Stopping OpenSandbox server...")
        _OSB_SERVER_PROCESS.terminate()
        try:
            _OSB_SERVER_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _OSB_SERVER_PROCESS.kill()
        _OSB_SERVER_PROCESS = None


# Register cleanup on exit
atexit.register(cleanup_opensandbox_server)


@register_backend("opensandbox")
class OpenSandboxBackend(SandboxBackend):
    def __init__(self, config: "SandboxConfig", session_key: SessionKey, workspace: Path):
        # OpenSandbox has built-in isolation, restrict_to_workspace is not needed
        super().__init__()
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._sandbox = None
        self._connection_config = None

        self._osb_config = config.backends.opensandbox

        self._is_vke = _is_kubernetes_env()
        if self._is_vke:
            self._server_url = "http://opensandbox-server:8080"
            logger.info(
                "Detected VKE environment, using OpenSandbox server at: {}", self._server_url
            )
        else:
            self._server_url = self._osb_config.server_url
            logger.info(
                "Detected local environment, using OpenSandbox server at: {}", self._server_url
            )

    async def start(self) -> None:
        self._workspace.mkdir(parents=True, exist_ok=True)

        if not self._is_vke:
            server_process = _start_opensandbox_server()
            if server_process:
                ready = await _wait_for_server(self._server_url, timeout=10)
                if not ready:
                    logger.info(
                        "OpenSandbox server not ready. Please start it manually: opensandbox-server"
                    )

        try:
            from opensandbox.sandbox import Sandbox
            from opensandbox.config import ConnectionConfig

            self._connection_config = ConnectionConfig(
                domain=self._server_url,
                api_key=self._osb_config.api_key,
                request_timeout=timedelta(seconds=300),
            )

            timeout_seconds = self._osb_config.runtime.timeout

            # Configure volumes
            volumes = None
            if not self._is_vke:
                # Local environment: mount host volume
                from opensandbox.models.sandboxes import Volume, Host

                volumes = [
                    Volume(
                        name="workspace",
                        host=Host(path=str(self._workspace.resolve())),
                        mountPath="/workspace",
                    )
                ]
            else:
                # VKE environment: always mount TOS PVC to /workspace
                try:
                    from opensandbox.models.sandboxes import Volume

                    # Build Volume with PVC using dictionary approach for compatibility
                    volume_dict = {
                        "name": "tos-workspace",
                        "persistentVolumeClaim": {"claimName": "vikingbot-data"},
                        "mountPath": "/workspace",
                    }

                    # Try to create Volume object
                    volumes = [Volume(**volume_dict)]
                    logger.info("Configured TOS PVC mount: vikingbot-data -> /workspace")
                except Exception as e:
                    logger.warning("Failed to create Volume object with PVC, falling back: {}", e)
                    volumes = None

            self._sandbox = await Sandbox.create(
                self._osb_config.default_image,
                connection_config=self._connection_config,
                timeout=timedelta(seconds=timeout_seconds),
                volumes=volumes,
            )

            logger.info("OpenSandbox created successfully")

        except ImportError:
            logger.error("opensandbox SDK not installed. Install with: pip install opensandbox")
            raise
        except Exception as e:
            logger.error("Failed to create OpenSandbox: {}", e)
            import traceback

            logger.error("Full traceback:\n{}", traceback.format_exc())
            raise

    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        if not self._sandbox:
            raise SandboxNotStartedError()

        logger.info("[OpenSandbox] Executing: {}", repr(command))

        if command.strip() == "pwd":
            return "/workspace" if self._is_vke else "/"

        try:
            from opensandbox.models.execd import RunCommandOpts

            opts = RunCommandOpts(timeout=timedelta(seconds=timeout))
            execution = await self._sandbox.commands.run(command, opts=opts)

            output_parts = []

            stdout_text = ""
            if execution.logs and execution.logs.stdout:
                stdout_text = "\n".join(
                    [chunk.text for chunk in execution.logs.stdout if chunk.text]
                )

            stderr_text = ""
            if execution.logs and execution.logs.stderr:
                stderr_text = "\n".join(
                    [chunk.text for chunk in execution.logs.stderr if chunk.text]
                )

            exit_code = execution.exit_code if hasattr(execution, "exit_code") else 0

            if stdout_text:
                output_parts.append(stdout_text)
            if stderr_text:
                output_parts.append(f"STDERR:\n{stderr_text}")
            if exit_code != 0:
                output_parts.append(f"\nExit code: {exit_code}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

            logger.info("[OpenSandbox] Output:\n{}", result)
            return result

        except Exception as e:
            logger.error("[OpenSandbox] Error: {}", e)
            import traceback

            logger.error("[OpenSandbox] Traceback:\n{}", traceback.format_exc())
            raise

    async def stop(self) -> None:
        if self._sandbox:
            try:
                if hasattr(self._sandbox, "kill"):
                    await self._sandbox.kill()
                if hasattr(self._sandbox, "close"):
                    await self._sandbox.close()
                logger.info("OpenSandbox stopped")
            except Exception as e:
                logger.warning("Error stopping sandbox: {}", e)

        self._sandbox = None
        self._connection_config = None

    def is_running(self) -> bool:
        return self._sandbox is not None

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def sandbox_cwd(self) -> str:
        return "/workspace"

    async def read_file(self, path: str) -> str:
        """Read file from OpenSandbox."""
        if not self._sandbox:
            raise SandboxNotStartedError()

        # In VKE environment, use SDK API; in local, use base implementation (host mount)
        if self._is_vke:
            try:
                sandbox_path = path
                if not path.startswith("/"):
                    sandbox_path = f"/workspace/{path}"
                return await self._sandbox.files.read_file(sandbox_path)
            except Exception as e:
                logger.error(f"[OpenSandbox] Failed to read file {path}: {e}")
                raise
        else:
            return await super().read_file(path)

    async def write_file(self, path: str, content: str) -> None:
        """Write file to OpenSandbox."""
        if not self._sandbox:
            raise SandboxNotStartedError()

        # In VKE environment, use SDK API; in local, use base implementation (host mount)
        if self._is_vke:
            try:
                sandbox_path = path
                if not path.startswith("/"):
                    sandbox_path = f"/workspace/{path}"
                await self._sandbox.files.write_file(sandbox_path, content, mode=0o644)
            except Exception as e:
                logger.error(f"[OpenSandbox] Failed to write file {path}: {e}")
                raise
        else:
            await super().write_file(path, content)

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        """List directory in OpenSandbox."""
        if not self._sandbox:
            raise SandboxNotStartedError()

        # In VKE environment, use SDK API; in local, use base implementation (host mount)
        if self._is_vke:
            try:
                sandbox_path = path
                if not path.startswith("/"):
                    sandbox_path = f"/workspace/{path}"

                # Use execute to list directory as fallback
                result = await self.execute(f"ls -la {sandbox_path}")

                items = []
                lines = result.strip().split("\n")
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 9:
                        name = " ".join(parts[8:])
                        is_dir = parts[0].startswith("d")
                        if name not in (".", ".."):
                            items.append((name, is_dir))

                return items
            except Exception as e:
                logger.error(f"[OpenSandbox] Failed to list directory {path}: {e}")
                raise
        else:
            return await super().list_dir(path)
