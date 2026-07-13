from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, call

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_mv_canonicalizes_user_shorthand_before_vector_update(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acc", "default"), role=Role.ROOT)
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = AsyncMock()
    fs._async_agfs.stat.return_value = {"isDir": False}
    fs._collect_uris = AsyncMock(return_value=[])
    fs._copy_for_mv = AsyncMock()
    fs._update_vector_store_uris = AsyncMock()

    @asynccontextmanager
    async def unlocked(*_args, **_kwargs):
        yield None

    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: None)
    monkeypatch.setattr("openviking.storage.transaction.LockContext", unlocked)

    await fs.mv(
        "viking://user/peers/vaka/memories/profile.md",
        "viking://user/memories/profile.md",
        ctx=ctx,
    )

    fs._update_vector_store_uris.assert_awaited_once_with(
        ["viking://user/default/peers/vaka/memories/profile.md"],
        "viking://user/default/peers/vaka/memories/profile.md",
        "viking://user/default/memories/profile.md",
        ctx=ctx,
    )


@pytest.mark.asyncio
async def test_vector_uri_batch_rolls_back_completed_updates():
    ctx = RequestContext(user=UserIdentifier("acc", "default"), role=Role.ROOT)
    fs = VikingFS.__new__(VikingFS)
    fs.vector_store = AsyncMock()
    fs.vector_store.update_uri_mapping.side_effect = [True, RuntimeError("failed"), True]

    with pytest.raises(RuntimeError, match="failed"):
        await fs._update_vector_store_uris(
            ["viking://resources/old/a.md", "viking://resources/old/b.md"],
            "viking://resources/old",
            "viking://resources/new",
            ctx=ctx,
        )

    assert fs.vector_store.update_uri_mapping.await_args_list == [
        call(
            ctx=ctx,
            uri="viking://resources/old/a.md",
            new_uri="viking://resources/new/a.md",
        ),
        call(
            ctx=ctx,
            uri="viking://resources/old/b.md",
            new_uri="viking://resources/new/b.md",
        ),
        call(
            ctx=ctx,
            uri="viking://resources/new/a.md",
            new_uri="viking://resources/old/a.md",
        ),
    ]
