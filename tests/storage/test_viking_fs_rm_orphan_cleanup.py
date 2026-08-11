# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, In, Or, PathScope
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class _MissingAsyncAgfs:
    async def stat(self, path):
        raise NotFoundError(path, "file")


class _RecordingVectorStore:
    def __init__(self, count_result=3):
        self.count_result = count_result
        self.count_calls = []
        self.delete_uris_calls = []
        self.delete_uri_scope_calls = []

    async def count(self, **kwargs):
        self.count_calls.append(kwargs)
        return self.count_result

    async def delete_uris(self, ctx, uris):
        self.delete_uris_calls.append((ctx, uris))

    async def delete_uri_scope(self, ctx, uri, depth=-1):
        self.delete_uri_scope_calls.append((ctx, uri, depth))
        return self.count_result


class _RecordingBackend:
    def __init__(self):
        self.filters = []

    async def delete_by_filter(self, filter):
        self.filters.append(filter)
        return 3


def _ctx():
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


def _missing_path_fs(vector_store):
    fs = VikingFS(agfs=object(), vector_store=vector_store)
    fs._async_agfs = _MissingAsyncAgfs()
    return fs


@pytest.mark.asyncio
async def test_recursive_rm_missing_path_deletes_vector_scope():
    vector_store = _RecordingVectorStore(count_result=3)
    fs = _missing_path_fs(vector_store)

    result = await fs.rm("viking://resources/project", recursive=True, ctx=_ctx())

    assert result == {"estimated_deleted_count": 3}
    assert vector_store.delete_uris_calls == []
    assert vector_store.delete_uri_scope_calls == [(_ctx(), "viking://resources/project", -1)]
    assert vector_store.count_calls[0]["filter"] == PathScope(
        "uri", "viking://resources/project", depth=-1
    )


@pytest.mark.asyncio
async def test_non_recursive_rm_missing_path_preserves_exact_delete():
    vector_store = _RecordingVectorStore(count_result=1)
    fs = _missing_path_fs(vector_store)

    result = await fs.rm("viking://resources/project", recursive=False, ctx=_ctx())

    assert result == {"estimated_deleted_count": 1}
    assert vector_store.delete_uri_scope_calls == []
    assert vector_store.delete_uris_calls == [(_ctx(), ["viking://resources/project"])]


@pytest.mark.asyncio
async def test_delete_uri_scope_uses_account_scoped_path_filter():
    backend = _RecordingBackend()
    vector_backend = object.__new__(VikingVectorIndexBackend)
    vector_backend._get_backend_for_context = lambda _ctx: backend

    result = await vector_backend.delete_uri_scope(_ctx(), "viking://resources/project", depth=-1)

    assert result == 3
    assert backend.filters == [
        And(
            [
                Eq("account_id", "default"),
                Or(
                    [
                        Eq("uri", "viking://resources/project"),
                        In("uri", ["viking://resources/project/"]),
                        PathScope("uri", "viking://resources/project", depth=-1),
                    ]
                ),
            ]
        )
    ]
