#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for VikingFS.mkdir() — verifies the target directory is actually created."""

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
    fs.agfs.mkdir = MagicMock(return_value=None)
    fs.query_embedder = None
    fs.vector_store = None
    fs._uri_prefix = "viking://"
    return fs


class TestMkdir:
    """Test that mkdir() actually creates the target directory."""

    @pytest.mark.asyncio
    async def test_mkdir_calls_agfs_mkdir(self):
        """mkdir() must call agfs.mkdir with the target path."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()
        fs.stat = AsyncMock(side_effect=Exception("not found"))

        await fs.mkdir("viking://resources/new_dir")

        fs.agfs.mkdir.assert_called_once()
        call_path = fs.agfs.mkdir.call_args[0][0]
        assert call_path.endswith("resources/new_dir")

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_true_existing(self):
        """mkdir(exist_ok=True) should return early if directory exists."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()
        fs.stat = AsyncMock(return_value={"type": "directory"})

        await fs.mkdir("viking://resources/existing_dir", exist_ok=True)

        # Should NOT call agfs.mkdir because directory already exists
        fs.agfs.mkdir.assert_not_called()

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_true_not_existing(self):
        """mkdir(exist_ok=True) should create dir if it does not exist."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()
        fs.stat = AsyncMock(side_effect=Exception("not found"))

        await fs.mkdir("viking://resources/new_dir", exist_ok=True)

        fs.agfs.mkdir.assert_called_once()
        call_path = fs.agfs.mkdir.call_args[0][0]
        assert call_path.endswith("resources/new_dir")

    @pytest.mark.asyncio
    async def test_mkdir_exist_ok_false_default(self):
        """mkdir(exist_ok=False) should always attempt to create."""
        fs = _make_viking_fs()
        fs._ensure_parent_dirs = AsyncMock()

        await fs.mkdir("viking://resources/another_dir")

        fs.agfs.mkdir.assert_called_once()

    @pytest.mark.asyncio
    async def test_mkdir_ensures_parents_first(self):
        """mkdir() must call _ensure_parent_dirs before creating target."""
        fs = _make_viking_fs()
        call_order = []
        fs._ensure_parent_dirs = AsyncMock(side_effect=lambda p: call_order.append("parents"))
        fs.agfs.mkdir = MagicMock(side_effect=lambda p: call_order.append("mkdir"))

        await fs.mkdir("viking://a/b/c")

        assert call_order == ["parents", "mkdir"]
