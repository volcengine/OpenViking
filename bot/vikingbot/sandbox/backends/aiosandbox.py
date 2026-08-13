"""AIO Sandbox backend implementation using agent-sandbox SDK."""

import posixpath
from pathlib import Path
from typing import Any

from loguru import logger

from vikingbot.config.schema import SandboxConfig, SessionKey
from vikingbot.sandbox.backends import register_backend
from vikingbot.sandbox.base import SandboxBackend, SandboxFileInfo, SandboxNotStartedError


@register_backend("aiosandbox")
class AioSandboxBackend(SandboxBackend):
    """AIO Sandbox backend using agent-sandbox SDK."""

    def __init__(self, config: "SandboxConfig", session_key: SessionKey, workspace: Path):
        super().__init__()
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._client = None
        self._base_url = config.backends.aiosandbox.base_url

    async def start(self) -> None:
        """Start the AIO Sandbox instance."""
        self._workspace.mkdir(parents=True, exist_ok=True)

        try:
            from agent_sandbox import AsyncSandbox

            logger.info("[AioSandbox] Connecting to {}", self._base_url)
            self._client = AsyncSandbox(base_url=self._base_url)
            logger.info("[AioSandbox] Connected successfully")
        except ImportError:
            logger.error(
                "agent-sandbox SDK not installed. Install with: uv pip install agent-sandbox>=0.0.23"
            )
            raise
        except Exception as e:
            logger.error("[AioSandbox] Failed to start: {}", e)
            raise

    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        """Execute command in AIO Sandbox."""
        if not self._client:
            raise SandboxNotStartedError()

        if command.strip() == "pwd":
            return "/home/gem"

        try:
            result = await self._client.shell.exec_command(command=command, timeout=timeout)

            output_parts = []
            if hasattr(result, "data") and hasattr(result.data, "output") and result.data.output:
                output_parts.append(result.data.output)
            if (
                hasattr(result, "data")
                and hasattr(result.data, "exit_code")
                and result.data.exit_code != 0
            ):
                output_parts.append(f"\nExit code: {result.data.exit_code}")

            result_text = "\n".join(output_parts) if output_parts else "(no output)"

            log_result = result_text[:2000] + ("... (truncated)" if len(result_text) > 2000 else "")
            logger.info(f"[AioSandbox] Output:\n{log_result}")

            max_len = 10000
            if len(result_text) > max_len:
                result_text = (
                    result_text[:max_len]
                    + f"\n... (truncated, {len(result_text) - max_len} more chars)"
                )

            return result_text
        except Exception as e:
            logger.error(f"[AioSandbox] Error: {e}")
            import traceback

            logger.error(f"[AioSandbox] Traceback:\n{traceback.format_exc()}")
            raise

    async def stop(self) -> None:
        """Stop the AIO Sandbox instance."""
        self._client = None
        logger.info("[AioSandbox] Stopped")

    def is_running(self) -> bool:
        """Check if AIO Sandbox is running."""
        return self._client is not None

    @property
    def workspace(self) -> Path:
        """Get sandbox workspace directory."""
        return self._workspace

    @property
    def sandbox_cwd(self) -> str:
        """Get the current working directory inside the sandbox."""
        return "/home/gem"

    def _sandbox_path(self, path: str) -> str:
        if path.startswith("/"):
            return path
        relative = self._normalize_workspace_path(path)
        return self.sandbox_cwd if not relative else f"{self.sandbox_cwd}/{relative}"

    async def _list_path(self, path: str) -> list[Any]:
        if not self._client:
            raise SandboxNotStartedError()
        result = await self._client.file.list_path(
            path=self._sandbox_path(path),
            recursive=False,
            show_hidden=True,
            include_size=True,
        )
        data = getattr(result, "data", result)
        return list(getattr(data, "files", None) or [])

    @staticmethod
    def _file_info_size(file_info: Any, path: str) -> int:
        size = getattr(file_info, "size", None)
        if not isinstance(size, int) or size < 0:
            raise IOError(f"Sandbox did not report a valid file size: {path}")
        return size

    async def read_file(self, path: str) -> str:
        """Read file from AIO Sandbox using SDK."""
        if not self._client:
            raise SandboxNotStartedError()

        try:
            result = await self._client.file.read_file(file=self._sandbox_path(path))
            if hasattr(result, "data") and hasattr(result.data, "content"):
                return result.data.content
            return str(result)
        except Exception as e:
            logger.error(f"[AioSandbox] Failed to read file {path}: {e}")
            raise

    async def write_file(self, path: str, content: str) -> None:
        """Write file to AIO Sandbox using SDK."""
        if not self._client:
            raise SandboxNotStartedError()

        try:
            result = await self._client.file.write_file(
                file=self._sandbox_path(path), content=content
            )
            if not result.success:
                raise Exception(f"Write failed: {result.message}")
        except Exception as e:
            logger.error(f"[AioSandbox] Failed to write file {path}: {e}")
            raise

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        """List directory in AIO Sandbox using SDK."""
        try:
            return sorted(
                (
                    (str(file_info.name), bool(file_info.is_directory))
                    for file_info in await self._list_path(path)
                ),
                key=lambda item: item[0],
            )
        except Exception as e:
            logger.error(f"[AioSandbox] Failed to list directory {path}: {e}")
            raise

    async def list_files(
        self,
        path: str = ".",
        *,
        max_entries: int,
    ) -> list[SandboxFileInfo]:
        """Recursively enumerate the remote AIO workspace with a server-side bound."""
        if not self._client:
            raise SandboxNotStartedError()
        self._validate_max_entries(max_entries)
        root = self._normalize_workspace_path(path)
        sandbox_root = self._sandbox_path(root or ".")
        result = await self._client.file.glob_files(
            path=sandbox_root,
            pattern="**",
            include_hidden=True,
            files_only=False,
            include_metadata=True,
            max_results=max_entries + 1,
            sort_by="path",
        )
        data = getattr(result, "data", result)
        entries = list(getattr(data, "files", None) or [])
        total_count = getattr(data, "total_count", None)
        normalized_entries = []
        for entry in entries:
            remote_path = str(getattr(entry, "path", ""))
            if not remote_path.startswith("/"):
                remote_path = posixpath.join(sandbox_root, remote_path)
            normalized_entries.append((entry, posixpath.normpath(remote_path)))
        root_entries = sum(remote_path == sandbox_root for _, remote_path in normalized_entries)
        if (
            bool(getattr(data, "truncated", False))
            or len(entries) - root_entries > max_entries
            or (isinstance(total_count, int) and total_count - root_entries > max_entries)
        ):
            raise ValueError(f"Sandbox workspace inventory exceeds {max_entries} entries")

        workspace_prefix = self.sandbox_cwd.rstrip("/") + "/"
        root_prefix = sandbox_root.rstrip("/") + "/"
        files: list[SandboxFileInfo] = []
        seen: set[str] = set()
        for file_info, remote_path in normalized_entries:
            if remote_path != sandbox_root and not remote_path.startswith(root_prefix):
                raise IOError("Sandbox returned a path outside the requested workspace root")
            if remote_path == self.sandbox_cwd.rstrip("/"):
                continue
            if not remote_path.startswith(workspace_prefix):
                raise IOError("Sandbox returned a path outside the workspace")
            relative = self._normalize_workspace_path(remote_path.removeprefix(workspace_prefix))
            if not relative or relative in seen or bool(getattr(file_info, "is_directory", False)):
                continue
            seen.add(relative)
            files.append(
                SandboxFileInfo(
                    path=relative,
                    size=self._file_info_size(file_info, relative),
                )
            )
        return sorted(files, key=lambda item: item.path)

    async def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        """Stream bytes from the remote workspace, retaining at most limit + 1 bytes."""
        if not self._client:
            raise SandboxNotStartedError()
        self._validate_max_bytes(max_bytes)
        stream = self._client.file.download_file(path=self._sandbox_path(path))
        return await self._collect_stream_bytes(stream, path, max_bytes)
