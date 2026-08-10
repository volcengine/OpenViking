#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression test: mv() must enumerate every entry of a source directory.

``VikingFS._copy_dir_through_vikingfs`` drives the copy phase of ``mv()`` for
non-temp directories. It must pass ``node_limit=LS_ALL_NODES`` to ``ls()`` —
the agent-facing default of 1000 would silently truncate larger directories,
and the subsequent recursive source delete would destroy the uncopied entries.
"""

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


class TestCopyDirThroughVikingFS:
    """_copy_dir_through_vikingfs must not inherit the agent-facing ls cap."""

    @pytest.mark.asyncio
    async def test_enumerates_all_entries_with_ls_all_nodes(self):
        from openviking.storage.viking_fs import LS_ALL_NODES

        fs = _make_viking_fs()
        fs.mkdir = AsyncMock()
        fs._copy_file_through_vikingfs = AsyncMock()
        fs.ls = AsyncMock(
            return_value=[
                {"name": "a.md", "isDir": False},
                {"name": "b.md", "isDir": False},
            ]
        )

        await fs._copy_dir_through_vikingfs(
            "viking://resources/src", "viking://resources/dst"
        )

        fs.ls.assert_called_once_with(
            "viking://resources/src",
            show_all_hidden=True,
            node_limit=LS_ALL_NODES,
            ctx=None,
        )
        assert fs._copy_file_through_vikingfs.await_count == 2

    @pytest.mark.asyncio
    async def test_subdirectory_recursion_also_enumerates_all_entries(self):
        from openviking.storage.viking_fs import LS_ALL_NODES

        fs = _make_viking_fs()
        fs.mkdir = AsyncMock()
        fs._copy_file_through_vikingfs = AsyncMock()
        fs.ls = AsyncMock(
            side_effect=[
                [{"name": "sub", "isDir": True}],
                [{"name": "leaf.md", "isDir": False}],
            ]
        )

        await fs._copy_dir_through_vikingfs(
            "viking://resources/src", "viking://resources/dst"
        )

        assert fs.ls.await_count == 2
        for call in fs.ls.await_args_list:
            assert call.kwargs["node_limit"] == LS_ALL_NODES
