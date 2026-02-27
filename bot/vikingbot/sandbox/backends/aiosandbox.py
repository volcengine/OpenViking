"""AIO Sandbox backend implementation using agent-sandbox SDK."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from vikingbot.sandbox.base import SandboxBackend, SandboxNotStartedError
from vikingbot.sandbox.backends import register_backend


from vikingbot.config.schema import SandboxConfig, SessionKey


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
            logger.error("agent-sandbox SDK not installed. Install with: pip install agent-sandbox")
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

    async def read_file(self, path: str) -> str:
        """Read file from AIO Sandbox using SDK."""
        if not self._client:
            raise SandboxNotStartedError()

        try:
            sandbox_path = path
            if not path.startswith("/"):
                sandbox_path = f"/home/gem/{path}"

            result = await self._client.file.read_file(file=sandbox_path)
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
            sandbox_path = path
            if not path.startswith("/"):
                sandbox_path = f"/home/gem/{path}"

            result = await self._client.file.write_file(file=sandbox_path, content=content)
            if not result.success:
                raise Exception(f"Write failed: {result.message}")
        except Exception as e:
            logger.error(f"[AioSandbox] Failed to write file {path}: {e}")
            raise

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        """List directory in AIO Sandbox using SDK."""
        if not self._client:
            raise SandboxNotStartedError()

        try:
            sandbox_path = path
            if not path.startswith("/"):
                sandbox_path = f"/home/gem/{path}"

            # Use find_files with "*" glob to list directory
            result = await self._client.file.find_files(path=sandbox_path, glob="*")

            items = []
            if hasattr(result, "data") and hasattr(result.data, "files"):
                for file_info in result.data.files:
                    if hasattr(file_info, "name") and hasattr(file_info, "type"):
                        is_dir = file_info.type == "directory"
                        items.append((file_info.name, is_dir))

            return items
        except Exception as e:
            logger.error(f"[AioSandbox] Failed to list directory {path}: {e}")
            raise
