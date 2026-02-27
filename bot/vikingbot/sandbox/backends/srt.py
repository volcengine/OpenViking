"""SRT backend implementation using @anthropic-ai/sandbox-runtime."""

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from loguru import logger

from vikingbot.sandbox.base import SandboxBackend, SandboxNotStartedError
from vikingbot.sandbox.backends import register_backend


from vikingbot.config.schema import SandboxConfig, SessionKey


@register_backend("srt")
class SrtBackend(SandboxBackend):
    """SRT backend using @anthropic-ai/sandbox-runtime."""

    def __init__(self, config, session_key: SessionKey, workspace: Path):
        # SRT has built-in isolation, restrict_to_workspace is not needed
        super().__init__()
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._process = None
        self._settings_path = self._generate_settings()
        self._wrapper_path = Path(__file__).parent / "srt-wrapper.mjs"
        # Find project root by looking for pyproject.toml
        self._project_root = Path(__file__).parent
        while (
            self._project_root.parent != self._project_root
            and not (self._project_root / "pyproject.toml").exists()
        ):
            self._project_root = self._project_root.parent
        self._response_queue = asyncio.Queue()
        self._task = None

    def _generate_settings(self) -> Path:
        """Generate SRT configuration file."""
        srt_config = self._load_config()

        settings_path = (
            Path.home()
            / ".vikingbot"
            / "sandboxes"
            / f"{self.session_key.safe_name()}-srt-settings.json"
        )
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        with open(settings_path, "w") as f:
            json.dump(srt_config, f, indent=2)

        return settings_path

    async def start(self) -> None:
        """Start SRT sandbox process."""
        self._workspace.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.config.backends.srt.node_path,
            str(self._wrapper_path),
            str(self._settings_path),
            str(self._workspace),
        ]
        logger.info(f"sandbox_cmd = {cmd}")
        logger.info(f"node_cwd = {self._project_root}")

        env = dict(os.environ)

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            cwd=str(self._project_root),
            env=env,
        )

        # Start reading responses from the wrapper
        self._task = asyncio.create_task(self._read_responses())

        # Also read stderr for debugging
        async def read_stderr():
            if not self._process or not self._process.stderr:
                return
            try:
                while True:
                    chunk = await self._process.stderr.read(4096)
                    if not chunk:
                        break
                    stderr_text = chunk.decode("utf-8", errors="replace")
                    if stderr_text.strip():
                        logger.error(f"[SRT wrapper stderr] {stderr_text}")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error reading stderr: {e}")

        asyncio.create_task(read_stderr())

        # Wait for ready signal
        response = await self._wait_for_response()
        if response.get("type") != "ready":
            raise RuntimeError(f"Unexpected response from wrapper: {response}")

        # Initialize the sandbox
        await self._send_message({"type": "initialize", "config": self._load_config()})

        response = await self._wait_for_response()
        if response.get("type") == "initialize_failed":
            errors = response.get("errors", [])
            warnings = response.get("warnings", [])
            if warnings:
                logger.warning(f"Sandbox warnings: {warnings}")
            raise RuntimeError(f"Failed to initialize sandbox: {errors}")

        if response.get("type") == "initialized":
            warnings = response.get("warnings", [])
            if warnings:
                logger.warning(f"Sandbox warnings: {warnings}")
            logger.info("SRT sandbox initialized successfully")
        else:
            raise RuntimeError(f"Unexpected response from wrapper: {response}")

    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        """Execute command in sandbox."""
        if not self._process:
            raise SandboxNotStartedError()

        if command.strip() == "pwd":
            return str(self._workspace.resolve())

        # Execute via wrapper
        custom_config = kwargs.get("custom_config")
        await self._send_message(
            {
                "type": "execute",
                "command": command,
                "timeout": timeout * 1000,  # Convert to milliseconds
                "customConfig": custom_config,
            }
        )

        response = await self._wait_for_response(timeout + 5)  # Extra 5 seconds buffer

        if response.get("type") == "error":
            raise RuntimeError(f"Execution error: {response.get('message')}")

        if response.get("type") != "executed":
            raise RuntimeError(f"Unexpected response from wrapper: {response}")

        output_parts = []
        stdout = response.get("stdout", "")
        stderr = response.get("stderr", "")
        exit_code = response.get("exitCode", 0)

        if stdout:
            output_parts.append(stdout)
        if stderr:
            output_parts.append(f"STDERR:\n{stderr}")
        if exit_code != 0:
            output_parts.append(f"\nExit code: {exit_code}")

        result = "\n".join(output_parts) if output_parts else "(no output)"

        # Log violations if any
        violations = response.get("violations", [])
        if violations:
            logger.warning(f"Sandbox violations during command execution: {violations}")

        # Log the execution result (truncated if too long)
        log_result = result[:2000] + ("... (truncated)" if len(result) > 2000 else "")
        logger.info(f"SRT execution result:\n{log_result}")

        max_len = 10000
        if len(result) > max_len:
            result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

        return result

    async def stop(self) -> None:
        """Stop sandbox process."""
        if self._process:
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"Error stopping response reader: {e}")

            if self._process.stdin:
                try:
                    await self._send_message({"type": "reset"})
                    # Wait a bit for reset
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

            self._process = None

    def is_running(self) -> bool:
        """Check if sandbox is running."""
        return self._process is not None and self._process.returncode is None

    @property
    def workspace(self) -> Path:
        """Get sandbox workspace directory."""
        return self._workspace

    @property
    def sandbox_cwd(self) -> str:
        """Get the current working directory inside the sandbox."""
        return str(self._workspace.resolve())

    def _load_config(self) -> dict[str, Any]:
        sandbox_workspace_str = str(self._workspace.resolve())
        allow_write = [sandbox_workspace_str]

        tmp_dir = "/tmp"
        if tmp_dir not in allow_write:
            allow_write.append(tmp_dir)

        return {
            "network": {
                "allowedDomains": self.config.network.allowed_domains,
                "deniedDomains": self.config.network.denied_domains,
                "allowLocalBinding": self.config.network.allow_local_binding,
            },
            "filesystem": {
                "denyRead": self.config.filesystem.deny_read,
                "allowWrite": allow_write,
                "denyWrite": self.config.filesystem.deny_write,
            },
        }

    async def read_file(self, path: str) -> str:
        if not self._process:
            raise SandboxNotStartedError()

        sandbox_path = path
        if not Path(path).is_absolute():
            sandbox_path = str(self._workspace.resolve() / path)

        await self._send_message({"type": "read_file", "path": sandbox_path})

        response = await self._wait_for_response()

        if response.get("type") == "error":
            raise RuntimeError(f"Read file error: {response.get('message')}")

        if response.get("type") != "file_read":
            raise RuntimeError(f"Unexpected response from wrapper: {response}")

        return response.get("content", "")

    async def write_file(self, path: str, content: str) -> None:
        if not self._process:
            raise SandboxNotStartedError()

        sandbox_path = path
        if not Path(path).is_absolute():
            sandbox_path = str(self._workspace.resolve() / path)

        await self._send_message({"type": "write_file", "path": sandbox_path, "content": content})

        response = await self._wait_for_response()

        if response.get("type") == "error":
            raise RuntimeError(f"Write file error: {response.get('message')}")

        if response.get("type") != "file_written":
            raise RuntimeError(f"Unexpected response from wrapper: {response}")

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        if not self._process:
            raise SandboxNotStartedError()

        sandbox_path = path
        if not Path(path).is_absolute():
            sandbox_path = str(self._workspace.resolve() / path)

        await self._send_message({"type": "list_dir", "path": sandbox_path})

        response = await self._wait_for_response()

        if response.get("type") == "error":
            raise RuntimeError(f"List dir error: {response.get('message')}")

        if response.get("type") != "dir_listed":
            raise RuntimeError(f"Unexpected response from wrapper: {response}")

        items = response.get("items", [])
        return [(item.get("name", ""), item.get("is_dir", False)) for item in items]

    async def _send_message(self, message: dict[str, Any]) -> None:
        """Send a message to the Node.js wrapper."""
        if not self._process or not self._process.stdin:
            raise SandboxNotStartedError()

        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode("utf-8"))
        await self._process.stdin.drain()

    async def _read_responses(self) -> None:
        """Read responses from the Node.js wrapper."""
        if not self._process or not self._process.stdout:
            return

        try:
            buffer = ""
            while True:
                chunk = await self._process.stdout.read(4096)
                if not chunk:
                    break

                buffer += chunk.decode("utf-8", errors="replace")
                lines = buffer.split("\n")
                buffer = lines.pop() or ""

                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        response = json.loads(line)
                        await self._response_queue.put(response)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse response: {e}, line: {line}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error reading responses: {e}")

    async def _wait_for_response(self, timeout: float = 30.0) -> dict[str, Any]:
        """Wait for a response from the wrapper."""
        try:
            return await asyncio.wait_for(self._response_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError("Timeout waiting for sandbox response")
