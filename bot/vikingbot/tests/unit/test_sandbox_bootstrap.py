"""Tests for SandboxManager._create_sandbox bootstrap-copy behavior.

The bootstrap files must be copied for a freshly created workspace. The
"workspace exists" check has to be captured BEFORE instance.start(), because
start() itself creates the workspace directory -- otherwise the post-start
existence check always sees the directory and never copies bootstrap files.
"""

from pathlib import Path
from types import SimpleNamespace

from vikingbot.sandbox.manager import SandboxManager


class _FakeBackend:
    """Backend whose start() materializes the workspace directory."""

    def __init__(self, sandbox_config, workspace_id, workspace):
        self.workspace = Path(workspace)

    async def start(self):
        self.workspace.mkdir(parents=True, exist_ok=True)


def _make_manager(tmp_path: Path):
    manager = SandboxManager.__new__(SandboxManager)
    manager.workspace = tmp_path
    manager.config = SimpleNamespace(sandbox=SimpleNamespace())
    manager._backend_cls = _FakeBackend
    manager._copy_calls = []

    async def _spy_copy(sandbox_workspace):
        manager._copy_calls.append(sandbox_workspace)

    manager._copy_bootstrap_files = _spy_copy
    return manager


async def test_bootstrap_copied_for_fresh_workspace(tmp_path):
    manager = _make_manager(tmp_path)

    await manager._create_sandbox("fresh_ws")

    assert manager._copy_calls == [tmp_path / "fresh_ws"]


async def test_bootstrap_not_copied_when_workspace_existed(tmp_path):
    manager = _make_manager(tmp_path)
    (tmp_path / "existing_ws").mkdir(parents=True, exist_ok=True)

    await manager._create_sandbox("existing_ws")

    assert manager._copy_calls == []
