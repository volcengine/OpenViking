#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Ensures VikingFS.append_file treats missing files as empty content Before writing."""

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.pyagfs.exceptions import AGFSClientError


def _make_viking_fs():
    """Create a VikingFS instance with all required hooks mocked."""
    from openviking.storage.viking_fs import VikingFS

    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = None
    fs.vector_store = None
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx", default=None)
    return fs


@pytest.mark.asyncio
async def test_append_file_missing_runtime_error():
    """Missing file should not crash when append_file reads a RuntimeError 'not found'."""
    fs = _make_viking_fs()
    fs._ensure_parent_dirs = AsyncMock()
    fs.agfs.read.side_effect = RuntimeError("not found: /default/session/.../messages.jsonl")
    fs.agfs.write = MagicMock()

    await fs.append_file("viking://session/default/foo/messages.jsonl", "hello\n")

    fs.agfs.write.assert_called_once()
    path, payload = fs.agfs.write.call_args[0]
    assert "messages.jsonl" in path
    assert payload == b"hello\n"


@pytest.mark.asyncio
async def test_append_file_missing_client_error():
    """AGFSClientError carrying 'not found' should also be treated as empty existing content."""
    fs = _make_viking_fs()
    fs._ensure_parent_dirs = AsyncMock()
    fs.agfs.read.side_effect = AGFSClientError("not found: /default/session/default/messages.jsonl")
    fs.agfs.write = MagicMock()

    await fs.append_file("viking://session/default/bar/messages.jsonl", "line\n")

    fs.agfs.write.assert_called_once()
    _, payload = fs.agfs.write.call_args[0]
    assert payload.endswith(b"line\n")


@pytest.mark.asyncio
async def test_append_file_other_runtime_error_bubbles():
    """RuntimeErrors without 'not found' should still propagate."""
    fs = _make_viking_fs()
    fs._ensure_parent_dirs = AsyncMock()
    fs.agfs.read.side_effect = RuntimeError("permission denied")

    with pytest.raises(RuntimeError):
        await fs.append_file("viking://session/default/bad/messages.jsonl", "x\n")
