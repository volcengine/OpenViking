# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSNetworkError
from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import StorageException
from openviking.storage.expr import Eq, In, Or, PathScope
from openviking.storage.vectordb.index.cuvs_index import matches_filter
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.exceptions import UnavailableError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.ROOT,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_outage", [False, True])
async def test_strict_recursive_delete_clears_orphan_vector_subtree_when_source_is_missing(
    monkeypatch,
    storage_outage,
):
    session_uri = "viking://user/alice/sessions/session-1"
    session_path = "/local/acct/user/alice/sessions/session-1"
    error = (
        AGFSNetworkError("endpoint not found")
        if storage_outage
        else FileNotFoundError(session_path)
    )
    agfs = SimpleNamespace(stat=AsyncMock(side_effect=error))
    vector_store = SimpleNamespace(
        delete_uris=AsyncMock(),
        delete_uri_scope=AsyncMock(),
        count=AsyncMock(return_value=0),
    )
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = agfs
    fs.vector_store = vector_store
    fs.acl_manager = None
    fs._deletion_guard = None
    fs._bound_ctx = SimpleNamespace(get=lambda: None)
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: session_path)
    monkeypatch.setattr(fs, "_path_to_uri", lambda path, ctx=None: session_uri)
    monkeypatch.setattr(fs, "_collect_uris", AsyncMock(return_value=[]))
    monkeypatch.setattr(fs, "_confirm_fs_scope_cleared", AsyncMock())

    if storage_outage:
        with pytest.raises(UnavailableError, match="endpoint not found"):
            await fs.rm(session_uri, recursive=True, ctx=_ctx(), strict=True)
        vector_store.delete_uris.assert_not_awaited()
        vector_store.delete_uri_scope.assert_not_awaited()
        return

    await fs.rm(session_uri, recursive=True, ctx=_ctx(), strict=True)

    vector_store.delete_uris.assert_not_awaited()
    vector_store.delete_uri_scope.assert_awaited_once_with(
        _ctx(),
        session_uri,
    )
    assert vector_store.count.await_count == 2
    assert vector_store.count.await_args_list[-1].kwargs == {
        "filter": Or([Eq("uri", session_uri), PathScope("uri", session_uri, depth=-1)]),
        "ctx": _ctx(),
    }


@pytest.mark.asyncio
async def test_strict_delete_confirmation_cannot_mistake_storage_outage_for_absence():
    fs = VikingFS(agfs=SimpleNamespace())
    fs._async_agfs.stat = AsyncMock(side_effect=AGFSNetworkError("endpoint not found"))
    with pytest.raises(AGFSNetworkError, match="endpoint not found"):
        await fs._confirm_fs_scope_cleared("/local/acct/path", "viking://resources/path")


@pytest.mark.asyncio
@pytest.mark.parametrize("trailing_slash", [False, True])
async def test_parent_summary_deletion_preserves_live_descendant_vectors(trailing_slash):
    """Exercise the cloud path-filter boundary, including delete's slash alias."""
    parent = "viking://user/alice/memories/events/batch"
    ctx = _ctx()
    rows = [
        {"id": "abstract", "uri": parent, "account_id": ctx.account_id},
        {"id": "overview", "uri": parent + "/", "account_id": ctx.account_id},
        {"id": "live", "uri": parent + "/live.md", "account_id": ctx.account_id},
        {"id": "nested", "uri": parent + "/nested/live.md", "account_id": ctx.account_id},
        {"id": "prefix", "uri": parent + "-other/live.md", "account_id": ctx.account_id},
        {"id": "foreign", "uri": parent, "account_id": "other"},
    ]
    compiler = object.__new__(LocalCollectionAdapter)

    def selected(expr, row):
        return matches_filter(
            {**row, "uri": compiler._encode_uri_field_value(row["uri"])},
            compiler._compile_filter(expr),
            {"uri": "path", "account_id": "string"},
        )

    async def delete_by_filter(expr):
        rows[:] = [row for row in rows if not selected(expr, row)]

    async def count(filter=None):
        return sum(selected(filter, row) for row in rows if row["account_id"] == ctx.account_id)

    backend = object.__new__(VikingVectorIndexBackend)
    backend._get_backend_for_context = lambda _: SimpleNamespace(
        delete_by_filter=delete_by_filter, count=count
    )
    fs = VikingFS(agfs=SimpleNamespace(), vector_store=backend)
    target = parent + "/" if trailing_slash else parent

    await fs._delete_from_vector_store([target], ctx=ctx)
    await fs._confirm_vector_uris_cleared([target], ctx=ctx)

    assert {row["id"] for row in rows} == {"live", "nested", "prefix", "foreign"}


@pytest.mark.parametrize("field", ["uri", "parent_uri"])
def test_path_membership_matches_only_listed_paths(field):
    compiler = object.__new__(LocalCollectionAdapter)
    paths = ["viking://user/alice/memories/events/a", "viking://user/alice/memories/events/b"]
    compiled = compiler._compile_filter(In(field, paths))
    candidates = [*paths, paths[0] + "/live.md", paths[1] + "-sibling"]
    selected = [
        value
        for value in candidates
        if matches_filter(
            {field: compiler._encode_uri_field_value(value)}, compiled, {field: "path"}
        )
    ]
    assert selected == paths


@pytest.mark.asyncio
@pytest.mark.parametrize("residue", [0, 1])
async def test_verification_retry_never_reissues_vector_or_content_delete(monkeypatch, residue):
    vectors = SimpleNamespace(count=AsyncMock(return_value=residue))
    fs = VikingFS(agfs=SimpleNamespace(), vector_store=vectors)
    fs._async_agfs.rm = AsyncMock()
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(fs, "_delete_from_vector_store", AsyncMock())
    monkeypatch.setattr(fs, "_confirm_fs_scope_cleared", AsyncMock())
    target = "viking://user/alice/sessions/expired"
    kwargs = {
        "ctx": _ctx(),
        "strict": True,
        "preserve_summaries": True,
        "verify_only": True,
        "lease_ref": {"lease_ref": "held-by-cleanup"},
    }
    if residue:
        with pytest.raises(StorageException) as error:
            await fs.rm(target, **kwargs)
        assert error.value.action == "confirm_delete"
        assert isinstance(error.value.__cause__, RuntimeError)
    else:
        await fs.rm(target, **kwargs)
        fs._confirm_fs_scope_cleared.assert_awaited_once()
    vectors.count.assert_awaited_once()
    fs._delete_from_vector_store.assert_not_awaited()
    fs._async_agfs.rm.assert_not_awaited()
