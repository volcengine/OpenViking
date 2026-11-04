from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSResourceExhaustedError
from openviking.server.identity import RequestContext, Role
from openviking.service.compile_graph import (
    ArtifactGraphError,
    ArtifactGraphTooLargeError,
    ArtifactRelationGraph,
)
from openviking.service.compile_targets import CompileStateStore
from openviking.storage.internal_names import is_storage_internal_name
from openviking_cli.session.user_id import UserIdentifier


class _CacheAgfs:
    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes, dict[str, str]]] = []

    def ensure_parent_dirs(self, _path: str, *, ctx: dict[str, str]) -> dict[str, str]:
        return {"message": "created"}

    def write(self, path: str, data: bytes, *, ctx: dict[str, str]) -> str:
        self.writes.append((path, data, ctx))
        return "written"


class _GraphAgfs:
    def __init__(self, entries: list[dict[str, Any]], gitignore: bytes = b"") -> None:
        self.entries = entries
        self.gitignore = gitignore

    async def tree_directory(self, _path: str, *, offset: int, node_limit: int, **_: Any):
        return self.entries[offset : offset + node_limit]

    async def read(self, _path: str):
        return self.gitignore


class _GraphFs:
    def __init__(
        self,
        entries: list[dict[str, Any]],
        contents: dict[str, bytes],
        *,
        gitignore: bytes = b"",
    ) -> None:
        self._async_agfs = _GraphAgfs(entries, gitignore)
        self.contents = contents
        self.reads: list[str] = []

    @staticmethod
    def _uri_to_path(uri: str, ctx: RequestContext) -> str:
        return f"/local/{ctx.account_id}/{uri.removeprefix('viking://')}"

    @staticmethod
    def _path_to_uri(path: str, ctx: RequestContext) -> str:
        return f"viking://{path.removeprefix(f'/local/{ctx.account_id}/')}"

    @staticmethod
    def _gitignore_agfs_path(ctx: RequestContext) -> str:
        return f"/local/{ctx.account_id}/.ovgitignore"

    @staticmethod
    def _is_tree_entry_visible(entry, _base_path, _ctx, *, acl_enabled):
        del acl_enabled
        name = entry["path"].rstrip("/").rsplit("/", 1)[-1]
        return not is_storage_internal_name(name)

    async def read_file_bytes(self, uri: str, ctx: RequestContext) -> bytes:
        del ctx
        self.reads.append(uri)
        return self.contents[uri]

    async def _can_access_many(self, uris, _ctx, *, action):
        del action
        return {uri: not uri.endswith("secret.md") for uri in uris}


class _GraphStore:
    def __init__(
        self,
        record: dict[str, Any],
        cache: dict[str, Any] | None = None,
        sources: dict[str, Any] | None = None,
    ) -> None:
        self.record = record
        self.cache = cache
        self.sources = sources
        self.state = None
        self.published = None
        self.published_sources = None
        self.write_order: list[str] = []
        self.cache_reads = 0

    async def get(self, _account_id: str, _target_id: str):
        return self.record

    async def read_artifact_relations(self, _account_id: str, _target_id: str):
        self.cache_reads += 1
        return self.cache

    async def write_artifact_relations(
        self, _account_id: str, _target_id: str, value, _lease=None
    ):
        self.published = value
        self.cache = value
        self.write_order.append("graph")

    async def read_artifact_relations_state(self, _account_id: str, _target_id: str):
        return self.state

    async def write_artifact_relations_state(
        self, _account_id: str, _target_id: str, value, _lease=None
    ):
        self.state = value
        self.write_order.append(f"state:{value['status']}")

    async def read_relation_sources(self, _account_id: str, _target_id: str):
        return self.sources

    async def write_relation_sources(
        self, _account_id: str, _target_id: str, value, _lease=None
    ):
        self.published_sources = value
        self.sources = value
        self.write_order.append("sources")

    @asynccontextmanager
    async def lock_artifact_graph(self, _account_id: str, _target_id: str):
        yield {"lease_ref": "graph"}

    @asynccontextmanager
    async def edit(self, _account_id: str, _target_id: str):
        yield self.record


