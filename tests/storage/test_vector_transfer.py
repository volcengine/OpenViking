# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import And, Contains, Eq, In, Or, PathScope
from openviking.storage.vectordb import engine as vectordb_engine
from openviking.storage.viking_vector_index_backend import (
    VectorTransferRollbackError,
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.exceptions import ConflictError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


def _record(record_id: str, uri: str, **overrides: Any) -> dict[str, Any]:
    return {
        "id": record_id,
        "uri": uri,
        "level": 2,
        "vector": [0.1, 0.2],
        "sparse_vector": {"7": 0.8},
        "content": f"content:{record_id}",
        "created_at": 10,
        "updated_at": 11,
        "active_count": 3,
        "account_id": "acct",
        **overrides,
    }


class _MemoryTransferBackend(VikingVectorIndexBackend):
    """In-memory I/O boundary for exercising the real transfer methods."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = {str(record["id"]): dict(record) for record in records}
        self.upsert_calls = 0
        self.fail_upsert_at: int | None = None
        self.delete_calls = 0
        self.fail_delete_at: int | None = None
        self.partial_delete_count = 0
        self.drop_delete_requests = False
        self.backend_mode = "local"
        self.scroll_filters = []
        self.acl_manager = None

    @property
    def mode(self) -> str:
        return self.backend_mode

    async def scroll(
        self,
        filter=None,
        limit: int = 100,
        cursor: str | None = None,
        output_fields=None,
        *,
        ctx: RequestContext,
    ) -> tuple[list[dict[str, Any]], str | None]:
        del output_fields, ctx
        self.scroll_filters.append(filter)
        offset = int(cursor or 0)
        ordered = [dict(self.records[key]) for key in sorted(self.records)]
        page = ordered[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(ordered) else None
        return page, next_cursor

    async def get(self, ids: list[str], *, ctx: RequestContext) -> list[dict[str, Any]]:
        del ctx
        return [dict(self.records[record_id]) for record_id in ids if record_id in self.records]

    async def upsert(self, data: dict[str, Any], *, ctx: RequestContext, options=None) -> str:
        del ctx, options
        self.upsert_calls += 1
        if self.fail_upsert_at == self.upsert_calls:
            raise RuntimeError("injected vector write failure")
        record = dict(data)
        self.records[str(record["id"])] = record
        return str(record["id"])

    async def upsert_many(
        self, data_list: list[dict[str, Any]], *, ctx: RequestContext
    ) -> list[str]:
        return [await self.upsert(data, ctx=ctx) for data in data_list]

    async def _upsert_many_raw(
        self, data_list: list[dict[str, Any]], *, ctx: RequestContext
    ) -> list[str]:
        return [await self.upsert(data, ctx=ctx) for data in data_list]

    async def delete(self, ids: list[str], *, ctx: RequestContext) -> int:
        del ctx
        self.delete_calls += 1
        if self.drop_delete_requests:
            return 0
        deleted = 0
        for record_id in ids:
            if self.records.pop(record_id, None) is not None:
                deleted += 1
            if self.fail_delete_at == self.delete_calls and deleted >= self.partial_delete_count:
                self.fail_delete_at = None
                raise RuntimeError("injected vector delete failure")
        return deleted

    async def _strict_transfer_count(self, ctx, filter):
        del ctx, filter
        return len(self.records)

    async def _strict_transfer_page(self, ctx, filter, *, limit, cursor, output_fields):
        return await self.scroll(
            filter=filter,
            limit=limit,
            cursor=cursor,
            output_fields=output_fields,
            ctx=ctx,
        )

    async def _strict_transfer_get(self, ctx, ids):
        return await self.get(ids, ctx=ctx)

    async def _strict_transfer_delete(self, ctx, ids):
        return await self.delete(ids, ctx=ctx)


class _TransferAclManager:
    def is_enabled(self, account_id: str) -> bool:
        return account_id == "acct"

    async def materialize_context_records(self, records, ctx):
        del ctx
        return [
            {
                **record,
                "acl_enabled": True,
                "acl_direct_grants": [],
                "acl_inherited_grants": ["1:group:target-readers"],
            }
            for record in records
        ]

    async def materialize_moved_record(self, record, new_uri, ctx):
        del new_uri, ctx
        return {
            "acl_enabled": True,
            "acl_direct_grants": list(record.get("acl_direct_grants") or []),
            "acl_inherited_grants": ["1:group:target-readers"],
        }


class _AclMemoryTransferBackend(_MemoryTransferBackend):
    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__(records)
        self.acl_manager = _TransferAclManager()

    async def upsert_many(
        self, data_list: list[dict[str, Any]], *, ctx: RequestContext
    ) -> list[str]:
        return await VikingVectorIndexBackend.upsert_many(self, data_list, ctx=ctx)


def _records_under(
    backend: _MemoryTransferBackend, uri: str, *, recursive: bool = True
) -> list[dict[str, Any]]:
    return [
        record
        for record in backend.records.values()
        if record["uri"] == uri
        or record["uri"].startswith(uri + "#")
        or (recursive and record["uri"].startswith(uri + "/"))
    ]


@pytest.mark.asyncio
async def test_copy_uri_mapping_scans_real_local_path_records(tmp_path):
    if not getattr(vectordb_engine, "PersistStore", None):
        pytest.skip("local persistent vectordb engine is not available in this environment")

    source = "viking://resources/src.md"
    target = "viking://resources/dst.md"
    backend = VikingVectorIndexBackend(
        config=VectorDBBackendConfig(
            backend="local",
            name="context",
            dimension=4,
            path=str(tmp_path),
        )
    )
    try:
        assert await backend.create_collection(
            "context", CollectionSchemas.context_collection("context", 4)
        )
        assert (
            await backend.upsert(
                _record(
                    "source-file",
                    source,
                    vector=[0.1, 0.2, 0.3, 0.4],
                    created_at="2026-08-20T00:00:00Z",
                    updated_at="2026-08-20T00:00:00Z",
                ),
                ctx=_ctx(),
            )
            == "source-file"
        )

        result = await backend.copy_uri_mapping(_ctx(), source, target, recursive=False)

        assert result.scanned == 1
        copied = await backend.get_context_by_uri(target, ctx=_ctx())
        assert [record["uri"] for record in copied] == [target]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_copy_uri_mapping_keeps_source_and_copies_all_pages():
    source = "viking://resources/src"
    records = [_record("source-root", source)] + [
        _record(f"source-{index:03d}", f"{source}/file-{index:03d}.md") for index in range(204)
    ]
    backend = _MemoryTransferBackend(records)

    result = await backend.copy_uri_mapping(
        _ctx(), source, "viking://resources/dst", recursive=True
    )

    assert result.scanned == 205
    assert result.written == 205
    assert result.batches == 3
    assert len(_records_under(backend, source)) == 205
    assert len(_records_under(backend, "viking://resources/dst")) == 205


@pytest.mark.asyncio
async def test_get_l2_abstracts_by_uris_uses_strict_batched_lookup():
    backend = _MemoryTransferBackend([])
    backend._strict_transfer_page = AsyncMock(
        side_effect=[
            (
                [
                    {
                        "uri": "viking://resources/a.md",
                        "abstract": "summary-a",
                        "updated_at": 1,
                    },
                    {
                        "uri": "viking://resources/b.md",
                        "abstract": "",
                        "updated_at": 1,
                    },
                ],
                None,
            )
        ]
    )

    result = await backend.get_l2_abstracts_by_uris(
        ["viking://resources/a.md", "viking://resources/b.md"],
        ctx=_ctx(),
    )

    assert result == {"viking://resources/a.md": "summary-a"}
    call = backend._strict_transfer_page.await_args
    assert call.kwargs["output_fields"] == ["uri", "abstract", "updated_at"]
    assert call.args[1] == And(
        [
            In("uri", ["viking://resources/a.md", "viking://resources/b.md"]),
            Eq("level", 2),
        ]
    )


@pytest.mark.asyncio
async def test_strict_scroll_propagates_real_adapter_query_failure():
    backend = _SingleAccountBackend.__new__(_SingleAccountBackend)
    backend._bound_account_id = "acct"
    backend._async_adapter = SimpleNamespace(
        call=AsyncMock(side_effect=RuntimeError("injected query failure"))
    )

    with pytest.raises(RuntimeError, match="injected query failure"):
        await backend.strict_scroll(limit=100, output_fields=["id", "uri"])


@pytest.mark.asyncio
async def test_strict_delete_removes_existing_subset_when_attempted_ids_include_missing():
    adapter_call = AsyncMock(
        side_effect=[
            [{"id": "written", "account_id": "acct"}],
            1,
        ]
    )
    backend = _SingleAccountBackend.__new__(_SingleAccountBackend)
    backend._bound_account_id = "acct"
    backend._async_adapter = SimpleNamespace(call=adapter_call)

    deleted = await backend.strict_delete(["written", "never-written"])

    assert deleted == 1
    assert adapter_call.await_args_list[1].args == ("delete",)
    assert adapter_call.await_args_list[1].kwargs == {"ids": ["written"]}


@pytest.mark.asyncio
async def test_transfer_scan_fails_closed_on_repeated_record_page():
    backend = _MemoryTransferBackend(
        [
            _record("source-a", "viking://resources/src/a.md"),
            _record("source-b", "viking://resources/src/b.md"),
        ]
    )

    async def repeated_page(*_args, **_kwargs):
        record = dict(backend.records["source-a"])
        return [record], "1"

    backend._strict_transfer_page = repeated_page

    with pytest.raises(RuntimeError, match="duplicate vector record"):
        await backend.copy_uri_mapping(
            _ctx(), "viking://resources/src", "viking://resources/dst", recursive=True
        )


@pytest.mark.asyncio
async def test_copy_uri_mapping_preserves_dense_sparse_and_chunk_payloads():
    source = "viking://resources/src.md"
    backend = _MemoryTransferBackend(
        [
            _record("source-file", source, sparse_vector={}),
            _record("source-chunk", f"{source}#chunk_0001", vector=[]),
            _record("outside", "viking://resources/other.md"),
        ]
    )

    result = await backend.copy_uri_mapping(
        _ctx(), source, "viking://resources/dst.md", recursive=False
    )

    copied = _records_under(backend, "viking://resources/dst.md")
    assert result.written == 2
    assert {record["uri"] for record in copied} == {
        "viking://resources/dst.md",
        "viking://resources/dst.md#chunk_0001",
    }
    assert {tuple(record["vector"]) for record in copied} == {(), (0.1, 0.2)}
    assert {tuple(record["sparse_vector"].items()) for record in copied} == {
        (),
        (("7", 0.8),),
    }


@pytest.mark.asyncio
async def test_volcengine_transfer_scope_avoids_unsupported_contains_filter():
    source = "viking://resources/src.md"
    backend = _MemoryTransferBackend([_record("source", source)])
    backend.backend_mode = "volcengine"

    await backend.copy_uri_mapping(_ctx(), source, "viking://resources/dst.md", recursive=False)

    assert backend.scroll_filters
    for filter_expr in backend.scroll_filters:
        assert isinstance(filter_expr, And)
        scopes = next(cond for cond in filter_expr.conds if isinstance(cond, Or)).conds
        assert not any(isinstance(scope, Contains) for scope in scopes)
        assert any(
            isinstance(scope, PathScope) and scope.path == "viking://resources" for scope in scopes
        )


@pytest.mark.asyncio
async def test_copy_uri_mapping_rejects_preexisting_target_before_writing():
    backend = _MemoryTransferBackend(
        [
            _record("source", "viking://resources/src.md"),
            _record("target", "viking://resources/dst.md#chunk_0001"),
        ]
    )

    with pytest.raises(ConflictError, match="target vector scope already exists"):
        await backend.copy_uri_mapping(
            _ctx(),
            "viking://resources/src.md",
            "viking://resources/dst.md",
            recursive=False,
        )

    assert backend.upsert_calls == 0
    assert len(_records_under(backend, "viking://resources/src.md")) == 1


@pytest.mark.asyncio
async def test_copy_uri_mapping_removes_partial_target_when_write_fails():
    source = "viking://resources/src"
    backend = _MemoryTransferBackend(
        [
            _record("source-root", source),
            _record("source-a", f"{source}/a.md"),
            _record("source-b", f"{source}/b.md"),
        ]
    )
    backend.fail_upsert_at = 3

    with pytest.raises(RuntimeError, match="injected vector write failure"):
        await backend.copy_uri_mapping(_ctx(), source, "viking://resources/dst", recursive=True)

    assert len(_records_under(backend, source)) == 3
    assert _records_under(backend, "viking://resources/dst") == []


@pytest.mark.asyncio
async def test_copy_uri_mapping_reports_actual_residual_after_cleanup_failure():
    source = "viking://resources/src"
    backend = _MemoryTransferBackend(
        [
            _record("source-root", source),
            _record("source-a", f"{source}/a.md"),
            _record("source-b", f"{source}/b.md"),
        ]
    )
    backend.fail_upsert_at = 3
    backend.fail_delete_at = 1
    backend.partial_delete_count = 1

    with pytest.raises(VectorTransferRollbackError) as exc_info:
        await backend.copy_uri_mapping(_ctx(), source, "viking://resources/dst", recursive=True)

    assert exc_info.value.phase == "copy_target_cleanup"
    assert exc_info.value.residual_count == 1
    assert len(_records_under(backend, "viking://resources/dst")) == 1


@pytest.mark.asyncio
async def test_copy_uri_mapping_reports_rollback_when_delete_returns_zero():
    source = "viking://resources/src"
    backend = _MemoryTransferBackend(
        [
            _record("source-root", source),
            _record("source-a", f"{source}/a.md"),
            _record("source-b", f"{source}/b.md"),
        ]
    )
    backend.fail_upsert_at = 3
    backend.drop_delete_requests = True

    with pytest.raises(VectorTransferRollbackError) as exc_info:
        await backend.copy_uri_mapping(_ctx(), source, "viking://resources/dst", recursive=True)

    assert exc_info.value.phase == "copy_target_cleanup"
    assert exc_info.value.residual_count == 2


@pytest.mark.asyncio
async def test_update_uri_mapping_deletes_source_only_after_targets_exist():
    source = "viking://resources/src"
    backend = _MemoryTransferBackend(
        [
            _record("source-root", source, created_at=5, updated_at=6, active_count=7),
            _record("source-a", f"{source}/a.md"),
            _record("source-b", f"{source}/b.md"),
        ]
    )

    result = await backend.update_uri_mapping(
        _ctx(), source, "viking://resources/dst", recursive=True
    )

    assert result.scanned == 3
    assert result.written == 3
    assert result.deleted == 3
    assert _records_under(backend, source) == []
    targets = _records_under(backend, "viking://resources/dst")
    assert len(targets) == 3
    root = next(record for record in targets if record["uri"] == "viking://resources/dst")
    assert (root["created_at"], root["updated_at"], root["active_count"]) == (5, 6, 7)


@pytest.mark.asyncio
async def test_update_uri_mapping_preserves_source_direct_acl_on_target():
    source = "viking://resources/src.md"
    target = "viking://resources/dst.md"
    backend = _AclMemoryTransferBackend(
        [
            _record(
                "source",
                source,
                acl_enabled=True,
                acl_direct_grants=["7:user:alice"],
                acl_inherited_grants=["1:group:source-readers"],
            )
        ]
    )

    await backend.update_uri_mapping(_ctx(), source, target, recursive=False)

    moved = _records_under(backend, target, recursive=False)
    assert len(moved) == 1
    assert moved[0]["acl_direct_grants"] == ["7:user:alice"]
    assert moved[0]["acl_inherited_grants"] == ["1:group:target-readers"]


@pytest.mark.asyncio
async def test_update_uri_mapping_restores_source_and_removes_target_after_partial_delete():
    source = "viking://resources/src"
    backend = _MemoryTransferBackend(
        [
            _record("source-root", source),
            _record("source-a", f"{source}/a.md"),
            _record("source-b", f"{source}/b.md"),
        ]
    )
    backend.fail_delete_at = 1
    backend.partial_delete_count = 1

    with pytest.raises(RuntimeError, match="injected vector delete failure"):
        await backend.update_uri_mapping(_ctx(), source, "viking://resources/dst", recursive=True)

    assert len(_records_under(backend, source)) == 3
    assert _records_under(backend, "viking://resources/dst") == []


@pytest.mark.asyncio
async def test_update_uri_mapping_rollback_restores_source_direct_acl():
    source = "viking://resources/src"
    target = "viking://resources/dst"
    backend = _AclMemoryTransferBackend(
        [
            _record(
                "source-root",
                source,
                acl_enabled=True,
                acl_direct_grants=["7:user:alice"],
                acl_inherited_grants=["1:group:source-readers"],
            ),
            _record(
                "source-child",
                f"{source}/child.md",
                acl_enabled=True,
                acl_direct_grants=["3:user:bob"],
                acl_inherited_grants=["7:user:alice"],
            ),
        ]
    )
    backend.fail_delete_at = 1
    backend.partial_delete_count = 1

    with pytest.raises(RuntimeError, match="injected vector delete failure"):
        await backend.update_uri_mapping(_ctx(), source, target, recursive=True)

    restored = sorted(_records_under(backend, source), key=lambda item: item["uri"])
    assert [record["acl_direct_grants"] for record in restored] == [
        ["7:user:alice"],
        ["3:user:bob"],
    ]
    assert _records_under(backend, target) == []


@pytest.mark.asyncio
async def test_update_uri_mapping_returns_empty_result_when_source_has_no_vectors():
    backend = _MemoryTransferBackend([_record("outside", "viking://resources/other.md")])

    result = await backend.update_uri_mapping(
        _ctx(),
        "viking://resources/missing.md",
        "viking://resources/dst.md",
        recursive=False,
    )

    assert result.scanned == 0
    assert result.written == 0
    assert result.deleted == 0
