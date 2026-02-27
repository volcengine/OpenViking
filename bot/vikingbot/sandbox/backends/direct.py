"""Direct backend implementation - executes commands directly on host without sandboxing."""

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from vikingbot.sandbox.base import SandboxBackend
from vikingbot.sandbox.backends import register_backend


from vikingbot.config.schema import SandboxConfig, SessionKey


@register_backend("direct")
class DirectBackend(SandboxBackend):
    """Direct backend that executes commands directly on the host."""

    def __init__(self, config: "SandboxConfig", session_key: SessionKey, workspace: Path):
        super().__init__()
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._running = False

    async def start(self) -> None:
        """Start the backend (no-op for direct backend)."""
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._running = True
        logger.info("Direct backend started")

    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        """Execute a command directly on the host."""
        if not self._running:
            raise RuntimeError("Direct backend not started")

        logger.info("[Direct] Executing: {}", repr(command))

        cwd = kwargs.get("working_dir", str(self._workspace))

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                return f"Error: Command timed out after {timeout} seconds"

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            log_result = result[:2000] + ("... (truncated)" if len(result) > 2000 else "")
            logger.info(f"[Direct] Output:\n{log_result}")

            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

            return result

        except Exception as e:
            logger.error(f"[Direct] Error: {e}")
            import traceback

            logger.error(f"[Direct] Traceback:\n{traceback.format_exc()}")
            raise

    async def stop(self) -> None:
        """Stop the backend (no-op for direct backend)."""
        self._running = False
        logger.info("Direct backend stopped")

    def is_running(self) -> bool:
        """Check if backend is running."""
        return self._running

    @property
    def workspace(self) -> Path:
        """Get the workspace directory."""
        return self._workspace

    @property
    def sandbox_cwd(self) -> str:
        """Get the current working directory (uses actual host cwd)."""
        return str(self._workspace)

    async def read_file(self, path: str) -> str:
        sandbox_path = Path(path)
        if not sandbox_path.is_absolute():
            sandbox_path = self._workspace / path

        self._check_path_restriction(sandbox_path)
        if not sandbox_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not sandbox_path.is_file():
            raise IOError(f"Not a file: {path}")
        return sandbox_path.read_text(encoding="utf-8")

    async def write_file(self, path: str, content: str) -> None:
        sandbox_path = Path(path)
        if not sandbox_path.is_absolute():
            sandbox_path = self._workspace / path

        self._check_path_restriction(sandbox_path)
        sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox_path.write_text(content, encoding="utf-8")

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        sandbox_path = Path(path)
        if not sandbox_path.is_absolute():
            sandbox_path = self._workspace / path

        self._check_path_restriction(sandbox_path)
        if not sandbox_path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        if not sandbox_path.is_dir():
            raise IOError(f"Not a directory: {path}")

        items = []
        for item in sorted(sandbox_path.iterdir()):
            items.append((item.name, item.is_dir()))
        return items