class _SnapshotGraphFs(_GraphFs):
    def __init__(
        self,
        diffs: dict[tuple[str | None, str, tuple[str, ...]], list[dict[str, str]]],
        blobs: dict[tuple[str, str], dict[str, Any]],
    ) -> None:
        super().__init__([], {})
        self.diffs = diffs
        self.blobs = blobs
        self.diff_calls: list[tuple[str | None, str, tuple[str, ...]]] = []
        self.read_limits: list[tuple[str, int | None]] = []

    async def diff_paths(self, *, dirs, base, head, ctx):
        del ctx
        key = (base, head, tuple(dirs))
        self.diff_calls.append(key)
        return self.diffs.get(key, [])

    async def show_blob_raw(
        self,
        head: str,
        *,
        path: str,
        ctx: RequestContext,
        max_blob_bytes: int | None = None,
    ):
        del ctx
        self.reads.append(path)
        self.read_limits.append((path, max_blob_bytes))
        blob = self.blobs[(head, path)]
        if max_blob_bytes is not None and int(blob["size"]) > max_blob_bytes:
            raise AGFSResourceExhaustedError("blob exceeds configured limit")
        return blob


def _entry(path: str, *, size: int = 1, is_dir: bool = False) -> dict[str, Any]:
    return {
        "path": path,
        "info": {"size": size, "isDir": is_dir},
    }


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("default", "alice"),
        role=Role.USER,
    )


def _graph(fs: _GraphFs, **limits: int) -> ArtifactRelationGraph:
    return ArtifactRelationGraph(
        None,
        fs,
        max_nodes=limits.get("max_nodes", 100),
        max_markdown_files=limits.get("max_markdown_files", 100),
        max_markdown_bytes=limits.get("max_markdown_bytes", 1024),
    )


@pytest.mark.asyncio
async def test_cache_publish_uses_one_supported_write() -> None:
    agfs = _CacheAgfs()
    store = CompileStateStore(agfs)
    graph = {"schema_version": 1, "nodes": [], "edges": []}

    await store.write_artifact_relations("default", "viking://resources/wiki", graph)

    assert len(agfs.writes) == 1
    path, data, ctx = agfs.writes[0]
    assert path == (
        "/local/default/_system/compile/target_folders/"
        "3e0b9d0a07f9ee59930bdb9747bb71ae33da0e276cd1b48808167cc6888505ce/"
        "artifact_relations.json"
    )
    assert json.loads(data) == graph
    assert ctx == {
        "account_id": "default",
        "disable_auto_pathlock": "true",
    }


@pytest.mark.asyncio
async def test_graph_state_publish_uses_lightweight_sibling_file() -> None:
    agfs = _CacheAgfs()
    store = CompileStateStore(agfs)
    state = {
        "schema_version": 1,
        "target_snapshot_id": "head",
        "status": "building",
    }

    await store.write_artifact_relations_state(
        "default",
        "viking://resources/wiki",
        state,
    )

    path, data, ctx = agfs.writes[0]
    assert path.endswith("/artifact_relations_state.json")
    assert json.loads(data) == state
    assert ctx["disable_auto_pathlock"] == "true"


@pytest.mark.asyncio
async def test_cache_hit_avoids_tree_lock_and_rebuild() -> None:
    record = {
        "to": "viking://resources/wiki",
        "initialized": True,
        "wiki_head": "abc123",
    }
    cache = {
        "schema_version": 1,
        "to": record["to"],
        "target_snapshot_id": "abc123",
        "generated_at": "2026-01-01T00:00:00Z",
        "nodes": [],
        "edges": [],
    }
    sources = {
        "schema_version": 1,
        "parser_version": 1,
        "ignore_signature": hashlib.sha256(b"").hexdigest(),
        "to": record["to"],
        "target_snapshot_id": "abc123",
        "pages": {},
        "page_sizes": {},
    }
    fs = _GraphFs([], {})
    graph = ArtifactRelationGraph(
        _GraphStore(record, cache, sources),
        fs,
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=10,
    )

    def unexpected_lock(_uri: str):
        raise AssertionError("cache hit acquired the tree lock")

    result = await graph.get(
        record,
        _ctx(),
        lock_output=unexpected_lock,
        commit_snapshot=AsyncMock(),
    )

    assert result == {**cache, "stale": False, "building": False}


