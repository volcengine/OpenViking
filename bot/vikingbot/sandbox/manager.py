"""Sandbox manager for creating and managing sandbox instances."""

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from openviking.async_client import logger
from vikingbot.sandbox.base import SandboxBackend, SandboxDisabledError, UnsupportedBackendError
from vikingbot.sandbox.backends import get_backend


from vikingbot.config.schema import SandboxConfig, SessionKey, Config


class SandboxManager:
    """Manager for creating and managing sandbox instances.

    Thread-safety model
    -------------------
    All public methods are async and must be called from the same event-loop
    thread (asyncio single-threaded).  ``_creation_lock`` serialises creation
    for the same workspace key so that concurrent ``get_sandbox()`` callers
    never leak a duplicate backend.  The lock is **not** held during the
    cached-hit fast path, and it is **never** acquired by ``cleanup_session``
    or ``cleanup_all``, so there is no deadlock risk with eviction / timer
    tasks that tear down idle sandboxes.
    """

    COPY_BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]

    def __init__(self, config: Config, sandbox_parent_path: Path, source_workspace_path: Path):
        self.config = config
        self.workspace = sandbox_parent_path
        self.source_workspace = source_workspace_path
        self._sandboxes: dict[str, SandboxBackend] = {}
        self._creation_lock = asyncio.Lock()
        backend_cls = get_backend(config.sandbox.backend)
        if not backend_cls:
            raise UnsupportedBackendError(f"Unknown sandbox backend: {config.backend}")
        self._backend_cls = backend_cls

    async def get_sandbox(self, session_key: SessionKey) -> SandboxBackend:
        """Return an existing sandbox for *session_key*, creating one if necessary.

        Creation is serialised per-manager via ``_creation_lock`` so that
        concurrent callers for the same workspace cannot race and leak an
        orphaned backend.
        """
        return await self._get_or_create_sandbox(session_key)

    async def _get_or_create_sandbox(self, session_key: SessionKey) -> SandboxBackend:
        """Get or create session-specific sandbox.

        Uses a double-checked locking pattern: if the workspace is already
        cached the fast path returns immediately without touching the lock.
        Inside the lock the cache is re-checked so only the first caller
        performs the (potentially expensive) creation.
        """
        workspace_id = self.to_workspace_id(session_key)
        if workspace_id in self._sandboxes:
            return self._sandboxes[workspace_id]
        async with self._creation_lock:
            if workspace_id not in self._sandboxes:  # re-check inside lock
                self._sandboxes[workspace_id] = await self._create_sandbox(workspace_id)
        return self._sandboxes[workspace_id]

    async def _create_sandbox(self, workspace_id: str) -> SandboxBackend:
        """Create a new sandbox backend instance and start it.

        If the creating task is cancelled during startup the backend is
        stopped (best-effort) before the cancellation propagates, so the
        next creation attempt starts from a clean state.
        """
        workspace = self.workspace / workspace_id
        instance = self._backend_cls(self.config.sandbox, workspace_id, workspace)
        try:
            await instance.start()
        except asyncio.CancelledError:
            # Best-effort stop so a partially-started backend (e.g. a Docker
            # container that was created but not fully initialised) is torn
            # down before the cancellation propagates to the caller.
            with contextlib.suppress(Exception):
                await instance.stop()
            raise
        except Exception:
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
        """Clean up sandbox for a session.

        Does **not** acquire ``_creation_lock`` so it cannot deadlock with
        ``get_sandbox``.  The entry is popped from the cache *before* the
        (awaitable) ``stop()`` call so that a concurrent ``cleanup_session``
        for the same key is a no-op instead of stopping the backend twice
        and raising ``KeyError`` on the second delete.
        """
        workspace_id = self.to_workspace_id(session_key)
        sandbox = self._sandboxes.pop(workspace_id, None)
        if sandbox is not None:
            await sandbox.stop()

    async def cleanup_all(self) -> None:
        """Clean up all sandboxes.

        Entries are popped one at a time so the dict is never mutated while
        being iterated (``stop()`` is an await point where creations or other
        cleanups may interleave); anything added mid-cleanup is torn down too.
        """
        while self._sandboxes:
            _, sandbox = self._sandboxes.popitem()
            await sandbox.stop()

    def get_workspace_path(self, session_key: SessionKey) -> Path:
        return self.workspace / self.to_workspace_id(session_key)

    def to_workspace_id(self, session_key: SessionKey):
        if self.config.sandbox.mode == "shared":
            return "shared"
        elif self.config.sandbox.mode == "per-channel":
            return session_key.channel_key()
        else:  # per-session
            return session_key.safe_name()

    async def get_sandbox_cwd(self, session_key: SessionKey) -> str:
        sandbox: SandboxBackend = await self._get_or_create_sandbox(session_key)
        return sandbox.sandbox_cwd
