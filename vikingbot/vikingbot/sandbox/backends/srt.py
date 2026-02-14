"""SRT backend implementation using @anthropic-ai/sandbox-runtime."""

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vikingbot.sandbox.base import SandboxBackend, SandboxNotStartedError
from vikingbot.sandbox.backends import register_backend

if TYPE_CHECKING:
    from vikingbot.config.schema import SandboxConfig


@register_backend("srt")
class SrtBackend(SandboxBackend):
    """SRT backend using @anthropic-ai/sandbox-runtime."""

    def __init__(self, config, session_key: str, workspace: Path):
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._process = None
        self._settings_path = self._generate_settings()

    def _generate_settings(self) -> Path:
        """Generate SRT configuration file."""
        srt_config = {
            "network": {
                "allowedDomains": self.config.network.allowed_domains,
                "deniedDomains": self.config.network.denied_domains,
                "allowLocalBinding": self.config.network.allow_local_binding
            },
            "filesystem": {
                "denyRead": self.config.filesystem.deny_read,
                "allowWrite": self.config.filesystem.allow_write,
                "denyWrite": self.config.filesystem.deny_write
            }
        }

        settings_path = Path.home() / ".vikingbot" / "sandboxes" / f"{self.session_key.replace(':', '_')}-srt-settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        with open(settings_path, "w") as f:
            json.dump(srt_config, f, indent=2)

        return settings_path

    async def start(self) -> None:
        """Start SRT sandbox process."""
        self._workspace.mkdir(parents=True, exist_ok=True)

        cmd = [
            "node",
            "-e",
            self._get_wrapper_script(),
            "--settings", str(self._settings_path),
            "--workspace", str(self._workspace)
        ]

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        """Execute command in sandbox."""
        if not self._process:
            raise SandboxNotStartedError()

        # TODO: Implement proper IPC communication with SRT wrapper
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._workspace,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
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

        max_len = 10000
        if len(result) > max_len:
            result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

        return result

    async def stop(self) -> None:
        """Stop sandbox process."""
        if self._process:
            self._process.terminate()
            await self._process.wait()
            self._process = None

    def is_running(self) -> bool:
        """Check if sandbox is running."""
        return self._process is not None and self._process.returncode is None

    @property
    def workspace(self) -> Path:
        """Get sandbox workspace directory."""
        return self._workspace

    def _get_wrapper_script(self) -> str:
        """Get Node.js wrapper script."""
        return """
        const { SandboxManager } = require('@anthropic-ai/sandbox-runtime');

        async function main() {
            const config = require(process.argv[2]);
            await SandboxManager.initialize(config);

            console.log('SRT sandbox initialized');
        }

        main().catch(console.error);
        """
