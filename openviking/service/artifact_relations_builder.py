# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Build relations from immutable snapshot structure and raw Markdown sources."""

from __future__ import annotations

import posixpath
import time
from dataclasses import dataclass

from openviking.service.artifact_relation_links import (
    PARSER_VERSION,
    extract_relation_source,
    resolve_relation_link,
)
from openviking.service.artifact_relations_models import (
    ArtifactRelations,
    ArtifactRelationsState,
    RelationEdge,
    RelationSources,
    can_use_incremental_sources,
)
from openviking.utils.path_safety import normalize_storage_target_uri


@dataclass(frozen=True)
class SnapshotEntry:
    uri: str
    oid: str
    is_dir: bool


def assemble_relations(
    to: str,
    entries: list[SnapshotEntry],
    sources: RelationSources,
    *,
    snapshot_id: str,
    build_id: str,
    previous: ArtifactRelations | None = None,
    changed_pages: set[str] | None = None,
) -> ArtifactRelations:
    """Deterministically assemble direct containment and resolved references.

    Resolve every raw destination against the current structure: unchanged
    sources can gain/lose edges when a destination is created/deleted/moved.
    """
    root = normalize_storage_target_uri(to)
    nodes: dict[str, str] = {}
    pages = set()
    for entry in entries:
        uri = normalize_storage_target_uri(entry.uri)
        if uri != root and not uri.startswith(root + "/"):
            raise ValueError(f"Snapshot entry outside target: {uri}")
        node = uri + "/" if entry.is_dir else uri
        if uri in nodes:
            raise ValueError(f"Duplicate snapshot entry: {uri}")
        nodes[uri] = node
        if not entry.is_dir and uri.lower().endswith(".md"):
            pages.add(uri)
    if nodes.get(root) != root + "/":
        raise ValueError("Snapshot target directory is missing")
    if set(sources.pages) != pages:
        raise ValueError("Sources do not cover exactly the snapshot Markdown pages")
    changed_pages = changed_pages if changed_pages is not None else set(pages)
    old_nodes = {uri.rstrip("/") for uri in previous.nodes} if previous else set()
    changed_nodes = old_nodes ^ nodes.keys()
    # Carry unaffected reference edges forward. Targets disappearing from the
    # structure remove incoming edges even when their source page is unchanged.
    edges = {
        (edge.from_uri, edge.to, edge.type)
        for edge in (previous.edges if previous else [])
        if edge.type == "references"
        and edge.from_uri in nodes
        and edge.from_uri not in changed_pages
        and edge.to.rstrip("/") in nodes
        and edge.to.rstrip("/") not in changed_nodes
    }
    for uri, node in nodes.items():
        if uri != root:
            parent = posixpath.dirname(uri)
            if nodes.get(parent) != parent + "/":
                raise ValueError(f"Snapshot parent directory is missing: {parent}")
            edges.add((nodes[parent], node, "contains"))
    for uri, source in sources.pages.items():
        for href in source.links:
            target = resolve_relation_link(uri, href, target_root=root)
            if previous and uri not in changed_pages and target not in changed_nodes:
                continue
            if target in nodes:
                edges.add((nodes[uri], nodes[target], "references"))
    return ArtifactRelations(
        build_id=build_id,
        target_snapshot_id=snapshot_id,
        generated_at=time.time(),
        nodes=sorted(nodes.values()),
        edges=[RelationEdge(from_uri=a, to=b, type=kind) for a, b, kind in sorted(edges)],
    )


class ArtifactRelationsBuilder:
    def __init__(self, fs) -> None:
        self._fs = fs

    async def build(
        self,
        to: str,
        claimed: ArtifactRelationsState,
        sources: RelationSources | None,
        previous: ArtifactRelations | None,
        ctx,
    ) -> tuple[RelationSources, ArtifactRelations]:
        if claimed.build_id is None:
            raise ValueError("Build must be claimed before computing")
        path = self._fs._uri_to_tree_path(to, ctx=ctx)
        manifest = await self._fs._async_agfs.run(
            "git_snapshot_entries",
            account=ctx.account_id,
            snapshot=claimed.target_snapshot_id,
            path=path,
        )
        entries = [
            SnapshotEntry(self._fs._tree_path_to_uri(path), oid, is_dir)
            for path, oid, is_dir in manifest
        ]
        markdown = {
            entry.uri: entry
            for entry in entries
            if not entry.is_dir and entry.uri.lower().endswith(".md")
        }
        incremental = can_use_incremental_sources(sources, previous, parser_version=PARSER_VERSION)
        pages = {}
        changed_pages = set(markdown)
        if incremental:
            changes = await self._fs.diff_paths(
                dirs=[to],
                base=previous.target_snapshot_id,
                head=claimed.target_snapshot_id,
                ctx=ctx,
            )
            changed = {change["uri"] for change in changes}
            pages = {
                uri: source
                for uri, source in sources.pages.items()
                if uri in markdown and uri not in changed
            }
            # Missing/inconsistent records invalidate the whole incremental base.
            if any(source.blob_oid != markdown[uri].oid for uri, source in pages.items()):
                pages = {}
            else:
                changed_pages = set(markdown) - pages.keys()
        elif claimed.compile_operation_id and sources and sources.parser_version == PARSER_VERSION:
            # Compile batches extracted their final bytes before snapshot commit.
            pages = {
                uri: source
                for uri, source in sources.pages.items()
                if uri in markdown and source.blob_oid == markdown[uri].oid
            }
        missing = [entry for uri, entry in sorted(markdown.items()) if uri not in pages]
        # Bound native/Python transient body memory to one batch, reuse equal blobs.
        for offset in range(0, len(missing), 32):
            batch = missing[offset : offset + 32]
            oids = list(dict.fromkeys(entry.oid for entry in batch))
            bodies = await self._fs._async_agfs.run(
                "git_snapshot_blobs", account=ctx.account_id, oids=oids
            )
            if len(bodies) != len(oids):
                raise ValueError("Incomplete snapshot blob batch")
            extracted = {
                oid: extract_relation_source(body) for oid, body in zip(oids, bodies, strict=True)
            }
            for entry in batch:
                source = extracted[entry.oid]
                if source.blob_oid != entry.oid:
                    raise ValueError("Snapshot blob content does not match its OID")
                pages[entry.uri] = source
        result = RelationSources(
            parser_version=PARSER_VERSION, build_id=claimed.build_id, pages=pages
        )
        graph = assemble_relations(
            to,
            entries,
            result,
            snapshot_id=claimed.target_snapshot_id,
            build_id=claimed.build_id,
            previous=previous if incremental else None,
            changed_pages=changed_pages,
        )
        return result, graph
