# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for orphan index cleanup in VikingFS.rm() (#3064).

When ``rm()`` targets a URI whose backing AGFS path no longer exists, the
filesystem listing is unavailable. The cleanup must therefore discover
descendant URIs from the vector index itself instead of relying on
``_collect_uris`` (which silently returns nothing for a missing path) plus
exact-match deletion, which left every child record behind as an orphan.
"""

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import PathScope
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


def _make_viking_fs(vector_store=None) -> VikingFS:
    """Create a VikingFS instance with a mocked AGFS backend."""
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = AsyncMock()
    fs._async_agfs = fs.agfs
    fs.query_embedder = None
    fs.rerank_config = None
    fs.grep_config = None
    fs.vector_store = vector_store
    fs._encryptor = None
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_test", default=None)
    return fs


def _user_ctx(
    account_id: str = "acct1",
    user_id: str = "alice",
    role: Role = Role.USER,
) -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id=account_id, user_id=user_id),
        role=role,
    )


class TestRmOrphanIndexCleanup:
    """rm() on a non-existent path must clean child records from the index."""

    @pytest.mark.asyncio
    async def test_recursive_rm_cleans_orphan_children_from_index(self) -> None:
        target_uri = "viking://resources/docs"
        orphan_uris = [
            "viking://resources/docs/a.md",
            "viking://resources/docs/",
            "viking://resources/docs/sub/.abstract.md",
        ]
        vector_store = MagicMock()
        vector_store.filter = AsyncMock(return_value=[{"uri": uri} for uri in orphan_uris])
        vector_store.count = AsyncMock(return_value=4)
        fs = _make_viking_fs(vector_store)
        fs.agfs.stat = AsyncMock(side_effect=FileNotFoundError(f"not found: {target_uri}"))
        fs._delete_from_vector_store = AsyncMock()

        result = await fs.rm(f"{target_uri}/", recursive=True, ctx=_user_ctx())

        # Descendants are discovered from the index via a full-depth scope.
        vector_store.filter.assert_awaited_once()
        kwargs = vector_store.filter.await_args.kwargs
        scope = kwargs["filter"]
        assert isinstance(scope, PathScope)
        assert scope.path == target_uri
        assert scope.depth == -1

        # Every discovered orphan plus the target itself gets deleted.
        deleted_uris = fs._delete_from_vector_store.await_args.args[0]
        assert sorted(deleted_uris) == sorted(orphan_uris + [target_uri])
        assert deleted_uris.count(target_uri) == 1
        assert result["estimated_deleted_count"] == 4
        fs.agfs.rm.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_recursive_rm_deletes_target_only(self) -> None:
        target_uri = "viking://resources/docs/notes.md"
        vector_store = MagicMock()
        vector_store.filter = AsyncMock()
        vector_store.count = AsyncMock(return_value=1)
        fs = _make_viking_fs(vector_store)
        fs.agfs.stat = AsyncMock(side_effect=FileNotFoundError("gone"))
        fs._delete_from_vector_store = AsyncMock()

        result = await fs.rm(target_uri, recursive=False, ctx=_user_ctx())

        vector_store.filter.assert_not_awaited()
        deleted_uris = fs._delete_from_vector_store.await_args.args[0]
        assert deleted_uris == [target_uri]
        assert result["estimated_deleted_count"] == 1

    @pytest.mark.asyncio
    async def test_recursive_rm_dedupes_and_skips_records_without_uri(self) -> None:
        target_uri = "viking://user/alice/memories/topics"
        vector_store = MagicMock()
        vector_store.filter = AsyncMock(
            return_value=[
                {"uri": "viking://user/alice/memories/topics/t1"},
                {"uri": "viking://user/alice/memories/topics/t1"},
                {"level": 2},
                {"uri": ""},
                {"uri": target_uri},
            ]
        )
        vector_store.count = AsyncMock(return_value=3)
        fs = _make_viking_fs(vector_store)
        fs.agfs.stat = AsyncMock(side_effect=FileNotFoundError("gone"))
        fs._delete_from_vector_store = AsyncMock()

        result = await fs.rm(target_uri, recursive=True, ctx=_user_ctx())

        deleted_uris = fs._delete_from_vector_store.await_args.args[0]
        assert deleted_uris == [
            "viking://user/alice/memories/topics/t1",
            target_uri,
        ]
        assert result["estimated_deleted_count"] == 3

    @pytest.mark.asyncio
    async def test_orphan_cleanup_without_vector_store_still_succeeds(self) -> None:
        target_uri = "viking://resources/docs"
        fs = _make_viking_fs(vector_store=None)
        fs.agfs.stat = AsyncMock(side_effect=FileNotFoundError("gone"))
        fs._delete_from_vector_store = AsyncMock()

        result = await fs.rm(target_uri, recursive=True, ctx=_user_ctx())

        deleted_uris = fs._delete_from_vector_store.await_args.args[0]
        assert deleted_uris == [target_uri]
        assert result["estimated_deleted_count"] == 0

    @pytest.mark.asyncio
    async def test_index_discovery_failure_falls_back_to_target_only(self) -> None:
        target_uri = "viking://resources/docs"
        vector_store = MagicMock()
        vector_store.filter = AsyncMock(side_effect=RuntimeError("backend down"))
        vector_store.count = AsyncMock(return_value=9)
        fs = _make_viking_fs(vector_store)
        fs.agfs.stat = AsyncMock(side_effect=FileNotFoundError("gone"))
        fs._delete_from_vector_store = AsyncMock()

        result = await fs.rm(target_uri, recursive=True, ctx=_user_ctx())

        deleted_uris = fs._delete_from_vector_store.await_args.args[0]
        assert deleted_uris == [target_uri]
        assert result["estimated_deleted_count"] == 9
