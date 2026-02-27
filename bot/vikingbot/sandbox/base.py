"""Abstract interface for sandbox backends."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


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
        sandbox_path = self._resolve_path(path)
        self._check_path_restriction(sandbox_path)
        if not sandbox_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not sandbox_path.is_file():
            raise IOError(f"Not a file: {path}")
        return sandbox_path.read_text(encoding="utf-8")

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
