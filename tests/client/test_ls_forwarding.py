# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Filesystem option forwarding at the public embedded-client boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.async_client import AsyncOpenViking


async def test_async_openviking_ls_forwards_ordering_and_limit_options():
    client = object.__new__(AsyncOpenViking)
    client._ensure_initialized = AsyncMock()
    client._client = SimpleNamespace(ls=AsyncMock(return_value=[]))

    await client.ls(
        "viking://session",
        node_limit=200,
        sort_by="mtime",
        sort_order="desc",
    )

    client._client.ls.assert_awaited_once_with(
        "viking://session",
        recursive=False,
        simple=False,
        output="original",
        abs_limit=256,
        show_all_hidden=False,
        node_limit=200,
        sort_by="mtime",
        sort_order="desc",
    )


async def test_async_openviking_tree_hides_internal_files_by_default():
    client = object.__new__(AsyncOpenViking)
    client._ensure_initialized = AsyncMock()
    client._client = SimpleNamespace(tree=AsyncMock(return_value={}))

    await client.tree("viking://session")

    client._client.tree.assert_awaited_once_with(
        "viking://session",
        output="original",
        abs_limit=128,
        show_all_hidden=False,
        node_limit=1000,
    )
