"""Sandbox manager for creating and managing sandbox instances."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from vikingbot.sandbox.base import SandboxBackend, SandboxDisabledError, UnsupportedBackendError
from vikingbot.sandbox.backends import get_backend

if TYPE_CHECKING:
    from vikingbot.config.schema import SandboxConfig


class SandboxManager:
    """Manager for creating and managing sandbox instances."""

    def __init__(self, config: "SandboxConfig", sandbox_parent_path: Path, source_workspace_path: Path):
        self.config = config
        self.workspace = sandbox_parent_path
        self.source_workspace = source_workspace_path
        self._sandboxes: dict[str, SandboxBackend] = {}
        self._shared_sandbox: SandboxBackend | None = None

        backend_cls = get_backend(config.backend)
        if not backend_cls:
            raise UnsupportedBackendError(f"Unknown sandbox backend: {config.backend}")
        self._backend_cls = backend_cls

    async def get_sandbox(self, session_key: str) -> SandboxBackend:
        """Get sandbox instance based on configuration mode."""

        if not self.config.enabled:
            raise SandboxDisabledError()

        if self.config.mode == "per-session":
            return await self._get_or_create_session_sandbox(session_key)
        elif self.config.mode == "shared":
            return await self._get_or_create_shared_sandbox()
        else:
            raise SandboxDisabledError()

    async def _get_or_create_session_sandbox(self, session_key: str) -> SandboxBackend:
        """Get or create session-specific sandbox."""
        if session_key not in self._sandboxes:
            sandbox = await self._create_sandbox(session_key)
            self._sandboxes[session_key] = sandbox
        return self._sandboxes[session_key]

    async def _get_or_create_shared_sandbox(self) -> SandboxBackend:
        """Get or create shared sandbox."""
        if self._shared_sandbox is None:
            self._shared_sandbox = await self._create_sandbox("shared")
        return self._shared_sandbox

    async def _create_sandbox(self, session_key: str) -> SandboxBackend:
        """Create new sandbox instance."""
        workspace = self.workspace / session_key.replace(":", "_")
        instance = self._backend_cls(self.config, session_key, workspace)
        try:
            await instance.start()
        except Exception as e:
            import traceback
            traceback.print_exc()
        await self._copy_bootstrap_files(workspace)
        return instance

    async def _copy_bootstrap_files(self, sandbox_workspace: Path) -> None:
        """Copy bootstrap files from source workspace to sandbox workspace."""
        from vikingbot.agent.context import ContextBuilder
        from vikingbot.agent.skills import BUILTIN_SKILLS_DIR
        import shutil

        # Copy from source workspace directly
        init_dir = self.source_workspace / ContextBuilder.INIT_DIR
        if init_dir.exists() and init_dir.is_dir():
            for item in init_dir.iterdir():
                src = init_dir / item.name
                dst = sandbox_workspace / item.name
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)

        # Copy source workspace skills (highest priority)
        skills_dir = self.source_workspace / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            dst_skills = sandbox_workspace / "skills"
            shutil.copytree(skills_dir, dst_skills, dirs_exist_ok=True)

        # Copy built-in skills (lower priority, dirs_exist_ok=True ensures source skills override)
        if BUILTIN_SKILLS_DIR.exists() and BUILTIN_SKILLS_DIR.is_dir():
            dst_skills = sandbox_workspace / "skills"
            shutil.copytree(BUILTIN_SKILLS_DIR, dst_skills, dirs_exist_ok=True)

        if not init_dir.exists():
            bootstrap_files = ContextBuilder.BOOTSTRAP_FILES
            for filename in bootstrap_files:
                src = self.source_workspace / filename
                if src.exists():
                    dst = sandbox_workspace / filename
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
        else:
            bootstrap_files = ContextBuilder.BOOTSTRAP_FILES
            for filename in bootstrap_files:
                src = self.source_workspace / filename
                if src.exists():
                    dst = sandbox_workspace / filename
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(src, dst)

    async def cleanup_session(self, session_key: str) -> None:
        """Clean up sandbox for a session."""
        if session_key in self._sandboxes:
            await self._sandboxes[session_key].stop()
            del self._sandboxes[session_key]

    async def cleanup_all(self) -> None:
        """Clean up all sandboxes."""
        for sandbox in self._sandboxes.values():
            await sandbox.stop()
        self._sandboxes.clear()

        if self._shared_sandbox:
            await self._shared_sandbox.stop()
            self._shared_sandbox = None
