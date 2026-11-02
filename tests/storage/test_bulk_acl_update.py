# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import ACL_CONTEXT_FIELDS, AclManager, AclMode
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import PathScope
from openviking.storage.viking_vector_index_backend import (
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _patch(record_id="doc-1", **fields):
    return {
        "id": record_id,
        "account_id": "acct",
        "acl_mode": "restricted",
        "acl_direct_grants": ["1:user:reader"],
        "acl_inherited_grants": [],
        **fields,
    }


class _Adapter:
    mode = "local"
    USE_CONTENT_FIELD = False

    def __init__(self):
        self.calls = []
        self.threads = []
        self.fields = {"id", "account_id", *ACL_CONTEXT_FIELDS}

    def get_collection(self):
        self.threads.append(threading.get_ident())
        return SimpleNamespace(
            get_meta_data=lambda: {"Fields": [{"FieldName": field} for field in self.fields]}
        )

    def update_data(self, records):
        self.threads.append(threading.get_ident())
        self.calls.append(deepcopy(records))
        return [record["id"] for record in records]


def _backend(adapter):
    return _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=4),
        bound_account_id="acct",
        shared_adapter=adapter,
    )


async def test_acl_batch_updates_only_permissions_in_worker_thread():
    adapter = _Adapter()
    records = [_patch(), _patch("doc-2", acl_direct_grants=[])]
    before = deepcopy(records)

    assert await _backend(adapter).update_acl_many(records) == ["doc-1", "doc-2"]

    assert records == before
    assert adapter.calls == [
        [{k: v for k, v in row.items() if k != "account_id"} for row in before]
    ]
    assert adapter.calls[0][1]["acl_direct_grants"] == []
    assert all(thread != threading.get_ident() for thread in adapter.threads)


@pytest.mark.parametrize(
    ("invalid", "error"),
    [
        (_patch(""), ValueError),
        (_patch(42), ValueError),
        (_patch("doc-2", account_id="other"), PermissionError),
        (_patch("doc-2", vector=[1, 0, 0, 0]), ValueError),
        (_patch("doc-2", acl_mode=None), ValueError),
        (_patch("doc-2", acl_mode="invalid"), RuntimeError),
        (_patch("doc-2", acl_direct_grants=["invalid"]), RuntimeError),
        ({"id": "doc-2", "account_id": "acct"}, ValueError),
        (_patch(), ValueError),  # Duplicate ID, not a second write to the same row.
    ],
)
async def test_acl_batch_validates_every_record_before_writing(invalid, error):
    adapter = _Adapter()
    with pytest.raises(error):
        await _backend(adapter).update_acl_many([_patch(), invalid])
    assert adapter.calls == []


async def test_acl_batch_rejects_schema_that_would_drop_permissions():
    adapter = _Adapter()
    adapter.fields.remove("acl_inherited_grants")
    with pytest.raises(RuntimeError, match="missing ACL update fields"):
        await _backend(adapter).update_acl_many([_patch()])
    assert adapter.calls == []


async def test_empty_acl_batch_does_not_touch_adapter():
    adapter = _Adapter()
    assert await _backend(adapter).update_acl_many([]) == []
    assert adapter.calls == adapter.threads == []


@pytest.mark.parametrize("returned_ids", [[], ["doc-1"], ["doc-2", "doc-1"], ["doc-1", "doc-1"]])
async def test_acl_batch_rejects_incomplete_or_incorrect_update_result(returned_ids):
    adapter = _Adapter()
    adapter.update_data = lambda records: returned_ids
    with pytest.raises(RuntimeError, match="do not match the input count and order"):
        await _backend(adapter).update_acl_many([_patch(), _patch("doc-2")])


async def test_acl_batch_propagates_missing_record_without_upsert():
    adapter = _Adapter()

    def missing(records):
        raise ValueError("record not found")

    adapter.update_data = missing
    with pytest.raises(ValueError, match="record not found"):
        await _backend(adapter).update_acl_many([_patch()])


