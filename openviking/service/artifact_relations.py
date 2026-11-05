# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Snapshot-aligned relation graphs for Compile output directories."""

from __future__ import annotations

import asyncio
import hashlib
import posixpath
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from openviking.server.identity import RequestContext
from openviking.session.memory.utils.link_renderer import LinkRenderer
from openviking_cli.exceptions import FailedPreconditionError, NotFoundError
from openviking_cli.utils import VikingURI

SCHEMA_VERSION = 1
PARSER_VERSION = 1
QUERY_WAIT_SECONDS = 10.0


def _name(uri: str) -> str:
    return unquote(uri.rstrip("/").rsplit("/", 1)[-1])


def _contains(root: str, uri: str) -> bool:
    return uri == root or uri.startswith(f"{root}/")


def _parent(uri: str) -> str | None:
    parent = VikingURI(uri).parent
    return str(parent).rstrip("/") if parent is not None and parent.scope else None


def _edge_id(relation_type: str, source_uri: str, target_uri: str) -> str:
    value = f"{relation_type}\0{source_uri}\0{target_uri}".encode()
    return hashlib.sha256(value).hexdigest()


def _resolve_link(source_uri: str, href: str, root: str) -> tuple[str | None, str | None]:
    raw = href.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    if not raw or raw.startswith("#"):
        return None, None
    parsed = urlsplit(raw)
    fragment = unquote(parsed.fragment) or None
    if parsed.scheme and parsed.scheme != "viking":
        return None, fragment
    if parsed.scheme == "viking":
        candidate = f"viking://{parsed.netloc}{parsed.path}".rstrip("/")
    else:
        path = unquote(parsed.path)
        if not path:
            return None, fragment
        if path.startswith("/"):
            candidate = f"{root}/{path.lstrip('/')}"
        else:
            source_parent = source_uri.rpartition("/")[0]
            relative = posixpath.normpath(f"{source_parent.removeprefix('viking://')}/{path}")
            candidate = f"viking://{relative.lstrip('/')}"
    candidate = candidate.rstrip("/")
    return (candidate if _contains(root, candidate) else None), fragment


def extract_relation_source(
    source_uri: str,
    content: str,
    content_id: str,
    root: str,
) -> dict[str, Any]:
    """Extract durable raw Markdown links and their root-scoped candidates."""
    links = []
    for link in LinkRenderer.iter_markdown_links(content):
        target_uri, fragment = _resolve_link(source_uri, link.target, root)
        item: dict[str, Any] = {
            "href": link.target,
            "anchor": link.text,
            "target_uri": target_uri,
        }
        if fragment is not None:
            item["fragment"] = fragment
        links.append(item)
    return {"content_id": content_id, "links": links}


def _rebase_relation_source(
    source: Mapping[str, Any], source_uri: str, root: str
) -> dict[str, Any]:
    links = []
    for stored in source.get("links", []):
        target_uri, fragment = _resolve_link(source_uri, str(stored.get("href") or ""), root)
        link = {
            "href": stored.get("href"),
            "anchor": stored.get("anchor"),
            "target_uri": target_uri,
        }
        if fragment is not None:
            link["fragment"] = fragment
        links.append(link)
    return {"content_id": source.get("content_id"), "links": links}


