# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.server.identity import Role
from openviking.service.vector_rebuild import VectorRebuildService


class _FakeVikingFS:
    def __init__(self, tree):
        self._tree = tree

    async def list_account_roots(self):
        return [{"name": "acme", "isDir": True}]

    async def exists(self, uri, ctx=None):
        return uri in self._tree

    async def ls(self, uri, **kwargs):
        return list(self._tree.get(uri, []))


class _FakeVikingDBManager:
    def __init__(self):
        self.calls = []

    async def delete_account_data(self, account_id, ctx):
        self.calls.append((account_id, ctx))
        return 7


class _FakeResourceService:
    def __init__(self):
        self.build_calls = []
        self.wait_calls = []

    async def build_index(self, resource_uris, ctx, **kwargs):
        self.build_calls.append((list(resource_uris), ctx))
        return {"status": "success"}

    async def wait_processed(self, timeout=None):
        self.wait_calls.append(timeout)
        return {
            "embedding": {
                "processed": len(self.build_calls),
                "requeue_count": 0,
                "error_count": 0,
                "errors": [],
            }
        }


@pytest.mark.asyncio
async def test_vector_rebuild_service_reindexes_all_scope_directories():
    tree = {
        "viking://resources": [
            {"uri": "viking://resources/project", "isDir": True},
            {"uri": "viking://resources/readme.md", "isDir": False},
        ],
        "viking://resources/project": [
            {"uri": "viking://resources/project/docs", "isDir": True},
            {"uri": "viking://resources/project/spec.md", "isDir": False},
        ],
        "viking://resources/project/docs": [
            {"uri": "viking://resources/project/docs/notes.md", "isDir": False},
        ],
        "viking://user": [
            {"uri": "viking://user/alice", "isDir": True},
        ],
        "viking://user/alice": [
            {"uri": "viking://user/alice/memories", "isDir": True},
        ],
        "viking://user/alice/memories": [
            {"uri": "viking://user/alice/memories/profile.md", "isDir": False},
        ],
    }
    resources = _FakeResourceService()
    vikingdb = _FakeVikingDBManager()
    service = SimpleNamespace(
        viking_fs=_FakeVikingFS(tree),
        vikingdb_manager=vikingdb,
        resources=resources,
    )

    rebuilder = VectorRebuildService(service)
    reports = await rebuilder.rebuild_accounts(wait_timeout=12.5)

    assert [report.account_id for report in reports] == ["acme"]
    assert reports[0].deleted_records == 7
    assert reports[0].indexed_directories == 6
    assert resources.wait_calls == [12.5]
    assert [call[0] for call in resources.build_calls] == [
        ["viking://resources"],
        ["viking://resources/project"],
        ["viking://resources/project/docs"],
        ["viking://user"],
        ["viking://user/alice"],
        ["viking://user/alice/memories"],
    ]
    ctx = vikingdb.calls[0][1]
    assert ctx.role == Role.ROOT
    assert ctx.account_id == "acme"
