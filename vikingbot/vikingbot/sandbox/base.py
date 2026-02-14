"""Abstract interface for sandbox backends."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class SandboxBackend(ABC):
    """Abstract base class for sandbox backends."""

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
        """Get the sandbox workspace directory."""


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