@pytest.mark.asyncio
async def test_legacy_target_initializes_wiki_head_then_returns_building() -> None:
    record = {
        "to": "viking://resources/wiki",
        "initialized": True,
        "wiki_head": None,
    }
    store = _GraphStore(record)
    fs = _GraphFs([], {})
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=10,
        query_wait_seconds=0,
    )
    graph.enqueue_wakeup = AsyncMock(return_value=True)
    locked = False

    @asynccontextmanager
    async def lock_output(_uri: str):
        nonlocal locked
        locked = True
        yield {"lease_ref": "tree"}

    async def commit_snapshot(_uri: str, lease: dict[str, str]):
        assert locked
        assert lease["lease_ref"] == "tree"
        return "new-head"

    result = await graph.get(
        record,
        _ctx(),
        lock_output=lock_output,
        commit_snapshot=commit_snapshot,
    )

    assert record["wiki_head"] == "new-head"
    assert store.published is None
    assert result == {
        "schema_version": 1,
        "to": record["to"],
        "target_snapshot_id": "new-head",
        "nodes": [],
        "edges": [],
        "stale": False,
        "building": True,
    }
    graph.enqueue_wakeup.assert_awaited_once_with("default", record["to"], "alice")


@pytest.mark.asyncio
async def test_query_returns_acl_filtered_stale_graph_after_wait_budget() -> None:
    target = "viking://resources/wiki"
    visible = f"{target}/visible.md"
    secret = f"{target}/secret.md"
    record = {"to": target, "initialized": True, "wiki_head": "new-head"}
    cache = {
        "schema_version": 1,
        "to": target,
        "target_snapshot_id": "old-head",
        "generated_at": "2026-01-01T00:00:00Z",
        "nodes": [{"uri": visible}, {"uri": secret}],
        "edges": [{"type": "references", "from": visible, "to": secret}],
    }
    graph = ArtifactRelationGraph(
        _GraphStore(record, cache),
        _GraphFs([], {}),
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=10,
        query_wait_seconds=0,
    )
    graph.enqueue_wakeup = AsyncMock(return_value=True)

    result = await graph.get(
        record,
        _ctx(),
        lock_output=AsyncMock(),
        commit_snapshot=AsyncMock(),
    )

    assert result["stale"] is True
    assert result["building"] is True
    assert result["nodes"] == [{"uri": visible}]
    assert result["edges"] == []


@pytest.mark.asyncio
async def test_compile_graph_failure_falls_back_to_queue() -> None:
    target = "viking://resources/wiki"
    record = {"to": target, "initialized": True, "wiki_head": "head"}
    graph = ArtifactRelationGraph(
        _GraphStore(record),
        _GraphFs([], {}),
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=10,
    )
    graph._compile_pages[("default", target, "op")] = {}
    graph.reconcile_graph = AsyncMock(side_effect=RuntimeError("build failed"))
    graph.enqueue_wakeup = AsyncMock(return_value=True)

    await graph.reconcile_compile(
        record,
        _ctx(),
        wiki_head="head",
        operation_id="op",
    )

    assert ("default", target, "op") not in graph._compile_pages
    graph.enqueue_wakeup.assert_awaited_once_with("default", target, "alice")


@pytest.mark.asyncio
async def test_compile_hint_parses_written_bytes_without_snapshot_read() -> None:
    target = "viking://resources/wiki"
    page = f"{target}/page.md"
    related = f"{target}/related.md"
    graph = ArtifactRelationGraph(
        _GraphStore({"to": target}),
        _GraphFs([], {}),
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=100,
    )
    content = b"[related](related.md)"

    await graph.capture_compile_page(
        "default",
        target,
        "op",
        "snapshot",
        page,
        _ctx(),
        content,
    )

    assert graph._compile_pages[("default", target, "op")][page] == {
        "blob_oid": hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content,
            usedforsecurity=False,
        ).hexdigest(),
        "snapshot_id": "snapshot",
        "size": len(content),
        "related_pages": [related],
        "relations_overflow": False,
    }


