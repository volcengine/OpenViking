# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for AsyncOpenViking singleton path enforcement (issue #3546)."""

import os
import tempfile

import pytest
import pytest_asyncio

from openviking import AsyncOpenViking, SyncOpenViking


@pytest_asyncio.fixture
async def clean_singleton():
    """Ensure singleton is reset before and after each test."""
    await AsyncOpenViking.reset()
    yield
    await AsyncOpenViking.reset()


class TestAsyncOpenVikingSingletonPath:
    """Regression tests for the singleton path check."""

    async def test_same_path_returns_singleton_without_error(self, clean_singleton):
        """Constructing the singleton twice with the same path is a no-op (idempotent)."""
        with tempfile.TemporaryDirectory() as root:
            client_a = AsyncOpenViking(path=root)
            client_b = AsyncOpenViking(path=root)
            assert client_a is client_b

    async def test_different_path_raises_clear_error(self, clean_singleton):
        """Reconstructing with a different path raises ValueError with actionable message."""
        with tempfile.TemporaryDirectory() as root:
            workspace_a = os.path.join(root, "a")
            workspace_b = os.path.join(root, "b")
            AsyncOpenViking(path=workspace_a)

            with pytest.raises(ValueError) as exc_info:
                AsyncOpenViking(path=workspace_b)

            assert "only one embedded" in str(exc_info.value).lower()
            assert workspace_b in str(exc_info.value)
            assert workspace_a in str(exc_info.value)

    async def test_different_path_after_close_is_allowed(self, clean_singleton):
        """After close(), a different workspace can be constructed.

        close() leaves the singleton instance in place (__new__ returns the same
        object, __init__ reconfigures it). The effective workspace must change.
        """
        with tempfile.TemporaryDirectory() as root:
            workspace_a = os.path.join(root, "a")
            workspace_b = os.path.join(root, "b")

            client_a = AsyncOpenViking(path=workspace_a)
            await client_a.close()

            client_b = AsyncOpenViking(path=workspace_b)
            # Singleton instance is reused; only the effective workspace changes.
            # Compare resolved forms to be portable across platforms where
            # TemporaryDirectory() exposes /var/... but Path.resolve() stores
            # /private/var/... on macOS.
            assert client_b is client_a
            assert os.path.realpath(client_b._path) == os.path.realpath(workspace_b)
            assert os.path.realpath(client_a._path) == os.path.realpath(workspace_b)

    async def test_different_path_after_reset_is_allowed(self, clean_singleton):
        """After reset(), a different workspace can be constructed."""
        with tempfile.TemporaryDirectory() as root:
            workspace_a = os.path.join(root, "a")
            workspace_b = os.path.join(root, "b")

            client_a = AsyncOpenViking(path=workspace_a)
            await AsyncOpenViking.reset()

            client_b = AsyncOpenViking(path=workspace_b)
            assert client_b is not client_a

    async def test_resolved_paths_are_compared(self, clean_singleton):
        """Paths that resolve to the same realpath are treated as equal."""
        with tempfile.TemporaryDirectory() as root:
            real_path = os.path.join(root, "workspace")
            os.makedirs(real_path)
            # Symlink to the same directory
            symlink_path = os.path.join(root, "symlink")
            os.symlink(real_path, symlink_path)

            # Both should work — same real path
            client_a = AsyncOpenViking(path=real_path)
            client_b = AsyncOpenViking(path=symlink_path)
            assert client_a is client_b

    async def test_explicit_then_implicit_same_workspace(self, clean_singleton):
        """AsyncOpenViking(path=<workspace>) followed by AsyncOpenViking() succeeds.

        The implicit call (path=None) resolves through the shared config to the
        same effective workspace that was set explicitly, so no error is raised.
        """
        with tempfile.TemporaryDirectory() as root:
            workspace = os.path.join(root, "workspace")
            os.makedirs(workspace)

            # Set an explicit workspace first
            client_a = AsyncOpenViking(path=workspace)
            # Implicit call resolves through config to the same workspace
            client_b = AsyncOpenViking()
            assert client_b is client_a
            assert os.path.realpath(client_b._path) == os.path.realpath(workspace)

    async def test_implicit_then_explicit_same_workspace(self, clean_singleton):
        """AsyncOpenViking() followed by AsyncOpenViking(path=<same workspace>) succeeds.

        The first implicit call establishes the workspace from config.
        The second explicit call requests the same effective workspace.
        """
        with tempfile.TemporaryDirectory() as root:
            workspace = os.path.join(root, "workspace")
            os.makedirs(workspace)

            # First call uses config's default workspace
            client_a = AsyncOpenViking()
            default_path = client_a._path

            # Second call explicitly requests the same path as the implicit default
            client_b = AsyncOpenViking(path=default_path)
            assert client_b is client_a
            assert os.path.realpath(client_b._path) == os.path.realpath(default_path)

    async def test_sync_client_raises_same_error(self, clean_singleton):
        """SyncOpenViking raises the same error when its underlying AsyncOpenViking has a different path."""
        with tempfile.TemporaryDirectory() as root:
            workspace_a = os.path.join(root, "a")
            workspace_b = os.path.join(root, "b")

            _ = SyncOpenViking(path=workspace_a)

            with pytest.raises(ValueError) as exc_info:
                SyncOpenViking(path=workspace_b)

            assert "only one embedded" in str(exc_info.value).lower()
