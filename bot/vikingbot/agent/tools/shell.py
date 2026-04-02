"""Shell execution tool."""

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from vikingbot.agent.tools.base import Tool
from vikingbot.config.schema import SessionKey


from vikingbot.sandbox.manager import SandboxManager


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
            },
            "required": ["command"],
        }

    async def execute(
        self,
        tool_context: "ToolContext",
        command: str,
        working_dir: str | None = None,
        **kwargs: Any,
    ) -> str:

        # Always use sandbox manager (includes direct mode)
        try:
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)

            if command.strip() == "pwd":
                return sandbox.sandbox_cwd

            return await sandbox.execute(command, timeout=self.timeout)
        except Exception as e:
            return f"Error executing: {str(e)}"