@pytest.mark.asyncio
async def test_compile_hint_without_written_bytes_does_not_read_snapshot() -> None:
    target = "viking://resources/wiki"
    page = f"{target}/page.md"
    fs = _GraphFs([], {})
    graph = ArtifactRelationGraph(
        _GraphStore({"to": target}),
        fs,
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=10,
    )

    await graph.capture_compile_page(
        "default",
        target,
        "op",
        "snapshot",
        page,
        _ctx(),
    )

    assert fs.reads == []
    assert graph._compile_pages == {}


@pytest.mark.asyncio
async def test_reconcile_uses_compile_hint_then_updates_incrementally() -> None:
    target = "viking://resources/wiki"
    a = f"{target}/a.md"
    b = f"{target}/b.md"
    c = f"{target}/sub/c.md"
    asset = f"{target}/logo.png"
    record = {"to": target, "initialized": True, "wiki_head": "h1"}
    store = _GraphStore(record)
    fs = _SnapshotGraphFs(
        {
            (None, "h1", (target,)): [
                {"uri": a, "change_type": "added"},
                {"uri": b, "change_type": "added"},
                {"uri": asset, "change_type": "added"},
            ],
            ("hint-a1", "h1", (a,)): [],
            ("h1", "h2", (target,)): [
                {"uri": a, "change_type": "modified"},
                {"uri": b, "change_type": "deleted"},
                {"uri": c, "change_type": "added"},
            ],
            ("hint-a2", "h2", (a,)): [],
        },
        {
            ("h1", b): {"oid": "oid-b1", "size": 3, "bytes": b"# B"},
            ("h2", c): {"oid": "oid-c1", "size": 9, "bytes": b"[A](../a.md)"},
        },
    )
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=20,
        max_markdown_files=10,
        max_markdown_bytes=1024,
    )

    first = await graph.reconcile_graph(
        record,
        _ctx(),
        wiki_head="h1",
        parsed_pages={
            a: {
                "blob_oid": "oid-a1",
                "snapshot_id": "hint-a1",
                "size": 9,
                "related_pages": [b],
            }
        },
    )

    assert fs.reads == [b]
    assert store.write_order == ["state:building", "sources", "graph", "state:ready"]
    assert store.sources["pages"] == {
        a: {"blob_oid": "oid-a1", "related_pages": [b]},
        b: {"blob_oid": "oid-b1", "related_pages": []},
    }
    assert ("references", a, b) in {
        (edge["type"], edge["from"], edge["to"]) for edge in first["edges"]
    }

    record["wiki_head"] = "h2"
    second = await graph.reconcile_graph(
        record,
        _ctx(),
        wiki_head="h2",
        parsed_pages={
            a: {
                "blob_oid": "oid-a2",
                "snapshot_id": "hint-a2",
                "size": 8,
                "related_pages": [c],
            }
        },
    )

    assert fs.reads == [b, c]
    assert set(store.sources["pages"]) == {a, c}
    assert b not in {node["uri"] for node in second["nodes"]}
    assert {asset, a, c, f"{target}/", f"{target}/sub/"} == {
        node["uri"] for node in second["nodes"]
    }
    assert {
        (edge["type"], edge["from"], edge["to"]) for edge in second["edges"]
    } >= {
        ("references", a, c),
        ("references", c, a),
    }


