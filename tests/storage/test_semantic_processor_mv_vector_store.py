import hashlib
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_vector_index_backend import (
    URI_REWRITE_RECORD_LIMIT,
    VikingVectorIndexBackend,
)
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
async def test_update_vector_store_uris_allows_missing_vector_record():
    ctx = _ctx()
    fs = VikingFS.__new__(VikingFS)
    fs.vector_store = AsyncMock()
    fs.vector_store.update_uri_mapping.return_value = None

    await fs._update_vector_store_uris(
        ["viking://resources/unindexed"],
        "viking://resources/unindexed",
        "viking://resources/moved",
        ctx=ctx,
    )


def _attach_strict_backend(backend):
    strict_backend = Mock()
    strict_backend.count_strict = AsyncMock()
    strict_backend.query_strict = AsyncMock()
    strict_backend.get_strict = AsyncMock()
    backend._get_backend_for_context = Mock(return_value=strict_backend)
    return strict_backend


@pytest.mark.asyncio
async def test_vector_backend_distinguishes_missing_record_from_failure():
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    strict_backend = _attach_strict_backend(backend)
    strict_backend.count_strict.return_value = 0

    result = await backend.update_uri_mapping(
        ctx=_ctx(),
        uri="viking://resources/unindexed",
        new_uri="viking://resources/moved",
    )

    assert result is None


@pytest.mark.asyncio
async def test_vector_backend_propagates_record_lookup_failures():
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    strict_backend = _attach_strict_backend(backend)
    strict_backend.count_strict.side_effect = RuntimeError("vector unavailable")

    with pytest.raises(RuntimeError, match="vector unavailable"):
        await backend.update_uri_mapping(
            ctx=_ctx(),
            uri="viking://resources/source.md",
            new_uri="viking://resources/target.md",
        )

    strict_backend.query_strict.assert_not_awaited()


def _vector_record(record_id, *, level, vector):
    return {
        "id": record_id,
        "uri": "viking://resources/source.md",
        "level": level,
        "vector": vector,
        "account_id": "acc",
    }


def _destination_record_id(level):
    suffix = "/.abstract.md" if level == 0 else "/.overview.md" if level == 1 else ""
    seed = f"acc:viking://resources/target.md{suffix}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_vector_backend_rejects_incomplete_record_rewrite():
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    strict_backend = _attach_strict_backend(backend)
    records = [
        _vector_record("old-l0", level=0, vector=[0.1]),
        _vector_record("old-l1", level=1, vector=[]),
    ]
    strict_backend.count_strict.return_value = len(records)
    strict_backend.query_strict.return_value = [
        {"id": record["id"]} for record in records
    ]
    strict_backend.get_strict.return_value = records
    backend.upsert = AsyncMock(return_value=True)
    backend.delete = AsyncMock(return_value=1)

    result = await backend.update_uri_mapping(
        ctx=_ctx(),
        uri="viking://resources/source.md",
        new_uri="viking://resources/target.md",
    )

    assert result is False
    backend.upsert.assert_not_awaited()
    backend.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_backend_rolls_back_destination_records_after_upsert_failure():
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    strict_backend = _attach_strict_backend(backend)
    records = [
        _vector_record("old-l0", level=0, vector=[0.1]),
        _vector_record("old-l1", level=1, vector=[0.2]),
    ]
    strict_backend.count_strict.return_value = len(records)
    strict_backend.query_strict.return_value = [
        {"id": record["id"]} for record in records
    ]
    first_new_id = _destination_record_id(0)
    second_new_id = _destination_record_id(1)
    strict_backend.get_strict.side_effect = [
        records,
        [],
        [{"id": first_new_id}, {"id": second_new_id}],
    ]
    backend.upsert = AsyncMock(
        side_effect=[first_new_id, RuntimeError("second upsert failed")]
    )
    backend.delete = AsyncMock(return_value=2)

    with pytest.raises(RuntimeError, match="second upsert failed"):
        await backend.update_uri_mapping(
            ctx=_ctx(),
            uri="viking://resources/source.md",
            new_uri="viking://resources/target.md",
        )

    backend.delete.assert_awaited_once_with(
        [first_new_id, second_new_id],
        ctx=_ctx(),
    )


