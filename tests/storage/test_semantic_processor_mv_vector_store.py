from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_mv_preserves_canonical_user_uris_for_vector_update(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acc", "default"), role=Role.ROOT)
    fs = VikingFS.__new__(VikingFS)
    fs.acl_manager = None
    fs._async_agfs = AsyncMock()

    async def stat(path):
        if path.endswith("/peers/vaka/memories/profile.md"):
            return {"isDir": False}
        if path.endswith("/user/default/memories"):
            return {"isDir": True}
        raise FileNotFoundError(path)

    fs._async_agfs.stat.side_effect = stat
    fs._async_agfs.pathlock_acquire_batch.return_value = {
        "lease_ref": "operation-ref",
        "owner_id": "operation-owner",
        "ownership_ref": "operation-ownership",
        "owned": True,
    }
    fs._collect_uris = AsyncMock(return_value=[])
    fs._copy_for_mv = AsyncMock()
    fs._update_vector_store_uris = AsyncMock()

    await fs.mv(
        "viking://user/default/peers/vaka/memories/profile.md",
        "viking://user/default/memories/profile.md",
        ctx=ctx,
    )

    fs._update_vector_store_uris.assert_awaited_once_with(
        "viking://user/default/peers/vaka/memories/profile.md",
        "viking://user/default/memories/profile.md",
        recursive=False,
        ctx=ctx,
    )


@pytest.mark.asyncio
async def test_update_vector_store_uris_propagates_update_failure():
    ctx = RequestContext(user=UserIdentifier("acc", "default"), role=Role.ROOT)
    fs = VikingFS.__new__(VikingFS)
    fs.vector_store = AsyncMock()
    fs.vector_store.update_uri_mapping.side_effect = RuntimeError("vector unavailable")

    with pytest.raises(RuntimeError, match="vector unavailable"):
        await fs._update_vector_store_uris(
            "viking://user/default/source.md",
            "viking://user/default/target.md",
            recursive=False,
            ctx=ctx,
        )
    fs.vector_store.update_uri_mapping.assert_awaited_once()