@pytest.mark.asyncio
async def test_reconcile_rereads_snapshot_when_compile_hint_changed() -> None:
    target = "viking://resources/wiki"
    page = f"{target}/page.md"
    actual = f"{target}/actual.md"
    record = {"to": target, "initialized": True, "wiki_head": "head"}
    store = _GraphStore(record)
    fs = _SnapshotGraphFs(
        {
            (None, "head", (target,)): [
                {"uri": page, "change_type": "added"},
                {"uri": actual, "change_type": "added"},
            ],
            ("hint", "head", (page,)): [
                {"uri": page, "change_type": "modified"},
            ],
        },
        {
            ("head", page): {
                "oid": "final-oid",
                "size": 19,
                "bytes": b"[actual](actual.md)",
            },
            ("head", actual): {"oid": "actual-oid", "size": 8, "bytes": b"# Actual"},
        },
    )
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=1024,
    )

    await graph.reconcile_graph(
        record,
        _ctx(),
        wiki_head="head",
        parsed_pages={
            page: {
                "blob_oid": "stale-oid",
                "snapshot_id": "hint",
                "size": 8,
                "related_pages": [],
            }
        },
    )

    assert fs.reads == [page, actual]
    assert store.sources["pages"][page] == {
        "blob_oid": "final-oid",
        "related_pages": [actual],
    }


@pytest.mark.asyncio
async def test_reconcile_enforces_markdown_byte_limit() -> None:
    target = "viking://resources/wiki"
    page = f"{target}/page.md"
    record = {"to": target, "initialized": True, "wiki_head": "head"}
    store = _GraphStore(record)
    fs = _SnapshotGraphFs(
        {
            (None, "head", (target,)): [
                {"uri": page, "change_type": "added"},
            ],
        },
        {
            ("head", page): {"oid": "oid", "size": 6, "bytes": b"123456"},
        },
    )
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=5,
    )

    with pytest.raises(ArtifactGraphTooLargeError) as exc_info:
        await graph.reconcile_graph(record, _ctx(), wiki_head="head")

    assert exc_info.value.details == {
        "limit": "markdown_bytes",
        "maximum": 5,
        "actual": 6,
    }
    assert fs.read_limits == [(page, 5)]


@pytest.mark.asyncio
async def test_reconcile_passes_remaining_markdown_budget_to_each_blob_read() -> None:
    target = "viking://resources/wiki"
    first = f"{target}/a.md"
    second = f"{target}/b.md"
    record = {"to": target, "initialized": True, "wiki_head": "head"}
    store = _GraphStore(record)
    fs = _SnapshotGraphFs(
        {
            (None, "head", (target,)): [
                {"uri": first, "change_type": "added"},
                {"uri": second, "change_type": "added"},
            ],
        },
        {
            ("head", first): {"oid": "a", "size": 4, "bytes": b"aaaa"},
            ("head", second): {"oid": "b", "size": 4, "bytes": b"bbbb"},
        },
    )
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=5,
    )

    with pytest.raises(ArtifactGraphTooLargeError):
        await graph.reconcile_graph(record, _ctx(), wiki_head="head")

    assert fs.read_limits == [(first, 5), (second, 1)]
    assert store.published is None


@pytest.mark.asyncio
async def test_reconcile_enforces_relation_limit_without_publishing_partial_graph() -> None:
    target = "viking://resources/wiki"
    page = f"{target}/page.md"
    related = f"{target}/related.md"
    record = {"to": target, "initialized": True, "wiki_head": "head"}
    store = _GraphStore(record)
    fs = _SnapshotGraphFs(
        {
            (None, "head", (target,)): [
                {"uri": page, "change_type": "added"},
                {"uri": related, "change_type": "added"},
            ],
        },
        {
            ("head", page): {
                "oid": "page",
                "size": 21,
                "bytes": b"[related](related.md)",
            },
            ("head", related): {"oid": "related", "size": 9, "bytes": b"# Related"},
        },
    )
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=10,
        max_relations=2,
        max_markdown_files=10,
        max_markdown_bytes=1024,
    )

    with pytest.raises(ArtifactGraphTooLargeError) as exc_info:
        await graph.reconcile_graph(record, _ctx(), wiki_head="head")

    assert exc_info.value.details == {
        "limit": "relations",
        "maximum": 2,
        "actual": 3,
    }
    assert store.published is None
    assert store.published_sources is None
    assert store.state["status"] == "failed"
    assert store.state["error"]["code"] == "ARTIFACT_RELATIONS_TOO_LARGE"


