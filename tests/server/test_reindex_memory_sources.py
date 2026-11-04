# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Physical orphan IDs, exact file chunks and confirmed memory sidecar sources."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import openviking.service.reindex_executor as module
from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import (
    ReindexExecutor,
    _ReindexCounters,
    _ReindexRunContext,
)
from openviking.storage.abstract_overview import (
    AbstractOverviewFormatError,
    body_for_preview,
    render_abstract_overview,
)
from openviking.storage.errors import LockError
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier

ROOT = "viking://user/bob/memories/events"


class MemoryFS:
    def __init__(self, sources):
        self.sources = sources
        self.directories = {ROOT}
        self.reads = []
        self._async_agfs = SimpleNamespace(
            pathlock_acquire_exact_batch=AsyncMock(return_value={"lease_id": "chunks"}),
            pathlock_release=AsyncMock(),
        )

    def _uri_to_path(self, uri, **kwargs):
        return "/local/memory_sources/" + uri[len("viking://") :]

    async def stat(self, uri, **kwargs):
        if isinstance(self.sources.get(uri), Exception):
            raise self.sources[uri]
        if uri not in self.sources and uri not in self.directories:
            raise NotFoundError(uri)
        return {"isDir": uri in self.directories}

    async def exists(self, uri, **kwargs):
        return uri in self.sources or uri in self.directories

    async def read_file(self, uri, **kwargs):
        self.reads.append(uri)
        if uri not in self.sources:
            raise NotFoundError(uri)
        value = self.sources[uri]
        if isinstance(value, BaseException):
            raise value
        return value

    async def abstract(self, uri, **kwargs):
        try:
            return body_for_preview(await self.read_file(uri + "/.abstract.md"))
        except (NotFoundError, AbstractOverviewFormatError):
            return ""

    async def overview(self, uri, **kwargs):
        try:
            return body_for_preview(await self.read_file(uri + "/.overview.md"))
        except (NotFoundError, AbstractOverviewFormatError):
            return ""


