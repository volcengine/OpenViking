# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for bootstrap files reaching a sandbox workspace.

Workspace existence has to be snapshotted *before* ``instance.start()``,
because start() itself materialises the workspace directory on disk. A
post-start existence check therefore always sees the directory present and
never copies the bootstrap files into a fresh workspace.
"""

# This test lives at the root of ``tests/api_test`` because that is the directory the
# api-test workflow collects (``cd tests/api_test && pytest .``); ``bot/tests`` is not
# reached by any workflow.  ``vikingbot`` lives under ``bot/`` and is not installed by
# the repo-wide test environment.  At this depth ``parents[2]`` is the repository root.
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bot"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


import pytest  # noqa: E402
from vikingbot.config.schema import Config, SessionKey  # noqa: E402
from vikingbot.sandbox.manager import SandboxManager  # noqa: E402

BOOTSTRAP_FILE = "AGENTS.md"
BOOTSTRAP_CONTENT = "bootstrap instructions"


class MaterializingBackend:
    """Backend whose start() creates the workspace directory, as real ones do."""

    def __init__(self, config, workspace_id, workspace):
        self.workspace = workspace

    async def start(self):
        self.workspace.mkdir(parents=True, exist_ok=True)

    async def stop(self):
        pass


def _session_key(chat_id: str) -> SessionKey:
    return SessionKey(type="cli", channel_id="default", chat_id=chat_id)


def _manager(tmp_path):
    source_workspace = tmp_path / "source"
    source_workspace.mkdir(parents=True, exist_ok=True)
    (source_workspace / BOOTSTRAP_FILE).write_text(BOOTSTRAP_CONTENT, encoding="utf-8")

    manager = SandboxManager(Config(), tmp_path / "sandboxes", source_workspace)
    manager._backend_cls = MaterializingBackend
    return manager


@pytest.mark.asyncio
async def test_bootstrap_files_copied_for_fresh_workspace(tmp_path):
    manager = _manager(tmp_path)
    session_key = _session_key("fresh")
    workspace = manager.get_workspace_path(session_key)
    assert not workspace.exists()

    try:
        await manager.get_sandbox(session_key)
    finally:
        await manager.cleanup_all()

    assert (workspace / BOOTSTRAP_FILE).read_text(encoding="utf-8") == BOOTSTRAP_CONTENT


@pytest.mark.asyncio
async def test_existing_workspace_is_not_overwritten(tmp_path):
    manager = _manager(tmp_path)
    session_key = _session_key("existing")
    workspace = manager.get_workspace_path(session_key)
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        await manager.get_sandbox(session_key)
    finally:
        await manager.cleanup_all()

    assert not (workspace / BOOTSTRAP_FILE).exists()