@pytest.mark.asyncio
async def test_vector_backend_restores_old_records_after_delete_failure():
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    strict_backend = _attach_strict_backend(backend)
    records = [
        _vector_record("old-l0", level=0, vector=[0.1]),
        _vector_record("old-l1", level=1, vector=[0.2]),
    ]
    strict_backend.count_strict.return_value = len(records)
    strict_backend.query_strict.return_value = [
        {"id": record["id"]} for record in records
    ]
    strict_backend.get_strict.side_effect = [
        records,
        [],
        [
            {"id": _destination_record_id(0)},
            {"id": _destination_record_id(1)},
        ],
    ]
    backend.upsert = AsyncMock(
        side_effect=[
            _destination_record_id(0),
            _destination_record_id(1),
            "old-l0",
            "old-l1",
        ]
    )
    backend.delete = AsyncMock(
        side_effect=[RuntimeError("old delete failed"), 2]
    )

    with pytest.raises(RuntimeError, match="old delete failed"):
        await backend.update_uri_mapping(
            ctx=_ctx(),
            uri="viking://resources/source.md",
            new_uri="viking://resources/target.md",
        )

    assert backend.upsert.await_count == 4
    assert [call.args[0]["id"] for call in backend.upsert.await_args_list[-2:]] == [
        "old-l0",
        "old-l1",
    ]
    assert backend.delete.await_count == 2


@pytest.mark.asyncio
async def test_vector_backend_restores_preexisting_destination_after_failure():
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    strict_backend = _attach_strict_backend(backend)
    records = [
        _vector_record("old-l0", level=0, vector=[0.1]),
        _vector_record("old-l1", level=1, vector=[0.2]),
    ]
    first_new_id = _destination_record_id(0)
    previous_destination = {
        "id": first_new_id,
        "uri": "viking://resources/target.md",
        "level": 0,
        "vector": [9.9],
        "account_id": "acc",
    }
    strict_backend.count_strict.return_value = len(records)
    strict_backend.query_strict.return_value = [
        {"id": record["id"]} for record in records
    ]
    strict_backend.get_strict.side_effect = [
        records,
        [previous_destination],
        [previous_destination],
    ]
    backend.upsert = AsyncMock(
        side_effect=[
            first_new_id,
            RuntimeError("second upsert failed"),
            first_new_id,
        ]
    )
    backend.delete = AsyncMock()

    with pytest.raises(RuntimeError, match="second upsert failed"):
        await backend.update_uri_mapping(
            ctx=_ctx(),
            uri="viking://resources/source.md",
            new_uri="viking://resources/target.md",
        )

    assert backend.upsert.await_args_list[-1].args[0] == previous_destination
    backend.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_backend_requests_the_complete_uri_record_set():
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    strict_backend = _attach_strict_backend(backend)
    records = [
        _vector_record("old-l0", level=0, vector=[0.1]),
        _vector_record("old-l1", level=1, vector=[0.2]),
    ]
    strict_backend.count_strict.return_value = len(records)
    strict_backend.query_strict.return_value = [
        {"id": record["id"]} for record in records
    ]
    strict_backend.get_strict.side_effect = [records, []]
    backend.upsert = AsyncMock(
        side_effect=[_destination_record_id(0), _destination_record_id(1)]
    )
    backend.delete = AsyncMock(return_value=2)

    result = await backend.update_uri_mapping(
        ctx=_ctx(),
        uri="viking://resources/source.md",
        new_uri="viking://resources/target.md",
    )

    assert result is True
    assert strict_backend.query_strict.await_args.kwargs["limit"] == len(records)


@pytest.mark.asyncio
async def test_vector_backend_rejects_record_sets_above_safe_limit():
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    strict_backend = _attach_strict_backend(backend)
    strict_backend.count_strict.return_value = URI_REWRITE_RECORD_LIMIT + 1
    backend.upsert = AsyncMock()
    backend.delete = AsyncMock()

    result = await backend.update_uri_mapping(
        ctx=_ctx(),
        uri="viking://resources/source.md",
        new_uri="viking://resources/target.md",
    )

    assert result is False
    strict_backend.query_strict.assert_not_awaited()
    strict_backend.get_strict.assert_not_awaited()
    backend.upsert.assert_not_awaited()
    backend.delete.assert_not_awaited()


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


@pytest.mark.parametrize("source_is_dir", [False, True], ids=["file", "directory"])
@pytest.mark.asyncio
async def test_mv_without_vector_record_deletes_source(monkeypatch, source_is_dir):
    vector_results = [None, None] if source_is_dir else [None]
    fs, agfs, _, source_uri, target_uri, source_path, _ = _move_fs(
        monkeypatch,
        source_is_dir=source_is_dir,
        vector_results=vector_results,
    )

    await fs.mv(source_uri, target_uri, ctx=_ctx())

    assert [(path, recursive) for path, recursive, _ in agfs.rm_calls] == [
        (source_path, source_is_dir)
    ]