def setup_memory(monkeypatch, sources, inventory):
    fs = MemoryFS(sources)
    db = SimpleNamespace(
        strict_delete=AsyncMock(side_effect=lambda ids, **kwargs: len(ids)),
        get_reindex_inventory_under_uri=AsyncMock(return_value=inventory),
    )
    monkeypatch.setattr(module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(module, "get_service", lambda: SimpleNamespace(vikingdb_manager=db))
    executor = ReindexExecutor()
    executor._upsert_context = AsyncMock()
    executor._effective_file_vectorization_concurrency = lambda: 2
    ctx = RequestContext(user=UserIdentifier("memory_sources", "bob"), role=Role.ADMIN)
    counters = _ReindexCounters()
    return executor, fs, db, ctx, counters


def records(*items):
    return {key: {"id": key, "uri": uri, "level": level} for key, uri, level in items}


async def scan(executor, ctx, counters, sources, inventory):
    entries = [
        {"uri": uri, "isDir": False}
        for uri in sources
        if not uri.rsplit("/", 1)[-1].startswith(".")
    ]
    await executor._reindex_memory_vectors(
        uri=ROOT, ctx=ctx, counters=counters, formal_entries=entries, vector_inventory=inventory
    )


@pytest.mark.asyncio
async def test_deletes_all_physical_ids_of_missing_files_chunks_and_directories(monkeypatch):
    sources = {ROOT + "/live.md": "Live memory"}
    inventory = records(
        ("live-a", ROOT + "/live.md", 2),
        ("live-b", ROOT + "/live.md", 2),
        ("gone-a", ROOT + "/gone.md", 2),
        ("gone-b", ROOT + "/gone.md", 2),
        ("chunk-a", ROOT + "/gone.md#chunk_0000", 2),
        ("chunk-b", ROOT + "/gone.md#chunk_0000", 2),
        ("dir-a", ROOT + "/gone-dir", 0),
        ("dir-b", ROOT + "/gone-dir", 0),
    )
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    await scan(executor, ctx, counters, sources, inventory)
    assert set(db.strict_delete.await_args.args[0]) == set(inventory) - {"live-a", "live-b"}
    assert counters.deleted_records == 6


@pytest.mark.asyncio
async def test_exact_file_includes_its_chunks_and_preserves_literal_sibling(monkeypatch):
    uri = ROOT + "/a.md"
    literal = uri + "#chunk_0007"
    sources = {uri: "Short memory", literal: "A real file"}
    inventory = records(
        ("base", uri, 2),
        ("stale-a", uri + "#chunk_0042", 2),
        ("stale-b", uri + "#chunk_0042", 2),
        ("literal", literal, 2),
    )
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    await executor._reindex_memory(
        uri=uri, mode="vectors_only", run=_ReindexRunContext(ctx=ctx, counters=counters)
    )
    assert (
        db.get_reindex_inventory_under_uri.await_args.kwargs.get("include_virtual_chunks") is True
    )
    assert set(db.strict_delete.await_args.args[0]) == {"stale-a", "stale-b"}
    assert {call.kwargs["uri"] for call in executor._upsert_context.await_args_list} == {uri}


@pytest.mark.asyncio
async def test_exact_file_chunk_probe_failure_prevents_mutation(monkeypatch):
    uri = ROOT + "/a.md"
    inventory = records(("base", uri, 2), ("chunk", uri + "#chunk_0042", 2))
    sources = {uri: "Short memory", uri + "#chunk_0042": RuntimeError("stat unavailable")}
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    with pytest.raises(RuntimeError, match="stat unavailable"):
        await executor._reindex_memory_vectors(
            uri=uri,
            ctx=ctx,
            counters=counters,
            root_is_dir=False,
            formal_entries=[],
            vector_inventory=inventory,
        )
    executor._upsert_context.assert_not_awaited()
    db.strict_delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("exact", [False, True])
async def test_long_memory_retires_all_virtual_chunks_after_base_rebuild(monkeypatch, exact):
    uri = ROOT + "/long.md"
    sources = {uri: "Long memory body. " * 1000}
    inventory = records(
        ("base", uri, 2),
        ("chunk-a", uri + "#chunk_0000", 2),
        ("chunk-b", uri + "#chunk_0000", 2),
        ("chunk-c", uri + "#chunk_0001", 2),
    )
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    if exact:
        await executor._reindex_memory_vectors(
            uri=uri,
            ctx=ctx,
            counters=counters,
            root_is_dir=False,
            formal_entries=[],
            vector_inventory=inventory,
        )
    else:
        await scan(executor, ctx, counters, sources, inventory)
    assert set(db.strict_delete.await_args.args[0]) == {"chunk-a", "chunk-b", "chunk-c"}
    assert [call.kwargs["uri"] for call in executor._upsert_context.await_args_list] == [uri]


@pytest.mark.asyncio
async def test_base_enqueue_failure_still_retires_unused_legacy_chunks(monkeypatch):
    uri = ROOT + "/a.md"
    sources = {uri: "Current memory"}
    inventory = records(
        ("base", uri, 2), ("a", uri + "#chunk_0000", 2), ("b", uri + "#chunk_0000", 2)
    )
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    executor._upsert_context.side_effect = RuntimeError("embedding enqueue unavailable")
    await scan(executor, ctx, counters, sources, inventory)
    assert set(db.strict_delete.await_args.args[0]) == {"a", "b"}
    assert counters.failed_records == 1


@pytest.mark.asyncio
async def test_unavailable_body_with_abstract_fallback_retires_legacy_chunks(monkeypatch):
    uri = ROOT + "/a.md"
    inventory = records(("base", uri, 2), ("chunk", uri + "#chunk_0000", 2))
    inventory["base"]["abstract"] = "Existing summary"
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, {}, inventory)
    executor._best_memory_file_summary = AsyncMock(
        side_effect=AssertionError("inventory abstract should bypass overview fallback")
    )
    await executor._reindex_memory_vectors(
        uri=uri,
        ctx=ctx,
        counters=counters,
        root_is_dir=False,
        formal_entries=[],
        vector_inventory=inventory,
    )
    assert db.strict_delete.await_args.args[0] == ["chunk"]
    assert counters.rebuilt_records == 1
    assert counters.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("enqueue failure"), LockError("lease lost")])
