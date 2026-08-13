"""Abstract interface for sandbox backends."""

import asyncio
import os
import posixpath
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SandboxFileInfo:
    """A regular file in the sandbox workspace."""

    path: str
    size: int


class SandboxBackend(ABC):
    """Abstract base class for sandbox backends."""

    def __init__(self):
        pass

    @abstractmethod
    async def start(self) -> None:
        """Start the sandbox instance."""

    @abstractmethod
    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        """Execute a command in the sandbox."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the sandbox instance and clean up resources."""

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the sandbox is running."""

    @property
    @abstractmethod
    def workspace(self) -> Path:
        """Get the sandbox workspace directory on the host."""

    @property
    def sandbox_cwd(self) -> str:
        """Get the current working directory inside the sandbox.

        Returns:
            Path string (e.g., "/", "/workspace")
        """
        return "/"

    def _check_path_restriction(self, path: Path) -> None:
        """Check if path is within workspace (if restricted).

        Args:
            path: Path to check

        Raises:
            PermissionError: If path outside workspace and restriction is enabled
        """

        workspace = self.workspace.resolve()
        resolved = path.resolve()

        if resolved != workspace and workspace not in resolved.parents:
            raise PermissionError(f"Path outside workspace: {path}")

    def _resolve_path(self, path: str) -> Path:
        """Resolve path to sandbox workspace.

        Args:
            path: Input path (absolute or relative)

        Returns:
            Resolved Path object in sandbox workspace
        """
        input_path = Path(path)
        if input_path.is_absolute():
            if path == "/":
                return self.workspace
            return self.workspace / path.lstrip("/")
        return self.workspace / path

    @staticmethod
    def _normalize_workspace_path(path: str) -> str:
        """Normalize a workspace-relative path used by bounded file APIs."""
        value = str(path)
        if value in {"", ".", "./"}:
            return ""
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"Path must be relative to the sandbox workspace: {path}")
        return candidate.as_posix().removeprefix("./")

    @staticmethod
    def _join_workspace_path(directory: str, name: str) -> str:
        if not name or name in {".", ".."} or "/" in name:
            raise IOError(f"Invalid sandbox directory entry: {name!r}")
        return posixpath.join(directory, name) if directory else name

    @staticmethod
    def _validate_max_bytes(max_bytes: int | None) -> None:
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")

    @staticmethod
    def _validate_max_entries(max_entries: int) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

    @staticmethod
    def _read_local_bytes(path: Path, original_path: str, max_bytes: int | None) -> bytes:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {original_path}")
        if not path.is_file():
            raise IOError(f"Not a file: {original_path}")
        with path.open("rb") as stream:
            data = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError(f"File exceeds the {max_bytes}-byte read limit: {original_path}")
        return data

    @staticmethod
    async def _collect_stream_bytes(
        stream: AsyncIterator[bytes],
        original_path: str,
        max_bytes: int | None,
    ) -> bytes:
        """Collect a byte stream while retaining at most ``max_bytes + 1`` bytes."""
        limit = None if max_bytes is None else max_bytes + 1
        data = bytearray()
        try:
            async for chunk in stream:
                if limit is None:
                    data.extend(chunk)
                    continue
                remaining = limit - len(data)
                if remaining <= 0:
                    break
                data.extend(chunk[:remaining])
                if len(data) >= limit:
                    break
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError(f"File exceeds the {max_bytes}-byte read limit: {original_path}")
        return bytes(data)

    async def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        """Read file bytes from sandbox (default implementation: host filesystem).

        Args:
            path: Path to file (absolute or relative to sandbox_cwd)
            max_bytes: Optional hard read limit. At most ``max_bytes + 1``
                bytes are read before an oversized file is rejected.

        Returns:
            File content as bytes

        Raises:
            FileNotFoundError: If file doesn't exist
            IOError: If read fails
            PermissionError: If path outside workspace and restriction is enabled
        """
        self._validate_max_bytes(max_bytes)
        sandbox_path = self._resolve_path(path)
        self._check_path_restriction(sandbox_path)
        return await asyncio.to_thread(self._read_local_bytes, sandbox_path, path, max_bytes)

    async def list_files(
        self,
        path: str = ".",
        *,
        max_entries: int,
    ) -> list[SandboxFileInfo]:
        """Recursively list regular files without unbounded workspace traversal.

        Returned paths are relative to the sandbox workspace, including ``path``
        when a subdirectory is requested. Directories and files both consume the
        inventory budget so a deep directory-only tree is bounded as well.
        """
        self._validate_max_entries(max_entries)
        root = self._normalize_workspace_path(path)
        root_path = self._resolve_path(root)
        self._check_path_restriction(root_path)

        def inventory() -> list[SandboxFileInfo]:
            pending = deque([(root, root_path)])
            files: list[SandboxFileInfo] = []
            visited = 0
            while pending:
                directory, local_directory = pending.popleft()
                try:
                    stream = os.scandir(local_directory)
                except FileNotFoundError:
                    continue
                with stream:
                    for entry in stream:
                        visited += 1
                        if visited > max_entries:
                            raise ValueError(
                                f"Sandbox workspace inventory exceeds {max_entries} entries"
                            )
                        relative = self._join_workspace_path(directory, entry.name)
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                pending.append((relative, Path(entry.path)))
                            elif entry.is_file(follow_symlinks=False):
                                files.append(
                                    SandboxFileInfo(
                                        path=relative,
                                        size=entry.stat(follow_symlinks=False).st_size,
                                    )
                                )
                        except FileNotFoundError:
                            # The Agent may still be writing while salvage takes its snapshot.
                            continue
            return sorted(files, key=lambda item: item.path)

        return await asyncio.to_thread(inventory)

    async def read_file(self, path: str) -> str:
        """Read file from sandbox (default implementation: host filesystem).

        Args:
            path: Path to file (absolute or relative to sandbox_cwd)

        Returns:
            File content as string

        Raises:
            FileNotFoundError: If file doesn't exist
            IOError: If read fails
            PermissionError: If path outside workspace and restriction is enabled
        """
        data = await self.read_file_bytes(path)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            # Some benchmark/tool artifacts may contain Windows-1252 smart quotes
            # (e.g. byte 0x92) or other non-UTF-8 bytes.  read_file is a user-facing
            # text tool; preserve progress by decoding lossily instead of failing
            # the entire agent rollout.
            return data.decode("utf-8", errors="replace")

    async def write_file(self, path: str, content: str) -> None:
        """Write file to sandbox (default implementation: host filesystem).

        Args:
            path: Path to file (absolute or relative to sandbox_cwd)
            content: Content to write

        Raises:
            IOError: If write fails
            PermissionError: If path outside workspace and restriction is enabled
        """
        sandbox_path = self._resolve_path(path)
        self._check_path_restriction(sandbox_path)
        sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox_path.write_text(content, encoding="utf-8")

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        """List directory in sandbox (default implementation: host filesystem).

        Args:
            path: Path to directory (absolute or relative to sandbox_cwd)

        Returns:
            List of (name, is_dir) tuples

        Raises:
            FileNotFoundError: If directory doesn't exist
            IOError: If not a directory
            PermissionError: If path outside workspace and restriction is enabled
        """
        sandbox_path = self._resolve_path(path)
        self._check_path_restriction(sandbox_path)
        if not sandbox_path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        if not sandbox_path.is_dir():
            raise IOError(f"Not a directory: {path}")

        items = []
        for item in sorted(sandbox_path.iterdir()):
            items.append((item.name, item.is_dir()))
        return items


class SandboxError(Exception):
    """Base exception for sandbox errors."""


class SandboxNotStartedError(SandboxError):
    """Raised when trying to execute commands in a non-started sandbox."""


class SandboxDisabledError(SandboxError):
    """Raised when sandbox functionality is disabled."""


class SandboxExecutionError(SandboxError):
    """Raised when sandbox command execution fails."""


class UnsupportedBackendError(SandboxError):
    """Raised when an unsupported sandbox backend is requested."""
