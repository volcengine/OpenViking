"""OpenSandbox backend implementation using official SDK."""

import json
import posixpath
import shlex
from datetime import timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from vikingbot.config.schema import SandboxConfig, SessionKey
from vikingbot.sandbox.backends import register_backend
from vikingbot.sandbox.base import SandboxBackend, SandboxFileInfo, SandboxNotStartedError
from vikingbot.sandbox.managed_server import WORKSPACE_GID_LABEL, WORKSPACE_UID_LABEL


@register_backend("opensandbox")
class OpenSandboxBackend(SandboxBackend):
    def __init__(
        self,
        config: "SandboxConfig",
        session_key: SessionKey,
        workspace: Path,
        *,
        host_workspace: Path | None = None,
    ):
        # Paths belong to the remote container, not the Bot host filesystem.
        super().__init__()
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._host_workspace = host_workspace
        self._sandbox = None
        self._connection_config = None

        self._osb_config = config.backends.opensandbox

        self._server_url = self._osb_config.server_url

    async def start(self) -> None:
        self._workspace.mkdir(parents=True, exist_ok=True)

        try:
            from opensandbox.config import ConnectionConfig
            from opensandbox.sandbox import Sandbox

            self._connection_config = ConnectionConfig(
                domain=self._server_url,
                api_key=self._osb_config.api_key,
                request_timeout=timedelta(seconds=self._osb_config.startup_timeout),
                use_server_proxy=self._osb_config.use_server_proxy,
            )

            timeout_seconds = self._osb_config.runtime.timeout

            from opensandbox.models.sandboxes import Host, NetworkPolicy, NetworkRule, Volume

            volume_options = {}
            if self._osb_config.managed:
                if self._host_workspace is None:
                    raise ValueError("Managed OpenSandbox requires a dedicated host workspace")
                owner = self._host_workspace.stat()
                volume_options["metadata"] = {
                    WORKSPACE_UID_LABEL: str(owner.st_uid),
                    WORKSPACE_GID_LABEL: str(owner.st_gid),
                }
                # Numeric users may not have a passwd entry or access to image /root.
                volume_options["env"] = {"HOME": "/workspace"}
                volume_options["volumes"] = [
                    Volume(
                        name="workspace",
                        host=Host(path=str(self._host_workspace.resolve())),
                        mount_path="/workspace",
                        read_only=False,
                    )
                ]

            network = self._osb_config.network
            policy = NetworkPolicy(
                default_action="deny",
                egress=[
                    NetworkRule(action="deny", target=domain) for domain in network.denied_domains
                ]
                + [
                    NetworkRule(action="allow", target=domain) for domain in network.allowed_domains
                ],
            )
            self._sandbox = await Sandbox.create(
                self._osb_config.default_image,
                connection_config=self._connection_config,
                timeout=timedelta(seconds=timeout_seconds),
                ready_timeout=timedelta(seconds=self._osb_config.startup_timeout),
                resource={
                    "cpu": self._osb_config.runtime.cpu,
                    "memory": self._osb_config.runtime.memory,
                },
                network_policy=policy,
                **volume_options,
            )
            # External services still use an API-only workspace without local mounts.
            await self._sandbox.commands.run("mkdir -p /workspace")

            logger.info("OpenSandbox created successfully")

        except ImportError:
            logger.error(
                'OpenSandbox SDK missing or incompatible. Install: pip install "openviking[bot]"'
            )
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
            return "/workspace"

        try:
            from opensandbox.models.execd import RunCommandOpts

            opts = RunCommandOpts(timeout=timedelta(seconds=timeout))
            execution = await self._sandbox.commands.run(f"cd /workspace && {command}", opts=opts)

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

            error = getattr(execution, "error", None)
            exit_code = getattr(execution, "exit_code", 0)
            if error:
                stderr_text = "\n".join(filter(None, [stderr_text, f"{error.name}: {error.value}"]))
                exit_code = exit_code or 1

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
                logger.info("OpenSandbox stopped")
            except Exception as e:
                logger.warning("Error stopping sandbox: {}", e)
            finally:
                await self._sandbox.close()

        self._sandbox = None
        self._connection_config = None

    def is_running(self) -> bool:
        return self._sandbox is not None

    async def is_healthy(self) -> bool:
        if self._sandbox is None:
            return False
        healthy = await self._sandbox.is_healthy()
        if healthy:
            await self._sandbox.renew(timedelta(seconds=self._osb_config.runtime.timeout))
        return healthy

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def sandbox_cwd(self) -> str:
        return "/workspace"

    def _sandbox_path(self, path: str) -> str:
        if path.startswith("/"):
            return path
        relative = self._normalize_workspace_path(path)
        return self.sandbox_cwd if not relative else f"{self.sandbox_cwd}/{relative}"

    def local_file_path(self, path: str) -> Path | None:
        return None

    async def read_file(self, path: str) -> str:
        if not self._sandbox:
            raise SandboxNotStartedError()
        return await self._sandbox.files.read_file(self._sandbox_path(path))

    async def write_file(self, path: str, content: str) -> None:
        if not self._sandbox:
            raise SandboxNotStartedError()
        # Execd interprets mode as octal digits (644), not Python's 0o644 (=420).
        await self._sandbox.files.write_file(self._sandbox_path(path), content, mode=644)

    async def write_file_bytes(self, path: str, content: bytes) -> None:
        if not self._sandbox:
            raise SandboxNotStartedError()
        await self._sandbox.files.write_file(self._sandbox_path(path), content, mode=644)

    async def remove_tree(self, path: str) -> None:
        if not self._sandbox:
            raise SandboxNotStartedError()
        if not path or path.startswith("/") or ".." in Path(path).parts:
            raise PermissionError("remove_tree requires a safe sandbox-relative path")
        output = await self.execute(f"rm -rf -- {shlex.quote(self._sandbox_path(path))}")
        self._ensure_command_succeeded(output, "sandbox tree removal")

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        if not self._sandbox:
            raise SandboxNotStartedError()
        from opensandbox.models.execd import RunCommandOpts

        script = (
            "import json, os; "
            f"print(json.dumps([(e.name, e.is_dir()) for e in os.scandir({self._sandbox_path(path)!r})]))"
        )
        # Structured data must not go through execute()'s display truncation or
        # stderr formatting. SDK stdout chunks may split a JSON token anywhere.
        execution = await self._sandbox.commands.run(
            f"python3 -c {shlex.quote(script)}",
            opts=RunCommandOpts(timeout=timedelta(seconds=30)),
        )
        if execution.error:
            raise IOError(f"Sandbox directory listing failed: {execution.error.value}")
        output = "".join(message.text for message in execution.logs.stdout)
        return [(name, is_dir) for name, is_dir in json.loads(output)]

    async def list_files(
        self,
        path: str = ".",
        *,
        max_entries: int,
    ) -> list[SandboxFileInfo]:
        """List remote VKE files without materializing an unbounded search response."""
        if not self._sandbox:
            raise SandboxNotStartedError()
        self._validate_max_entries(max_entries)
        root = self._normalize_workspace_path(path)
        sandbox_root = self._sandbox_path(root or ".").rstrip("/")

        # OpenSandbox's search API returns one fully materialized JSON array and
        # has no server-side result limit. Traverse in the sandbox so both the
        # walk and the response stop at the service-owned inventory bound.
        script = "\n".join(
            [
                "import json, os",
                f"root = {sandbox_root!r}",
                f"limit = {max_entries}",
                f"pending = [({root!r}, root)]",
                "files = []",
                "visited = 0",
                "overflow = False",
                "while pending and not overflow:",
                "    relative_dir, directory = pending.pop()",
                "    try:",
                "        entries = os.scandir(directory)",
                "    except FileNotFoundError:",
                "        continue",
                "    with entries:",
                "        for entry in entries:",
                "            visited += 1",
                "            if visited > limit:",
                "                overflow = True",
                "                break",
                "            relative = os.path.join(relative_dir, entry.name)",
                "            try:",
                "                if entry.is_dir(follow_symlinks=False):",
                "                    pending.append((relative, entry.path))",
                "                elif entry.is_file(follow_symlinks=False):",
                "                    size = entry.stat(follow_symlinks=False).st_size",
                "                    files.append((relative, size))",
                "            except FileNotFoundError:",
                "                pass",
                'print(json.dumps({"overflow": overflow, "files": files}, separators=(",", ":")))',
            ]
        )

        from opensandbox.models.execd import RunCommandOpts

        execution = await self._sandbox.commands.run(
            f"python3 -c {shlex.quote(script)}",
            opts=RunCommandOpts(timeout=timedelta(seconds=30)),
        )
        if execution.error:
            raise IOError(f"Sandbox workspace inventory failed: {execution.error.value}")
        stdout = "".join(message.text for message in execution.logs.stdout)
        try:
            inventory = json.loads(stdout)
            overflow = inventory["overflow"]
            entries = inventory["files"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise IOError("Sandbox returned an invalid workspace inventory") from exc
        if not isinstance(overflow, bool) or not isinstance(entries, list):
            raise IOError("Sandbox returned an invalid workspace inventory")
        if overflow:
            raise ValueError(f"Sandbox workspace inventory exceeds {max_entries} entries")

        workspace_prefix = self.sandbox_cwd.rstrip("/") + "/"
        files: list[SandboxFileInfo] = []
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, list) or len(entry) != 2:
                raise IOError("Sandbox returned an invalid workspace inventory")
            remote_path, size = entry
            if not isinstance(remote_path, str):
                raise IOError("Sandbox returned an invalid workspace inventory")
            remote_path = posixpath.normpath(posixpath.join(self.sandbox_cwd, remote_path))
            if not remote_path.startswith(workspace_prefix):
                raise IOError("Sandbox returned a path outside the workspace")
            relative = remote_path.removeprefix(workspace_prefix)
            relative = self._normalize_workspace_path(relative)
            if not relative or relative in seen:
                continue
            if not isinstance(size, int) or size < 0:
                raise IOError(f"Sandbox did not report a valid file size: {relative}")
            seen.add(relative)
            files.append(SandboxFileInfo(path=relative, size=size))
        return sorted(files, key=lambda item: item.path)

    async def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        if not self._sandbox:
            raise SandboxNotStartedError()
        self._validate_max_bytes(max_bytes)
        range_header = None if max_bytes is None else f"bytes=0-{max_bytes}"
        stream = await self._sandbox.files.read_bytes_stream(
            self._sandbox_path(path),
            range_header=range_header,
        )
        return await self._collect_stream_bytes(stream, path, max_bytes)

    async def export_file(
        self,
        path: str,
        destination: Path,
        *,
        max_bytes: int | None = None,
    ) -> int:
        if not self._sandbox:
            raise SandboxNotStartedError()
        self._validate_max_bytes(max_bytes)
        range_header = None if max_bytes is None else f"bytes=0-{max_bytes}"
        stream = await self._sandbox.files.read_bytes_stream(
            self._sandbox_path(path),
            range_header=range_header,
        )
        return await self._export_stream_to_local(stream, destination, path, max_bytes)
