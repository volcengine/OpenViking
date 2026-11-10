#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VikingFS.mkdir() — verifies the target directory is actually created
and that backend errors are propagated instead of silently swallowed."""

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
        """mkdir() must call agfs.mkdir with the target path."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()

        await fs.mkdir("viking://resources/new_dir")

        fs._async_agfs.mkdir.assert_called_once()
        call_path = fs._async_agfs.mkdir.call_args[0][0]
        assert call_path.endswith("resources/new_dir")

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_true_existing(self):
        """mkdir(exist_ok=True) should tolerate an already-exists backend error."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()
        fs._async_agfs.mkdir = AsyncMock(side_effect=Exception("directory already exists"))

        await fs.mkdir("viking://resources/existing_dir", exist_ok=True)

        fs._async_agfs.mkdir.assert_called_once()

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_true_not_existing(self):
        """mkdir(exist_ok=True) should create dir if it does not exist."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()

        await fs.mkdir("viking://resources/new_dir", exist_ok=True)

        fs._async_agfs.mkdir.assert_called_once()
        call_path = fs._async_agfs.mkdir.call_args[0][0]
        assert call_path.endswith("resources/new_dir")

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_false_default(self):
        """mkdir(exist_ok=False) should always attempt to create."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()

        await fs.mkdir("viking://resources/another_dir")

        fs._async_agfs.mkdir.assert_called_once()

    @pytest.mark.asyncio
    async def test_mkdir_ensures_parents_first(self):
        """mkdir() must call _ensure_parent_dirs before creating target."""
        fs = _make_viking_fs()
        call_order = []
        fs._ensure_parent_dirs = AsyncMock(side_effect=lambda *a, **kw: call_order.append("parents"))
        fs._async_agfs.mkdir = AsyncMock(side_effect=lambda *a, **kw: call_order.append("mkdir"))

        await fs.mkdir("viking://a/b/c")

        assert call_order == ["parents", "mkdir"]

    @pytest.mark.asyncio
    async def test_mkdir_propagates_backend_error(self):
        """mkdir() must not swallow backend errors (permissions, quota, I/O)."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()
        fs._async_agfs.mkdir = AsyncMock(side_effect=Exception("permission denied"))

        with pytest.raises(Exception, match="permission denied"):
            await fs.mkdir("viking://resources/protected_dir")

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_true_propagates_non_exists_error(self):
        """mkdir(exist_ok=True) only tolerates already-exists errors, not others."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()
        fs._async_agfs.mkdir = AsyncMock(side_effect=Exception("resource exhausted: quota exceeded"))

        with pytest.raises(Exception, match="resource exhausted"):
            await fs.mkdir("viking://resources/full_dir", exist_ok=True)

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_false_raises_on_existing(self):
        """mkdir(exist_ok=False) surfaces the already-exists error to the caller."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()
        fs._async_agfs.mkdir = AsyncMock(side_effect=Exception("directory already exists"))

        with pytest.raises(Exception, match="already exists"):
            await fs.mkdir("viking://resources/existing_dir")
