"""Sandbox manager for creating and managing sandbox instances."""

import asyncio
from pathlib import Path

from loguru import logger

from vikingbot.config.schema import Config, SessionKey
from vikingbot.sandbox.backends import get_backend
from vikingbot.sandbox.base import SandboxBackend, UnsupportedBackendError
from vikingbot.utils.session_paths import resolve_workspace_path, workspace_name


class SandboxManager:
    """Manager for creating and managing sandbox instances."""

    COPY_BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]

    def __init__(self, config: Config, sandbox_parent_path: Path, source_workspace_path: Path):
        from vikingbot.agent.remote_skill_cache import RemoteSkillSnapshotCache

        self.config = config
        self.workspace = sandbox_parent_path
        self.source_workspace = source_workspace_path
        self._sandboxes: dict[str, SandboxBackend] = {}
        self._create_lock = asyncio.Lock()
        self.remote_skill_cache = (
            RemoteSkillSnapshotCache(config)
            if getattr(config, "remote_skills", None) is not None
            and getattr(config, "bot_data_path", None) is not None
            else None
        )
        backend_cls = get_backend(config.sandbox.backend)
        if not backend_cls:
            raise UnsupportedBackendError(f"Unknown sandbox backend: {config.backend}")
        self._backend_cls = backend_cls

    async def get_sandbox(self, session_key: SessionKey) -> SandboxBackend:
        return await self._get_or_create_sandbox(session_key)

    async def _get_or_create_sandbox(self, session_key: SessionKey) -> SandboxBackend:
        """Get or create session-specific sandbox."""
        workspace_id = self.to_workspace_id(session_key)
        async with self._create_lock:
            sandbox = self._sandboxes.get(workspace_id)
            if sandbox is not None and self.config.sandbox.backend == "opensandbox":
                if not await sandbox.is_healthy():
                    await self.cleanup_session(session_key)
                    sandbox = None
            if sandbox is None:
                sandbox = await self._create_sandbox(
                    workspace_id,
                    self.get_workspace_path(session_key),
                )
                self._sandboxes[workspace_id] = sandbox
            return sandbox

    async def _create_sandbox(self, workspace_id: str, workspace: Path) -> SandboxBackend:
        """Create new sandbox instance."""
        backend_options = {}
        managed = self.config.uses_managed_opensandbox
        if managed:
            root = self.config.opensandbox_workspaces_path
            # Never mount credentials, the parent runtime directory, or a symlink escape.
            if root.is_symlink() or workspace.resolve() == root.resolve():
                raise ValueError("Invalid OpenSandbox host workspace")
            if not workspace.resolve().is_relative_to(root.resolve()):
                raise ValueError(
                    "OpenSandbox host workspace must be under its workspaces directory"
                )
            backend_options["host_workspace"] = workspace
        instance = self._backend_cls(
            self.config.sandbox, workspace_id, workspace, **backend_options
        )
        needs_bootstrap = not workspace.exists()
        try:
            if needs_bootstrap and self.config.sandbox.backend == "opensandbox":
                await self._copy_bootstrap_files(workspace)
            await instance.start()
            if not workspace.exists():
                await self._copy_bootstrap_files(workspace)
            if self.config.sandbox.backend == "opensandbox" and not managed:
                # External services have no local mount; upload bootstrap inputs via APIs.
                for name in [*self.COPY_BOOTSTRAP_FILES, "skills"]:
                    root = workspace / name
                    paths = root.rglob("*") if root.is_dir() else [root]
                    for path in paths:
                        if path.is_file() and not path.is_symlink():
                            if path.resolve().is_relative_to(workspace.resolve()):
                                await instance.write_file_bytes(
                                    path.relative_to(workspace).as_posix(),
                                    path.read_bytes(),
                                )
        except BaseException:
            logger.exception(f"Failed to start sandbox for workspace {workspace_id}")
            try:
                await instance.stop()
            except Exception:
                logger.exception(f"Failed to clean up sandbox for workspace {workspace_id}")
            raise
        return instance

    async def _copy_bootstrap_files(self, sandbox_workspace: Path) -> None:
        """Copy bootstrap files from source workspace to sandbox workspace."""
        import shutil

        from vikingbot.agent.context import ContextBuilder

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
        sandbox = self._sandboxes.pop(workspace_id, None)
        if sandbox is not None:
            await sandbox.stop()

    async def cleanup_all(self) -> None:
        """Clean up all sandboxes."""
        sandboxes = list(self._sandboxes.values())
        self._sandboxes.clear()
        for sandbox in sandboxes:
            try:
                await sandbox.stop()
            except Exception:
                logger.exception("Failed to clean up sandbox")

    def get_workspace_path(self, session_key: SessionKey) -> Path:
        # Compile managers receive a task-specific root shared by parent and child agents.
        if session_key.type == "compile":
            return self.workspace
        return resolve_workspace_path(
            self.workspace,
            session_key,
            self.config.sandbox.mode,
        )

    def to_workspace_id(self, session_key: SessionKey):
        return workspace_name(session_key, self.config.sandbox.mode, portable=False)

    async def get_sandbox_cwd(self, session_key: SessionKey) -> str:
        sandbox: SandboxBackend = await self._get_or_create_sandbox(session_key)
        return sandbox.sandbox_cwd
