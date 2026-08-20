from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_mv_preserves_canonical_user_uris_for_vector_update(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acc", "default"), role=Role.ROOT)
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = AsyncMock()
    fs._async_agfs.stat.return_value = {"isDir": False}
    fs._async_agfs.pathlock_acquire_batch.return_value = {"lease_ref": "lease-1"}
    fs._collect_uris = AsyncMock(return_value=[])
    fs._copy_for_mv = AsyncMock()
    fs._update_vector_store_uris = AsyncMock()

    await fs.mv(
        "viking://user/default/peers/vaka/memories/profile.md",
        "viking://user/default/memories/profile.md",
        ctx=ctx,
    )

    fs._update_vector_store_uris.assert_awaited_once_with(
        ["viking://user/default/peers/vaka/memories/profile.md"],
        "viking://user/default/peers/vaka/memories/profile.md",
        "viking://user/default/memories/profile.md",
        ctx=ctx,
    )


@pytest.mark.asyncio
async def test_update_vector_store_uris_swallows_update_failure():
    ctx = RequestContext(user=UserIdentifier("acc", "default"), role=Role.ROOT)
    fs = VikingFS.__new__(VikingFS)
    fs.vector_store = AsyncMock()
    fs.vector_store.update_uri_mapping.side_effect = RuntimeError("vector unavailable")

    await fs._update_vector_store_uris(
        ["viking://user/default/source.md"],
        "viking://user/default/source.md",
        "viking://user/default/target.md",
        ctx=ctx,
    )
    fs.vector_store.update_uri_mapping.assert_awaited_once()