class ArtifactRelationsService:
    """Build, publish and query one relation graph per Compile target."""

    def __init__(self, store: Any, viking_fs: Any, fs_service: Any = None) -> None:
        self._store = store
        self._fs = viking_fs
        self._fs_service = fs_service
        self._background: set[asyncio.Task[Any]] = set()

    async def build(
        self,
        account_id: str,
        to: str,
        *,
        extracted: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Publish exactly the target snapshot observed after acquiring the state lock."""
        to = to.rstrip("/")
        state_path = self._store.artifact_relations_state_path(account_id, to)
        async with self._store.locked(state_path) as lease:
            record = await self._store.get(account_id, to)
            if record is None or record.get("status") != "active":
                raise NotFoundError(to, "compile target")
            target = record.get("wiki_head") or record.get("versions_head")
            if not target:
                raise FailedPreconditionError("Compile target has no published snapshot")

            published_state = await self._store.read_json(state_path) or {}
            base = published_state.get("snapshot_id")
            if base == target:
                graph = await self._read_graph(account_id, to)
                if graph is not None:
                    return graph

            graph, relation_sources = await self._build_snapshot(
                account_id,
                to,
                base=base,
                target=target,
                extracted=extracted or {},
            )
            await self._store.write_json(
                self._store.relation_sources_path(account_id, to),
                relation_sources,
            )
            await self._store.write_json(
                self._store.artifact_relations_path(account_id, to),
                graph,
            )
            await self._store.write_json(state_path, {"snapshot_id": target}, lease)
            return graph

    async def extract_snapshot_sources(
        self,
        account_id: str,
        to: str,
        snapshot_id: str,
        uris: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Prepare Compile's ephemeral relation sources from final Markdown bytes."""
        from openviking.service.compile_targets import task_ctx

        ctx = task_ctx(account_id, "artifact-relations")
        if uris is None:
            changes = await self._fs.diff_paths(dirs=[to], base=None, head=snapshot_id, ctx=ctx)
            uris = [change["uri"] for change in changes]
        extracted = {}
        for uri in dict.fromkeys(uris):
            if not uri.lower().endswith(".md"):
                continue
            blob = await self._fs.show_blob_raw(snapshot_id, path=uri, ctx=ctx)
            raw = blob["bytes"]
            content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            extracted[uri] = extract_relation_source(
                uri,
                content,
                str(blob["oid"]),
                to,
            )
        return extracted

    async def get(self, to: str, ctx: RequestContext) -> dict[str, Any]:
        """Return the newest graph when practical, then apply caller visibility."""
        to = to.rstrip("/")
        record = await self._store.get_by_to(ctx.account_id, to)
        if record is None:
            raise NotFoundError(to, "compile target")
        ctx = self._fs.authorize_read(ctx, [to])
        await self._fs._ensure_access(to, ctx)
        published = await self._store.read_json(
            self._store.artifact_relations_state_path(ctx.account_id, to)
        )
        base = (published or {}).get("snapshot_id")
        target = record.get("wiki_head") or record.get("versions_head")

        if not base:
            graph = await self.build(ctx.account_id, to)
        elif base == target:
            graph = await self._read_graph(ctx.account_id, to)
            if graph is None:
                graph = await self.build(ctx.account_id, to)
        else:
            task = asyncio.create_task(
                self.build(ctx.account_id, to),
                name=f"artifact-relations:{ctx.account_id}:{to}",
            )
            self._background.add(task)
            task.add_done_callback(self._finish_background)
            try:
                graph = await asyncio.wait_for(asyncio.shield(task), QUERY_WAIT_SECONDS)
            except Exception:
                graph = await self._read_graph(ctx.account_id, to)
        if graph is None:
            raise FailedPreconditionError("Artifact relation graph is unavailable")
        return await self._filter_visible(graph, ctx)

    def _finish_background(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if not task.cancelled():
            task.exception()

    async def _build_snapshot(
        self,
        account_id: str,
        to: str,
        *,
        base: str | None,
        target: str,
        extracted: Mapping[str, Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from openviking.service.compile_targets import task_ctx

        ctx = task_ctx(account_id, "artifact-relations")
        old_graph = await self._read_graph(account_id, to) if base else None
        old_sources_doc = (
            await self._store.read_json(self._store.relation_sources_path(account_id, to))
            if base
            else None
        )
        sources_compatible = (
            old_sources_doc is not None
            and old_sources_doc.get("schema_version") == SCHEMA_VERSION
            and old_sources_doc.get("parser_version") == PARSER_VERSION
        )
        if base and (old_graph is None or not sources_compatible):
            base = None
            old_graph = None
            old_sources_doc = None
        sources: dict[str, Any] = dict((old_sources_doc or {}).get("sources") or {})
        nodes = {
            node["URI"]: dict(node)
            for node in (old_graph or {}).get("Nodes", [])
            if _contains(to, node.get("URI", ""))
        }
        nodes[to] = {"URI": to, "Name": _name(to), "NodeType": "directory"}

        changes = await self._fs.diff_paths(dirs=[to], base=base, head=target, ctx=ctx)
        deleted_sources = {
            uri: sources.get(uri)
            for uri in (change["uri"] for change in changes if change["change_type"] == "deleted")
            if uri in sources
        }
        reusable = {
            source.get("content_id"): source
            for source in deleted_sources.values()
            if source and source.get("content_id")
        }

        for change in changes:
            uri, change_type = change["uri"], change["change_type"]
            if change_type == "deleted":
                nodes.pop(uri, None)
                sources.pop(uri, None)
                continue
            nodes[uri] = {"URI": uri, "Name": _name(uri), "NodeType": "file"}
            if not uri.lower().endswith(".md"):
                sources.pop(uri, None)
                continue
            blob = await self._fs.show_blob_raw(target, path=uri, ctx=ctx)
            content_id = str(blob["oid"])
            supplied = extracted.get(uri)
            if supplied and supplied.get("content_id") == content_id:
                sources[uri] = dict(supplied)
            elif content_id in reusable:
                sources[uri] = _rebase_relation_source(reusable[content_id], uri, to)
            else:
                raw = blob["bytes"]
                content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                sources[uri] = extract_relation_source(uri, content, content_id, to)

        for uri in list(nodes):
            parent = _parent(uri)
            while parent is not None and _contains(to, parent):
                nodes.setdefault(
                    parent,
                    {"URI": parent, "Name": _name(parent), "NodeType": "directory"},
                )
                if parent == to:
                    break
                parent = _parent(parent)

        required_directories = {to}
        for uri in nodes:
            parent = _parent(uri)
            if parent is not None and _contains(to, parent):
                required_directories.add(parent)
        nodes = {
            uri: node
            for uri, node in nodes.items()
            if node["NodeType"] == "file" or uri in required_directories
        }
        edges = self._assemble_edges(nodes, sources)
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        graph = {
            "To": to,
            "SnapshotID": target,
            "GeneratedAt": generated_at,
            "Nodes": [nodes[uri] for uri in sorted(nodes)],
            "Edges": edges,
        }
        relation_sources = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": target,
            "parser_version": PARSER_VERSION,
            "sources": {uri: sources[uri] for uri in sorted(sources) if uri in nodes},
        }
        return graph, relation_sources

    @staticmethod
    def _assemble_edges(
        nodes: Mapping[str, Mapping[str, Any]], sources: Mapping[str, Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        edges = []
        for target_uri in sorted(nodes):
            parent = _parent(target_uri)
            if parent in nodes:
                edges.append(
                    {
                        "ID": _edge_id("contains", parent, target_uri),
                        "SourceURI": parent,
                        "TargetURI": target_uri,
                        "RelationType": "contains",
                        "Anchors": [],
                    }
                )
        references: dict[tuple[str, str], list[str]] = {}
        for source_uri, source in sources.items():
            if source_uri not in nodes:
                continue
            for link in source.get("links", []):
                target_uri = link.get("target_uri")
                if target_uri not in nodes:
                    continue
                anchors = references.setdefault((source_uri, target_uri), [])
                anchor = str(link.get("anchor") or "")
                if anchor and anchor not in anchors:
                    anchors.append(anchor)
        for (source_uri, target_uri), anchors in sorted(references.items()):
            edges.append(
                {
                    "ID": _edge_id("references", source_uri, target_uri),
                    "SourceURI": source_uri,
                    "TargetURI": target_uri,
                    "RelationType": "references",
                    "Anchors": anchors,
                }
            )
        return edges

    async def _read_graph(self, account_id: str, to: str) -> dict[str, Any] | None:
        return await self._store.read_json(self._store.artifact_relations_path(account_id, to))

    async def _filter_visible(
        self, graph: Mapping[str, Any], ctx: RequestContext
    ) -> dict[str, Any]:
        uris = [node["URI"] for node in graph.get("Nodes", [])]
        if self._fs_service is not None:
            visibility = await self._fs_service.can_read_many(uris, ctx)
        else:
            visibility = await self._fs._can_access_many(uris, ctx)
        visible = {uri for uri in uris if visibility.get(uri, False)}
        return {
            "To": graph["To"],
            "SnapshotID": graph["SnapshotID"],
            "GeneratedAt": graph["GeneratedAt"],
            "Nodes": [node for node in graph.get("Nodes", []) if node["URI"] in visible],
            "Edges": [
                edge
                for edge in graph.get("Edges", [])
                if edge["SourceURI"] in visible and edge["TargetURI"] in visible
            ],
        }
