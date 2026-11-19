# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Verify Python FS context reaches the native account observer."""

import pytest

from openviking.pyagfs import AsyncAGFSClient


@pytest.mark.asyncio
async def test_native_account_lock_observation():
    import ragfs_python

    client = ragfs_python.RAGFSBindingClient()
    client.mount("memfs", "/data", {})
    agfs = AsyncAGFSClient(client)
    try:
        a = await agfs.pathlock_acquire_batch(
            [{"path": "/data/a", "kind": "exact"}], fs_ctx={"account_id": "A"}
        )
        b = await agfs.pathlock_acquire_batch(
            [{"path": "/data/b", "kind": "exact"}], fs_ctx={"account_id": "B"}
        )
        assert (await agfs.pathlock_observe())["active_locks"] == 2
        assert (await agfs.pathlock_observe(fs_ctx={"account_id": "A"}))["active_locks"] == 1
        assert (await agfs.pathlock_observe(fs_ctx={"account_id": "C"}))["active_locks"] == 0
        await agfs.pathlock_release(a, fs_ctx={"account_id": "A"})
        await agfs.pathlock_release(b, fs_ctx={"account_id": "B"})
        assert (await agfs.pathlock_observe(fs_ctx={"account_id": "A"}))["active_locks"] == 0
    finally:
        client.close()