async def test_raw_acl_update_routes_to_bound_account_without_public_update():
    backend = object.__new__(VikingVectorIndexBackend)
    account_backend = SimpleNamespace(update_acl_many=AsyncMock(return_value=["doc-1"]))
    backend._get_backend_for_context = AsyncMock(return_value=account_backend)
    ctx = RequestContext(user=UserIdentifier("acct", "admin"), role=Role.ADMIN)
    records = [_patch()]

    assert await backend._update_acl_many_raw(records, ctx=ctx) == ["doc-1"]
    backend._get_backend_for_context.assert_awaited_once_with(ctx)
    account_backend.update_acl_many.assert_awaited_once_with(records)


async def test_acl_update_preserves_records_and_revokes_indexed_access(
    vector_backend_factory, tmp_path, monkeypatch
):
    backend = vector_backend_factory(
        config=VectorDBBackendConfig(
            backend="local", name="context", dimension=4, path=str(tmp_path)
        )
    )
    admin = RequestContext(user=UserIdentifier("acct", "admin"), role=Role.ADMIN)
    other = RequestContext(user=UserIdentifier("other", "admin"), role=Role.ADMIN)
    reader = RequestContext(user=UserIdentifier("acct", "reader"), role=Role.USER)
    root = "viking://resources/project"
    scope = PathScope("uri", root, depth=-1)
    try:
        assert await backend.create_collection(
            "context", CollectionSchemas.context_collection("context", 4)
        )
        records = [
            {
                "id": record_id,
                "uri": uri,
                "account_id": "acct",
                "context_type": "resource",
                "level": level,
                "name": "keep-name",
                "abstract": "keep-abstract",
                "search_tags": ["team=search"],
                "vector": [0.1, 0.2, 0.3, 0.4],
                "acl_mode": "inherit",
                "acl_direct_grants": [],
                "acl_inherited_grants": ["7:user:*"],
            }
            for record_id, uri, level in (
                ("root-l0", root, 0),
                ("root-l1", root, 1),
                ("child-l2", root + "/file.md", 2),
            )
        ]
        ids = await backend._upsert_many_raw(records, ctx=admin)
        await backend._upsert_many_raw(
            [{**records[0], "id": "other-root", "account_id": "other"}], ctx=other
        )
        before = {r["id"]: r for r in await backend.get_strict(ids, ctx=admin)}
        other_before = await backend.get_strict(["other-root"], ctx=other)
        manager = backend.acl_manager = AclManager(
            backend,
            SimpleNamespace(get_account=AsyncMock(return_value=SimpleNamespace(enabled=True))),
        )
        adapter = (await backend._get_backend_for_context(admin))._adapter
        original_update = adapter.update_data
        updates = []

        def capture_update(data):
            updates.append(deepcopy(data))
            return original_update(data)

        monkeypatch.setattr(adapter, "update_data", capture_update)
        await manager.set_acl(
            root,
            [{"principal": "user:reader", "level": "read"}],
            admin,
            acl_mode=AclMode.RESTRICTED,
        )
        visible = await backend.search_by_random(
            filter=scope, output_fields=["id"], limit=10, ctx=reader
        )
        assert {row["id"] for row in visible} == set(ids)

        # A subsequent insert must still upsert and materialize the current ACL.
        await backend.upsert({**records[2], "id": "new-child", "uri": root + "/new.md"}, ctx=admin)
        created = (await backend.get_strict(["new-child"], ctx=admin))[0]
        assert created["acl_inherited_grants"] == ["1:user:reader"]

        await manager.set_acl(root, [], admin, acl_mode=AclMode.RESTRICTED)
        assert (
            await backend.search_by_random(filter=scope, output_fields=["id"], limit=10, ctx=reader)
            == []
        )
        for batch in updates:
            assert all(set(row) == {"id", *ACL_CONTEXT_FIELDS} for row in batch)
        after = {r["id"]: r for r in await backend.get_strict(ids, ctx=admin)}
        for record_id in ids:
            assert {k: v for k, v in after[record_id].items() if k not in ACL_CONTEXT_FIELDS} == {
                k: v for k, v in before[record_id].items() if k not in ACL_CONTEXT_FIELDS
            }
        assert await backend.get_strict(["other-root"], ctx=other) == other_before

        with pytest.raises(ValueError, match="not found"):
            await backend._update_acl_many_raw([_patch("missing")], ctx=admin)
        assert await backend.get_strict(["missing"], ctx=admin) == []
    finally:
        await backend.close()
