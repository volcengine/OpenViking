# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Artifact relation graph construction and cache filtering."""

from __future__ import annotations

import asyncio
import posixpath
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from pathspec import GitIgnoreSpec

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext
from openviking.storage.acl import AclAction
from openviking_cli.exceptions import OpenVikingError

SCHEMA_VERSION = 1
_DERIVED_NAMES = frozenset({".abstract.md", ".overview.md", ".relations.json"})
_READ_CONCURRENCY = 16
_TREE_PAGE_SIZE = 512


class ArtifactGraphError(OpenVikingError):
    def __init__(self, message: str, code: str, details: dict[str, Any] | None = None):
        super().__init__(message, code=code, details=details)


class ArtifactGraphTooLargeError(ArtifactGraphError):
    def __init__(self, limit: str, maximum: int, actual: int):
        super().__init__(
            "Artifact relation graph exceeds the configured limit",
            code="ARTIFACT_RELATIONS_TOO_LARGE",
            details={"limit": limit, "maximum": maximum, "actual": actual},
        )


class ArtifactRelationGraph:
    """Build one target's graph once, then filter the cached result per caller."""

    def __init__(
        self,
        store: Any,
        viking_fs: Any,
        *,
        max_nodes: int,
        max_markdown_files: int,
        max_markdown_bytes: int,
    ) -> None:
        self._store = store
        self._fs = viking_fs
        self._max_nodes = max_nodes
        self._max_markdown_files = max_markdown_files
        self._max_markdown_bytes = max_markdown_bytes
        self._markdown = MarkdownIt("commonmark")

    async def get(
        self,
        record: dict[str, Any],
        ctx: RequestContext,
        *,
        lock_output: Any,
        commit_snapshot: Any,
    ) -> dict[str, Any]:
        if not record.get("initialized"):
            raise ArtifactGraphError(
                "Compile target is not initialized",
                code="COMPILE_TARGET_NOT_INITIALIZED",
            )
        cached = await self._valid_cache(ctx.account_id, record)
        if cached is None:
            async with lock_output(record["to"]) as lease:
                record = await self._store.get(ctx.account_id, record["to"])
                cached = await self._valid_cache(ctx.account_id, record)
                if cached is None:
                    wiki_head = record.get("wiki_head")
                    if not wiki_head:
                        wiki_head = await commit_snapshot(record["to"], lease)
                        if not wiki_head:
                            raise ArtifactGraphError(
                                "Snapshots are disabled",
                                code="SNAPSHOT_DISABLED",
                            )
                        async with self._store.edit(ctx.account_id, record["to"]) as editable:
                            editable["wiki_head"] = wiki_head
                        record["wiki_head"] = wiki_head
                    try:
                        cached = await self._build(record["to"], wiki_head, ctx)
                        await self._store.write_artifact_relations(
                            ctx.account_id, record["to"], cached
                        )
                    except ArtifactGraphError:
                        raise
                    except Exception as exc:
                        raise ArtifactGraphError(
                            "Failed to build artifact relation graph",
                            code="ARTIFACT_RELATIONS_BUILD_FAILED",
                        ) from exc
        return await self._filter(cached, ctx)

    async def build_locked(
        self,
        record: dict[str, Any],
        ctx: RequestContext,
        *,
        wiki_head: str,
    ) -> dict[str, Any]:
        graph = await self._build(record["to"], wiki_head, ctx)
        await self._store.write_artifact_relations(ctx.account_id, record["to"], graph)
        return graph

    async def _valid_cache(
        self, account_id: str, record: dict[str, Any]
    ) -> dict[str, Any] | None:
        wiki_head = record.get("wiki_head")
        if not wiki_head:
            return None
        try:
            cached = await self._store.read_artifact_relations(account_id, record["to"])
        except (ValueError, UnicodeDecodeError):
            return None
        if (
            cached
            and cached.get("schema_version") == SCHEMA_VERSION
            and cached.get("to") == record["to"].rstrip("/")
            and cached.get("target_snapshot_id") == wiki_head
            and isinstance(cached.get("nodes"), list)
            and isinstance(cached.get("edges"), list)
        ):
            return cached
        return None

    async def _build(
        self, to: str, wiki_head: str, ctx: RequestContext
    ) -> dict[str, Any]:
        to = to.rstrip("/")
        internal_ctx = self._internal_ctx(ctx)
        files, nodes = await self._scan_entries(to, internal_ctx)
        node_uris = sorted(nodes)
        markdown = [entry for entry in files if entry["uri"].lower().endswith(".md")]

        node_set = set(node_uris)
        edges = self._containment_edges(to, node_set)
        actual_markdown_bytes = 0
        for start in range(0, len(markdown), _READ_CONCURRENCY):
            batch = markdown[start : start + _READ_CONCURRENCY]
            contents = await asyncio.gather(
                *(self._fs.read_file_bytes(entry["uri"], ctx=internal_ctx) for entry in batch)
            )
            for entry, raw in zip(batch, contents, strict=True):
                actual_markdown_bytes += len(raw)
                self._check_limit(
                    "markdown_bytes", self._max_markdown_bytes, actual_markdown_bytes
                )
                text = raw.decode("utf-8", errors="replace")
                for target in self._link_targets(text, entry["uri"], node_set):
                    edges.add(("references", entry["uri"], target))

        return {
            "schema_version": SCHEMA_VERSION,
            "to": to,
            "target_snapshot_id": wiki_head,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "nodes": [{"uri": uri} for uri in node_uris],
            "edges": [
                {"type": edge_type, "from": source, "to": target}
                for edge_type, source, target in sorted(edges)
            ],
        }

    async def _scan_entries(
        self, to: str, ctx: RequestContext
    ) -> tuple[list[dict[str, Any]], set[str]]:
        root_path = self._fs._uri_to_path(to, ctx=ctx)
        ignore = await self._ignore_spec(ctx)
        entries: list[dict[str, Any]] = []
        nodes = {f"{to.rstrip('/')}/"}
        markdown_files = 0
        markdown_bytes = 0
        offset = 0
        while True:
            page = await self._fs._async_agfs.tree_directory(
                root_path,
                show_hidden=True,
                node_limit=_TREE_PAGE_SIZE,
                level_limit=None,
                offset=offset,
                sort_by="name",
                sort_order="asc",
            )
            if not page:
                break
            offset += len(page)
            for entry in page:
                if not self._fs._is_tree_entry_visible(
                    entry,
                    root_path,
                    ctx,
                    acl_enabled=False,
                ):
                    continue
                info = entry.get("info", {})
                uri = self._fs._path_to_uri(entry["path"], ctx=ctx).rstrip("/")
                is_dir = bool(info.get("isDir", False))
                if is_dir or self._excluded(uri, ignore):
                    continue
                item = {
                    "uri": uri,
                    "isDir": False,
                    "size": int(info.get("size") or 0),
                }
                entries.append(item)
                nodes.add(uri)
                nodes.update(self._required_directories(to, (uri,)))
                self._check_limit("nodes", self._max_nodes, len(nodes))
                if uri.lower().endswith(".md"):
                    markdown_files += 1
                    markdown_bytes += item["size"]
                    self._check_limit(
                        "markdown_files",
                        self._max_markdown_files,
                        markdown_files,
                    )
                    self._check_limit(
                        "markdown_bytes",
                        self._max_markdown_bytes,
                        markdown_bytes,
                    )
            if len(page) < _TREE_PAGE_SIZE:
                break
        return entries, nodes

    async def _ignore_spec(self, ctx: RequestContext) -> GitIgnoreSpec | None:
        path = self._fs._gitignore_agfs_path(ctx)
        try:
            raw = await self._fs._async_agfs.read(path)
        except (AGFSNotFoundError, FileNotFoundError):
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("!") or "\\" in line:
                raise ArtifactGraphError(
                    "Invalid .ovgitignore",
                    code="ARTIFACT_RELATIONS_BUILD_FAILED",
                )
            lines.append(line)
        return GitIgnoreSpec.from_lines(lines) if lines else None

    @staticmethod
    def _excluded(uri: str, ignore: GitIgnoreSpec | None) -> bool:
        leaf = uri.rsplit("/", 1)[-1]
        if leaf in _DERIVED_NAMES:
            return True
        rel = uri.removeprefix("viking://").lstrip("/")
        return bool(ignore and ignore.match_file(rel))

    @staticmethod
    def _required_directories(to: str, files: Iterable[str]) -> set[str]:
        root = f"{to}/"
        result: set[str] = set()
        for uri in files:
            result.add(root)
            parent = posixpath.dirname(uri)
            while parent and parent != "viking:":
                result.add(f"{parent.rstrip('/')}/")
                if parent == to:
                    break
                parent = posixpath.dirname(parent)
        return result

    @staticmethod
    def _containment_edges(to: str, nodes: set[str]) -> set[tuple[str, str, str]]:
        edges: set[tuple[str, str, str]] = set()
        root = f"{to}/"
        for uri in nodes:
            if uri == root:
                continue
            value = uri.rstrip("/")
            parent = f"{posixpath.dirname(value)}/"
            if parent in nodes:
                edges.add(("contains", parent, uri))
        return edges

    def _link_targets(self, text: str, source: str, nodes: set[str]) -> set[str]:
        targets: set[str] = set()
        for token in self._markdown.parse(text):
            children = token.children or []
            for child in children:
                if child.type != "link_open":
                    continue
                href = child.attrGet("href")
                target = self._resolve_link(source, href or "")
                if target in nodes:
                    targets.add(target)
                elif f"{target.rstrip('/')}/" in nodes:
                    targets.add(f"{target.rstrip('/')}/")
        return targets

    @staticmethod
    def _resolve_link(source: str, href: str) -> str:
        value = unquote(href.strip())
        if not value or value.startswith("#"):
            return ""
        parsed = urlsplit(value)
        if parsed.scheme and parsed.scheme != "viking":
            return ""
        if parsed.scheme == "viking":
            path = f"{parsed.netloc}{parsed.path}".strip("/")
            return f"viking://{posixpath.normpath(path)}"
        path = parsed.path
        if not path:
            return ""
        if path.startswith("/"):
            return f"viking://{posixpath.normpath(path).lstrip('/')}"
        parent = posixpath.dirname(source.removeprefix("viking://"))
        return f"viking://{posixpath.normpath(posixpath.join(parent, path))}"

    async def _filter(
        self, graph: dict[str, Any], ctx: RequestContext
    ) -> dict[str, Any]:
        uris = [node["uri"] for node in graph["nodes"]]
        access = await self._fs._can_access_many(uris, ctx, action=AclAction.READ)
        visible = {uri for uri in uris if access.get(uri, False)}
        return {
            **graph,
            "nodes": [node for node in graph["nodes"] if node["uri"] in visible],
            "edges": [
                edge
                for edge in graph["edges"]
                if edge["from"] in visible and edge["to"] in visible
            ],
        }

    @staticmethod
    def _internal_ctx(ctx: RequestContext) -> RequestContext:
        from dataclasses import replace

        return replace(ctx, bypass_acl=True)

    @staticmethod
    def _check_limit(name: str, maximum: int, actual: int) -> None:
        if actual > maximum:
            raise ArtifactGraphTooLargeError(name, maximum, actual)
