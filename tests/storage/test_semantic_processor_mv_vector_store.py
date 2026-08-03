from unittest.mock import AsyncMock, call

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


class _MoveAGFS:
    def __init__(self, *, source_path: str, source_is_dir: bool):
        self.source_path = source_path
        self.source_is_dir = source_is_dir
        self.rm_calls = []
        self.release_calls = []

    async def stat(self, path):
        if path == self.source_path:
            return {"isDir": self.source_is_dir}
        raise FileNotFoundError(path)

    async def pathlock_acquire_batch(self, requests, owner_lease_ref=None):
        return {"lease_ref": "operation"}

    async def pathlock_acquire_tree(self, path, owner_lease_ref=None):
        return {"lease_ref": "cleanup"}

    async def pathlock_release(self, lease):
        self.release_calls.append(lease)

    async def rm(self, path, recursive=False, fs_ctx=None):
        self.rm_calls.append((path, recursive, fs_ctx))
        return {}


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc", "default"), role=Role.ROOT)


def _move_fs(monkeypatch, *, source_is_dir: bool, vector_results):
    source_uri = "viking://resources/source"
    target_uri = "viking://resources/target"
    source_path = "/local/acc/resources/source"
    target_path = "/local/acc/resources/target"
    agfs = _MoveAGFS(source_path=source_path, source_is_dir=source_is_dir)
    vector_store = AsyncMock()
    vector_store.update_uri_mapping.side_effect = vector_results
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = agfs
    fs.vector_store = vector_store

    monkeypatch.setattr(fs, "_ensure_mutable_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fs, "_ensure_delete_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fs, "_copy_for_mv", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_collect_uris",
        AsyncMock(
            return_value=[f"{source_uri}/child.md"] if source_is_dir else [],
        ),
    )

    return fs, agfs, vector_store, source_uri, target_uri, source_path, target_path


@pytest.mark.asyncio
async def test_mv_canonicalizes_user_shorthand_before_vector_update(monkeypatch):
    ctx = _ctx()
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = AsyncMock()
    fs._async_agfs.stat.return_value = {"isDir": False}
    fs._async_agfs.pathlock_acquire_batch.return_value = {"lease_ref": "operation"}
    fs._collect_uris = AsyncMock(return_value=[])
    fs._copy_for_mv = AsyncMock()
    fs._update_vector_store_uris = AsyncMock()

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
async def test_update_vector_store_uris_raises_update_exception():
    ctx = _ctx()
    fs = VikingFS.__new__(VikingFS)
    fs.vector_store = AsyncMock()
    fs.vector_store.update_uri_mapping.side_effect = RuntimeError("vector unavailable")

    with pytest.raises(RuntimeError, match="vector unavailable"):
        await fs._update_vector_store_uris(
            ["viking://user/default/source.md"],
            "viking://user/default/source.md",
            "viking://user/default/target.md",
            ctx=ctx,
        )
    fs.vector_store.update_uri_mapping.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_vector_store_uris_raises_false_result():
    ctx = _ctx()
    fs = VikingFS.__new__(VikingFS)
    fs.vector_store = AsyncMock()
    fs.vector_store.update_uri_mapping.return_value = False

    with pytest.raises(RuntimeError, match="source.md"):
        await fs._update_vector_store_uris(
            ["viking://user/default/source.md"],
            "viking://user/default/source.md",
            "viking://user/default/target.md",
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_update_vector_store_uris_rolls_back_partial_batch():
    ctx = _ctx()
    old_base = "viking://resources/source"
    new_base = "viking://resources/target"
    first_uri = f"{old_base}/first.md"
    second_uri = f"{old_base}/second.md"
    first_target = f"{new_base}/first.md"
    fs = VikingFS.__new__(VikingFS)
    fs.vector_store = AsyncMock()
    fs.vector_store.update_uri_mapping.side_effect = [True, False, True]

    with pytest.raises(RuntimeError, match="second.md"):
        await fs._update_vector_store_uris(
            [first_uri, second_uri],
            old_base,
            new_base,
            ctx=ctx,
        )

    assert fs.vector_store.update_uri_mapping.await_args_list == [
        call(ctx=ctx, uri=first_uri, new_uri=first_target),
        call(ctx=ctx, uri=second_uri, new_uri=f"{new_base}/second.md"),
        call(ctx=ctx, uri=first_target, new_uri=first_uri),
    ]


@pytest.mark.asyncio
async def test_update_vector_store_uris_succeeds_for_complete_batch():
    ctx = _ctx()
    old_base = "viking://resources/source"
    new_base = "viking://resources/target"
    uris = [old_base, f"{old_base}/child.md"]
    fs = VikingFS.__new__(VikingFS)
    fs.vector_store = AsyncMock()
    fs.vector_store.update_uri_mapping.side_effect = [True, True]

    await fs._update_vector_store_uris(uris, old_base, new_base, ctx=ctx)

    assert fs.vector_store.update_uri_mapping.await_args_list == [
        call(ctx=ctx, uri=old_base, new_uri=new_base),
        call(
            ctx=ctx,
            uri=f"{old_base}/child.md",
            new_uri=f"{new_base}/child.md",
        ),
    ]


@pytest.mark.parametrize("source_is_dir", [False, True], ids=["file", "directory"])
@pytest.mark.asyncio
async def test_mv_vector_remap_failure_keeps_source_and_cleans_destination(
    monkeypatch, source_is_dir
):
    fs, agfs, _, source_uri, target_uri, source_path, target_path = _move_fs(
        monkeypatch,
        source_is_dir=source_is_dir,
        vector_results=[False],
    )

    with pytest.raises(RuntimeError):
        await fs.mv(source_uri, target_uri, ctx=_ctx())

    assert not any(path == source_path for path, _, _ in agfs.rm_calls)
    assert [(path, recursive) for path, recursive, _ in agfs.rm_calls] == [
        (target_path, source_is_dir)
    ]


@pytest.mark.parametrize("source_is_dir", [False, True], ids=["file", "directory"])
@pytest.mark.asyncio
async def test_mv_complete_vector_remap_deletes_source(monkeypatch, source_is_dir):
    vector_results = [True, True] if source_is_dir else [True]
    fs, agfs, _, source_uri, target_uri, source_path, _ = _move_fs(
        monkeypatch,
        source_is_dir=source_is_dir,
        vector_results=vector_results,
    )

    await fs.mv(source_uri, target_uri, ctx=_ctx())

    assert [(path, recursive) for path, recursive, _ in agfs.rm_calls] == [
        (source_path, source_is_dir)
    ]
