"""Sandbox manager for creating and managing sandbox instances."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from openviking.async_client import logger
from vikingbot.sandbox.base import SandboxBackend, SandboxDisabledError, UnsupportedBackendError
from vikingbot.sandbox.backends import get_backend


from vikingbot.config.schema import SandboxConfig, SessionKey, Config


class SandboxManager:
    """Manager for creating and managing sandbox instances."""

    COPY_BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]

    def __init__(self, config: Config, sandbox_parent_path: Path, source_workspace_path: Path):
        self.config = config
        self.workspace = sandbox_parent_path
        self.source_workspace = source_workspace_path
        self._sandboxes: dict[str, SandboxBackend] = {}
        backend_cls = get_backend(config.sandbox.backend)
        if not backend_cls:
            raise UnsupportedBackendError(f"Unknown sandbox backend: {config.backend}")
        self._backend_cls = backend_cls

    async def get_sandbox(self, session_key: SessionKey) -> SandboxBackend:
        return await self._get_or_create_sandbox(session_key)

    async def _get_or_create_sandbox(self, session_key: SessionKey) -> SandboxBackend:
        """Get or create session-specific sandbox."""
        workspace_id = self.to_workspace_id(session_key)
        if workspace_id not in self._sandboxes:
            sandbox = await self._create_sandbox(workspace_id)
            self._sandboxes[workspace_id] = sandbox
        return self._sandboxes[workspace_id]

    async def _create_sandbox(self, workspace_id: str) -> SandboxBackend:
        """Create new sandbox instance."""
        workspace = self.workspace / workspace_id
        instance = self._backend_cls(self.config.sandbox, workspace_id, workspace)
        try:
            await instance.start()
        except Exception as e:
            import traceback

            traceback.print_exc()
        if not workspace.exists():
            await self._copy_bootstrap_files(workspace)
        return instance

    async def _copy_bootstrap_files(self, sandbox_workspace: Path) -> None:
        """Copy bootstrap files from source workspace to sandbox workspace."""
        from vikingbot.agent.context import ContextBuilder
        from vikingbot.agent.skills import BUILTIN_SKILLS_DIR
        import shutil

        # Copy from source workspace init directory (if exists)
        init_dir = self.source_workspace / ContextBuilder.INIT_DIR
        if init_dir.exists() and init_dir.is_dir():
            for item in init_dir.iterdir():
                src = init_dir / item.name
                dst = sandbox_workspace / item.name
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)

        # Always copy bootstrap files from source workspace root
        for filename in self.COPY_BOOTSTRAP_FILES:
            src = self.source_workspace / filename
            if src.exists():
                dst = sandbox_workspace / filename
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Copy source workspace skills (highest priority)
        skills_dir = self.source_workspace / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            for item in skills_dir.iterdir():
                if item.name not in self.config.skills or []:
                    continue
                dst_skill = sandbox_workspace / "skills" / item.name
                if dst_skill.exists():
                    continue
                shutil.copytree(item, dst_skill, dirs_exist_ok=True)

    async def cleanup_session(self, session_key: SessionKey) -> None:
        """Clean up sandbox for a session."""
        workspace_id = self.to_workspace_id(session_key)
        if workspace_id in self._sandboxes:
            await self._sandboxes[workspace_id].stop()
            del self._sandboxes[workspace_id]

    async def cleanup_all(self) -> None:
        """Clean up all sandboxes."""
        for sandbox in self._sandboxes.values():
            await sandbox.stop()
        self._sandboxes.clear()

    def get_workspace_path(self, session_key: SessionKey) -> Path:
        return self.workspace / self.to_workspace_id(session_key)

    def to_workspace_id(self, session_key: SessionKey):
        if self.config.sandbox.mode == "shared":
            return "shared"
        else:
            return session_key.safe_name()

    async def get_sandbox_cwd(self, session_key: SessionKey) -> str:
        sandbox: SandboxBackend = await self._get_or_create_sandbox(session_key)
        return sandbox.sandbox_cwd
