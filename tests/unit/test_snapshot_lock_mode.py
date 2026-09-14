# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility checks for the snapshot's non-reserving lock mode."""

from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.async_client import AsyncAGFSClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "forwarded"),
    [
        ({}, {}),
        ({"create_missing_dirs": True}, {}),
        ({"create_missing_dirs": False}, {"create_missing_dirs": False}),
    ],
)
async def test_tree_batch_only_forwards_non_default_mode(options, forwarded):
    client = AsyncAGFSClient(object())
    client.run = AsyncMock(return_value={"lease_ref": "test-lease"})
    paths = ["/local/account/user/alice/memories/deleted.md"]
    result = await client.pathlock_acquire_tree_batch(paths, **options)

    assert result == {"lease_ref": "test-lease"}
    client.run.assert_awaited_once_with(
        "pathlock_acquire_tree_batch", {"account_id": "account"}, paths, 0.0, None, **forwarded
    )


@pytest.mark.asyncio
async def test_non_reserving_mode_does_not_fall_back_to_legacy_reservations():
    class LegacyBinding:
        called = False

        def pathlock_acquire_tree_batch(self, ctx, paths, timeout_secs, owner_lease_ref):
            self.called = True
            return {"lease_ref": "unsafe-reservation"}

    binding = LegacyBinding()
    client = AsyncAGFSClient(binding)
    with pytest.raises(TypeError, match="create_missing_dirs"):
        await client.pathlock_acquire_tree_batch(
            ["/local/account/deleted.md"], create_missing_dirs=False
        )
    assert not binding.called
