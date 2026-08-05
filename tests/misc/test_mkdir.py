#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VikingFS.mkdir() — verifies the target directory is actually created."""

import contextvars
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _make_viking_fs():
    """Create a VikingFS instance with mocked AGFS backend."""
    from openviking.storage.viking_fs import VikingFS

    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs._async_agfs = MagicMock()
    fs._async_agfs.mkdir = AsyncMock(return_value=None)
    fs.query_embedder = None
    fs.vector_store = None
    fs._uri_prefix = "viking://"
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx", default=None)
    return fs


class TestMkdir:
    """Test that mkdir() actually creates the target directory."""

    @pytest.mark.asyncio
    async def test_mkdir_calls_agfs_mkdir(self):
        """mkdir() must call _async_agfs.mkdir with the target path."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()

        await fs.mkdir("viking://resources/new_dir")

        fs._async_agfs.mkdir.assert_awaited_once()
        call_path = fs._async_agfs.mkdir.call_args[0][0]
        assert call_path.endswith("resources/new_dir")

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_true_existing(self):
        """mkdir(exist_ok=True) should swallow the already-exists error."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()
        fs._async_agfs.mkdir = AsyncMock(side_effect=FileExistsError("already exists"))

        await fs.mkdir("viking://resources/existing_dir", exist_ok=True)

        # mkdir was attempted, but the already-exists error was swallowed.
        fs._async_agfs.mkdir.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_true_not_existing(self):
        """mkdir(exist_ok=True) should create dir if it does not exist."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()

        await fs.mkdir("viking://resources/new_dir", exist_ok=True)

        fs._async_agfs.mkdir.assert_awaited_once()
        call_path = fs._async_agfs.mkdir.call_args[0][0]
        assert call_path.endswith("resources/new_dir")

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_false_default(self):
        """mkdir(exist_ok=False) should always attempt to create."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()

        await fs.mkdir("viking://resources/another_dir")

        fs._async_agfs.mkdir.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mkdir_ensures_parents_first(self):
        """mkdir() must call _ensure_parent_dirs before creating target."""
        fs = _make_viking_fs()
        call_order = []

        async def _parents(path, **kwargs):
            call_order.append("parents")

        async def _mkdir(path, **kwargs):
            call_order.append("mkdir")

        fs._ensure_parent_dirs = AsyncMock(side_effect=_parents)
        fs._async_agfs.mkdir = AsyncMock(side_effect=_mkdir)

        await fs.mkdir("viking://a/b/c")

        assert call_order == ["parents", "mkdir"]