@pytest.mark.asyncio
async def test_compile_hint_overflow_rereads_bounded_snapshot() -> None:
    target = "viking://resources/wiki"
    page = f"{target}/page.md"
    related = f"{target}/related.md"
    record = {"to": target, "initialized": True, "wiki_head": "head"}
    store = _GraphStore(record)
    fs = _SnapshotGraphFs(
        {
            (None, "head", (target,)): [
                {"uri": page, "change_type": "added"},
                {"uri": related, "change_type": "added"},
            ],
            ("hint", "head", (page,)): [],
        },
        {
            ("head", page): {
                "oid": "page",
                "size": 21,
                "bytes": b"[related](related.md)",
            },
            ("head", related): {"oid": "related", "size": 9, "bytes": b"# Related"},
        },
    )
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=10,
        max_relations=3,
        max_markdown_files=10,
        max_markdown_bytes=1024,
    )
    hint = " ".join(f"[x{i}](missing-{i}.md)" for i in range(4)).encode()
    await graph.capture_compile_page(
        "default",
        target,
        "op",
        "hint",
        page,
        _ctx(),
        hint,
    )
    captured = graph._compile_pages[("default", target, "op")][page]
    assert captured["relations_overflow"] is True
    assert len(captured["related_pages"]) == 3

    result = await graph.reconcile_graph(
        record,
        _ctx(),
        wiki_head="head",
        parsed_pages={page: captured},
    )

    assert page in fs.reads
    assert ("references", page, related) in {
        (edge["type"], edge["from"], edge["to"]) for edge in result["edges"]
    }


@pytest.mark.asyncio
async def test_query_surfaces_failed_state_without_rereading_graph() -> None:
    target = "viking://resources/wiki"
    record = {"to": target, "initialized": True, "wiki_head": "head"}
    store = _GraphStore(record)
    store.state = {
        "schema_version": 1,
        "parser_version": 1,
        "ignore_signature": hashlib.sha256(b"").hexdigest(),
        "target_snapshot_id": "head",
        "status": "failed",
        "attempts": 1,
        "error": {
            "code": "ARTIFACT_RELATIONS_TOO_LARGE",
            "message": "too large",
            "details": {"limit": "relations", "maximum": 2, "actual": 3},
        },
    }
    graph = ArtifactRelationGraph(
        store,
        _GraphFs([], {}),
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=10,
        query_wait_seconds=0.01,
    )
    graph.enqueue_wakeup = AsyncMock(return_value=True)

    with pytest.raises(ArtifactGraphError) as exc_info:
        await graph.get(
            record,
            _ctx(),
            lock_output=AsyncMock(),
            commit_snapshot=AsyncMock(),
        )

    assert exc_info.value.code == "ARTIFACT_RELATIONS_TOO_LARGE"
    assert store.cache_reads == 1
    graph.enqueue_wakeup.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_target_worker_does_not_rebuild_graph() -> None:
    target = "viking://resources/wiki"
    record = {
        "to": target,
        "status": "deleted",
        "initialized": True,
        "wiki_head": "head",
    }
    store = _GraphStore(record)
    fs = _SnapshotGraphFs({}, {})
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=10,
    )

    await graph.reconcile_target("default", target, "alice")

    assert store.state is None
    assert fs.diff_calls == []


@pytest.mark.asyncio
async def test_target_deleted_before_graph_lock_does_not_rebuild() -> None:
    target = "viking://resources/wiki"
    active = {"to": target, "status": "active", "initialized": True, "wiki_head": "head"}
    deleted = {**active, "status": "deleted"}
    store = _GraphStore(deleted)
    fs = _SnapshotGraphFs({}, {})
    graph = ArtifactRelationGraph(
        store,
        fs,
        max_nodes=10,
        max_markdown_files=10,
        max_markdown_bytes=10,
    )

    result = await graph.reconcile_graph(active, _ctx(), wiki_head="head")

    assert result is None
    assert store.state is None
    assert fs.diff_calls == []