async def test_directory_enqueue_failure_preserves_old_source_vectors(monkeypatch, error):
    sources = {ROOT + "/.abstract.md": "Valid summary"}
    inventory = records(("old-a", ROOT, 0), ("old-b", ROOT, 0))
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    executor._upsert_context.side_effect = error
    if isinstance(error, LockError):
        with pytest.raises(LockError):
            await scan(executor, ctx, counters, sources, inventory)
    else:
        await scan(executor, ctx, counters, sources, inventory)
        assert counters.failed_records == 1
    db.strict_delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("level,sidecar", [(0, ".abstract.md"), (1, ".overview.md")])
@pytest.mark.parametrize("source", [None, "", "  \n"])
async def test_missing_or_empty_sidecar_deletes_only_its_level(monkeypatch, level, sidecar, source):
    other = ".overview.md" if level == 0 else ".abstract.md"
    sources = {ROOT + "/live.md": "Live", ROOT + "/" + other: "Valid summary"}
    if source is not None:
        sources[ROOT + "/" + sidecar] = source
    inventory = records(("l0", ROOT, 0), ("l1", ROOT, 1), ("l2", ROOT + "/live.md", 2))
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    await scan(executor, ctx, counters, sources, inventory)
    assert set(db.strict_delete.await_args.args[0]) == {"l" + str(level)}
    assert counters.failed_records == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("level,sidecar", [(0, ".abstract.md"), (1, ".overview.md")])
@pytest.mark.parametrize("source", [RuntimeError("S3 timeout"), "---\ndirectory: [\n---\nBad"])
async def test_sidecar_read_or_format_failure_preserves_old_level_and_reports(
    monkeypatch, level, sidecar, source
):
    sources = {ROOT + "/live.md": "Live", ROOT + "/" + sidecar: source}
    inventory = records(("old-a", ROOT, level), ("old-b", ROOT, level))
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    await scan(executor, ctx, counters, sources, inventory)
    db.strict_delete.assert_not_awaited()
    assert counters.failed_records == 1
    assert any(sidecar in warning for warning in counters.warnings)
    assert {call.kwargs["uri"] for call in executor._upsert_context.await_args_list} == {
        ROOT + "/live.md"
    }


@pytest.mark.asyncio
async def test_sidecar_okf_is_parsed_without_embedding_metadata(monkeypatch):
    sources = {
        ROOT + "/.abstract.md": render_abstract_overview(0, ROOT, "Visible abstract"),
        ROOT + "/.overview.md": render_abstract_overview(1, ROOT, "Visible overview"),
    }
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, {})
    await scan(executor, ctx, counters, sources, {})
    assert [call.kwargs["abstract"] for call in executor._upsert_context.await_args_list] == [
        "Visible abstract",
        "Visible overview",
    ]
    assert fs.reads.count(ROOT + "/.abstract.md") == 1
    assert fs.reads.count(ROOT + "/.overview.md") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError(), LockError("lock lost")])
async def test_sidecar_fatal_errors_propagate_without_cleanup(monkeypatch, error):
    sources = {ROOT + "/.abstract.md": error}
    inventory = records(("old", ROOT, 0))
    executor, fs, db, ctx, counters = setup_memory(monkeypatch, sources, inventory)
    with pytest.raises(type(error)):
        await scan(executor, ctx, counters, sources, inventory)
    db.strict_delete.assert_not_awaited()
