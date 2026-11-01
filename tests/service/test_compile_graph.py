from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.compile_graph import (
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
    def __init__(self, record: dict[str, Any], cache: dict[str, Any] | None = None) -> None:
        self.record = record
        self.cache = cache
        self.published = None

    async def get(self, _account_id: str, _target_id: str):
        return self.record

    async def read_artifact_relations(self, _account_id: str, _target_id: str):
        return self.cache

    async def write_artifact_relations(self, _account_id: str, _target_id: str, value):
        self.published = value

    @asynccontextmanager
    async def edit(self, _account_id: str, _target_id: str):
        yield self.record


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
async def test_builds_stable_containment_and_reference_edges() -> None:
    root = "/local/default/resources/wiki"
    entries = [
        _entry(f"{root}/sub", is_dir=True),
        _entry(f"{root}/a.md", size=60),
        _entry(f"{root}/sub/b.md", size=10),
        _entry(f"{root}/secret.md", size=6),
        _entry(f"{root}/.overview.md", size=100),
        _entry(f"{root}/guide.abstract.md", size=10),
        _entry(f"{root}/.path.ovlock", size=100),
        _entry(f"{root}/ignored.log", size=100),
    ]
    fs = _GraphFs(
        entries,
        {
            "viking://resources/wiki/a.md": (
                b"[B](sub/b.md) [again](sub/b.md) [query](?view=1) "
                b"[external](https://example.com)"
            ),
            "viking://resources/wiki/sub/b.md": b"# B",
            "viking://resources/wiki/secret.md": b"# S",
            "viking://resources/wiki/guide.abstract.md": b"# Guide",
        },
        gitignore=b"*.log\n",
    )

    result = await _graph(fs)._build("viking://resources/wiki", "abc123", _ctx())

    assert result["nodes"] == [
        {"uri": "viking://resources/wiki/"},
        {"uri": "viking://resources/wiki/a.md"},
        {"uri": "viking://resources/wiki/guide.abstract.md"},
        {"uri": "viking://resources/wiki/secret.md"},
        {"uri": "viking://resources/wiki/sub/"},
        {"uri": "viking://resources/wiki/sub/b.md"},
    ]
    assert {
        (edge["type"], edge["from"], edge["to"]) for edge in result["edges"]
    } == {
        (
            "contains",
            "viking://resources/wiki/",
            "viking://resources/wiki/a.md",
        ),
        (
            "contains",
            "viking://resources/wiki/",
            "viking://resources/wiki/guide.abstract.md",
        ),
        (
            "contains",
            "viking://resources/wiki/",
            "viking://resources/wiki/secret.md",
        ),
        (
            "contains",
            "viking://resources/wiki/",
            "viking://resources/wiki/sub/",
        ),
        (
            "contains",
            "viking://resources/wiki/sub/",
            "viking://resources/wiki/sub/b.md",
        ),
        (
            "references",
            "viking://resources/wiki/a.md",
            "viking://resources/wiki/sub/b.md",
        ),
    }

    filtered = await _graph(fs)._filter(result, _ctx())
    assert all(node["uri"] != "viking://resources/wiki/secret.md" for node in filtered["nodes"])
    assert all("secret.md" not in (edge["from"], edge["to"]) for edge in filtered["edges"])


@pytest.mark.asyncio
async def test_size_limit_stops_before_markdown_reads() -> None:
    root = "/local/default/resources/wiki"
    fs = _GraphFs(
        [
            _entry(f"{root}/a.md", size=6),
            _entry(f"{root}/b.md", size=6),
        ],
        {
            "viking://resources/wiki/a.md": b"123456",
            "viking://resources/wiki/b.md": b"123456",
        },
    )

    with pytest.raises(ArtifactGraphTooLargeError) as exc_info:
        await _graph(fs, max_markdown_bytes=10)._build(
            "viking://resources/wiki", "abc123", _ctx()
        )

    assert exc_info.value.code == "ARTIFACT_RELATIONS_TOO_LARGE"
    assert fs.reads == []


@pytest.mark.asyncio
async def test_node_limit_stops_during_tree_scan() -> None:
    root = "/local/default/resources/wiki"
    fs = _GraphFs(
        [
            _entry(f"{root}/a.txt"),
            _entry(f"{root}/b.txt"),
            _entry(f"{root}/c.txt"),
        ],
        {},
    )

    with pytest.raises(ArtifactGraphTooLargeError) as exc_info:
        await _graph(fs, max_nodes=3)._build(
            "viking://resources/wiki", "abc123", _ctx()
        )

    assert exc_info.value.details == {
        "limit": "nodes",
        "maximum": 3,
        "actual": 4,
    }


@pytest.mark.asyncio
async def test_actual_bytes_are_checked_when_metadata_underreports() -> None:
    root = "/local/default/resources/wiki"
    fs = _GraphFs(
        [_entry(f"{root}/a.md", size=1)],
        {"viking://resources/wiki/a.md": b"123456"},
    )

    with pytest.raises(ArtifactGraphTooLargeError):
        await _graph(fs, max_markdown_bytes=5)._build(
            "viking://resources/wiki", "abc123", _ctx()
        )


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
    fs = _GraphFs([], {})
    graph = ArtifactRelationGraph(
        _GraphStore(record, cache),
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

    assert result == cache


@pytest.mark.asyncio
async def test_legacy_target_initializes_wiki_head_inside_lock() -> None:
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
    )
    graph._build = AsyncMock(
        return_value={
            "schema_version": 1,
            "to": record["to"],
            "target_snapshot_id": "new-head",
            "generated_at": "2026-01-01T00:00:00Z",
            "nodes": [],
            "edges": [],
        }
    )
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
    assert store.published == result
