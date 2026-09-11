# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Build and query sealed SQLite CodeGraph generations."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable, Optional, Sequence
from urllib.parse import quote

from openviking.codegraph.models import (
    CodeGraphHit,
    GraphEdge,
    GraphExpansion,
    GraphManifest,
    PendingCall,
    SourceFile,
    SymbolNode,
)
from openviking.codegraph.python_extractor import (
    GRAPH_ID_SCHEMA_VERSION,
    extract_python_file,
)

SCHEMA_VERSION = 2
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE files (
    file_id INTEGER PRIMARY KEY,
    file_uri TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL UNIQUE,
    source_blob_oid TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE nodes (
    ordinal INTEGER PRIMARY KEY,
    node_id TEXT NOT NULL UNIQUE,
    file_id INTEGER NOT NULL REFERENCES files(file_id),
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    canonical_signature TEXT NOT NULL,
    declaration_ordinal INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source_blob_oid TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    snippet TEXT NOT NULL
);

CREATE INDEX nodes_file_id_idx ON nodes(file_id);
CREATE INDEX nodes_name_idx ON nodes(name);
CREATE INDEX nodes_qualified_name_idx ON nodes(qualified_name);

CREATE TABLE edges (
    ordinal INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES nodes(node_id),
    target_id TEXT REFERENCES nodes(node_id),
    target_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    resolution TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_file_id INTEGER NOT NULL REFERENCES files(file_id),
    evidence_line INTEGER NOT NULL
);

CREATE INDEX edges_source_idx ON edges(source_id, kind);
CREATE INDEX edges_target_idx ON edges(target_id, kind);

CREATE VIRTUAL TABLE node_fts USING fts5(
    name,
    qualified_name,
    file_path,
    signature,
    snippet,
    tokenize = 'unicode61'
);
"""


class InvalidCodeGraphError(RuntimeError):
    """Raised when a sealed graph does not match the expected schema."""


def _normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    if not normalized or normalized.startswith("../") or "/../" in normalized:
        raise ValueError(f"invalid relative source path: {value!r}")
    return normalized


def _resolve_pending_calls(
    nodes: Sequence[SymbolNode],
    calls: Sequence[PendingCall],
) -> list[GraphEdge]:
    callable_nodes = [node for node in nodes if node.kind in {"function", "method"}]
    by_qualified: dict[str, list[SymbolNode]] = defaultdict(list)
    by_name: dict[str, list[SymbolNode]] = defaultdict(list)
    by_file_and_name: dict[tuple[str, str], list[SymbolNode]] = defaultdict(list)
    by_file_and_qualified: dict[tuple[str, str], list[SymbolNode]] = defaultdict(list)
    for node in callable_nodes:
        by_qualified[node.qualified_name].append(node)
        by_name[node.name].append(node)
        by_file_and_name[(node.file_path, node.name)].append(node)
        by_file_and_qualified[(node.file_path, node.qualified_name)].append(node)

    edges: list[GraphEdge] = []
    for call in calls:
        candidates: list[SymbolNode] = []
        exact = False
        if call.receiver in {"self", "cls"} and call.containing_class:
            candidates = by_file_and_qualified.get(
                (
                    call.source_file_path,
                    f"{call.containing_class}.{call.target_name}",
                ),
                [],
            )
            exact = bool(candidates)
        elif call.receiver:
            suffix = f"{call.receiver}.{call.target_name}"
            local = [
                node
                for node in callable_nodes
                if node.file_path == call.source_file_path
                and (node.qualified_name == suffix or node.qualified_name.endswith(f".{suffix}"))
            ]
            if local:
                candidates = local
                exact = True
            else:
                candidates = [
                    node
                    for node in callable_nodes
                    if node.qualified_name == suffix or node.qualified_name.endswith(f".{suffix}")
                ]
        else:
            source_scope = call.source_qualified_name.split(".")[:-1]
            for depth in range(len(source_scope), -1, -1):
                candidate_name = ".".join((*source_scope[:depth], call.target_name))
                scoped = by_file_and_qualified.get(
                    (call.source_file_path, candidate_name),
                    [],
                )
                if scoped:
                    candidates = scoped
                    exact = True
                    break
            if not candidates:
                candidates = by_file_and_name.get(
                    (call.source_file_path, call.target_name),
                    [],
                )
                exact = bool(candidates)
            if not candidates:
                candidates = by_name.get(call.target_name, [])

        unique = {candidate.node_id: candidate for candidate in candidates}
        resolved = list(unique.values())
        if len(resolved) == 1:
            target_id = resolved[0].node_id
            resolution = "resolved" if exact else "best_effort"
            confidence = 1.0 if exact else 0.6
        elif resolved:
            target_id = None
            resolution = "ambiguous"
            confidence = 0.5
        else:
            target_id = None
            resolution = "unresolved"
            confidence = 0.0

        edges.append(
            GraphEdge(
                source_id=call.source_id,
                target_id=target_id,
                target_name=call.target_name,
                kind="calls",
                resolution=resolution,
                confidence=confidence,
                evidence_file_id=call.source_file_id,
                evidence_line=call.evidence_line,
            )
        )
    return edges


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class CodeGraphBuilder:
    """Build an immutable graph generation without changing a published catalog."""

    def build(
        self,
        *,
        repo_id: str,
        revision_id: str,
        commit_sha: str,
        source_snapshot_oid: str,
        graph_generation: str,
        source_files: Sequence[SourceFile],
        output_path: Path,
    ) -> GraphManifest:
        if not repo_id or not revision_id or not graph_generation:
            raise ValueError("repo_id, revision_id, and graph_generation are required")
        if output_path.exists():
            raise FileExistsError(f"graph generation already exists: {output_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        normalized_files = sorted(
            (
                SourceFile(
                    relative_path=_normalize_relative_path(source.relative_path),
                    uri=source.uri,
                    content=source.content,
                    source_blob_oid=source.source_blob_oid,
                )
                for source in source_files
                if source.relative_path.lower().endswith(".py")
            ),
            key=lambda source: source.relative_path,
        )
        if not normalized_files:
            raise ValueError("the prototype requires at least one Python source file")
        paths = [source.relative_path for source in normalized_files]
        if len(paths) != len(set(paths)):
            raise ValueError("source file paths must be unique")

        all_nodes: list[SymbolNode] = []
        all_edges: list[GraphEdge] = []
        all_calls: list[PendingCall] = []
        try:
            for file_id, source_file in enumerate(normalized_files, start=1):
                extracted = extract_python_file(
                    repo_id=repo_id,
                    graph_generation=graph_generation,
                    file_id=file_id,
                    source_file=source_file,
                )
                all_nodes.extend(extracted.nodes)
                all_edges.extend(extracted.edges)
                all_calls.extend(extracted.pending_calls)
            all_edges.extend(_resolve_pending_calls(all_nodes, all_calls))
            self._write_database(
                temp_path,
                repo_id=repo_id,
                revision_id=revision_id,
                commit_sha=commit_sha,
                source_snapshot_oid=source_snapshot_oid,
                graph_generation=graph_generation,
                source_files=normalized_files,
                nodes=all_nodes,
                edges=all_edges,
            )
            _fsync_file(temp_path)
            try:
                os.link(temp_path, output_path)
            except FileExistsError:
                raise FileExistsError(f"graph generation already exists: {output_path}") from None
            temp_path.unlink()
            _fsync_directory(output_path.parent)
        finally:
            temp_path.unlink(missing_ok=True)

        return GraphManifest(
            repo_id=repo_id,
            revision_id=revision_id,
            commit_sha=commit_sha,
            source_snapshot_oid=source_snapshot_oid,
            graph_generation=graph_generation,
            graph_id_schema_version=GRAPH_ID_SCHEMA_VERSION,
            index_path=output_path,
            file_count=len(normalized_files),
            node_count=len(all_nodes),
            edge_count=len(all_edges),
        )

    @staticmethod
    def _write_database(
        path: Path,
        *,
        repo_id: str,
        revision_id: str,
        commit_sha: str,
        source_snapshot_oid: str,
        graph_generation: str,
        source_files: Sequence[SourceFile],
        nodes: Sequence[SymbolNode],
        edges: Sequence[GraphEdge],
    ) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            metadata = {
                "schema_version": str(SCHEMA_VERSION),
                "graph_id_schema_version": str(GRAPH_ID_SCHEMA_VERSION),
                "repo_id": repo_id,
                "revision_id": revision_id,
                "commit_sha": commit_sha,
                "source_snapshot_oid": source_snapshot_oid,
                "graph_generation": graph_generation,
            }
            conn.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                metadata.items(),
            )

            node_by_file = {node.file_path: node for node in nodes if node.kind == "file"}
            conn.executemany(
                """
                INSERT INTO files(
                    file_id, file_uri, file_path, source_blob_oid, content_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        file_id,
                        source.uri,
                        source.relative_path,
                        node_by_file[source.relative_path].source_blob_oid,
                        hashlib.sha256(source.content.encode("utf-8")).hexdigest(),
                    )
                    for file_id, source in enumerate(source_files, start=1)
                ],
            )

            for ordinal, node in enumerate(nodes, start=1):
                conn.execute(
                    """
                    INSERT INTO nodes(
                        ordinal, node_id, file_id, kind, name, qualified_name,
                        canonical_signature, declaration_ordinal, start_line, end_line,
                        source_blob_oid, content_hash, snippet
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ordinal,
                        node.node_id,
                        node.file_id,
                        node.kind,
                        node.name,
                        node.qualified_name,
                        node.canonical_signature,
                        node.declaration_ordinal,
                        node.start_line,
                        node.end_line,
                        node.source_blob_oid,
                        node.content_hash,
                        node.snippet,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO node_fts(
                        rowid, name, qualified_name, file_path, signature, snippet
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ordinal,
                        node.name,
                        node.qualified_name,
                        node.file_path,
                        node.canonical_signature,
                        node.snippet,
                    ),
                )

            conn.executemany(
                """
                INSERT INTO edges(
                    source_id, target_id, target_name, kind, resolution,
                    confidence, evidence_file_id, evidence_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        edge.source_id,
                        edge.target_id,
                        edge.target_name,
                        edge.kind,
                        edge.resolution,
                        edge.confidence,
                        edge.evidence_file_id,
                        edge.evidence_line,
                    )
                    for edge in edges
                ],
            )
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise InvalidCodeGraphError(f"SQLite integrity check failed: {integrity!r}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _fts_query(value: str) -> Optional[str]:
    terms = list(dict.fromkeys(_TOKEN_RE.findall(value)))
    if not terms:
        return None
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


class CodeGraphIndex:
    """Read-only query interface for one sealed graph generation."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self._metadata = self._read_metadata()

    @property
    def metadata(self) -> dict[str, str]:
        return dict(self._metadata)

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{quote(self.path.as_posix(), safe='/')}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _read_metadata(self) -> dict[str, str]:
        conn = self._connect()
        try:
            metadata = {
                str(row["key"]): str(row["value"])
                for row in conn.execute("SELECT key, value FROM metadata")
            }
        except sqlite3.DatabaseError as exc:
            raise InvalidCodeGraphError(f"invalid CodeGraph database: {self.path}") from exc
        finally:
            conn.close()
        if metadata.get("schema_version") != str(SCHEMA_VERSION):
            raise InvalidCodeGraphError(
                f"unsupported CodeGraph schema: {metadata.get('schema_version')!r}"
            )
        return metadata

    @staticmethod
    def _register_acl_filter(
        conn: sqlite3.Connection,
        allowed_file_ids: Optional[Iterable[int]],
    ) -> None:
        allowed = None if allowed_file_ids is None else frozenset(allowed_file_ids)
        conn.create_function(
            "ov_file_allowed",
            1,
            lambda file_id: 1 if allowed is None or int(file_id) in allowed else 0,
            deterministic=True,
        )

    @staticmethod
    def _hit_from_row(row: sqlite3.Row, *, score: float = 0.0) -> CodeGraphHit:
        return CodeGraphHit(
            node_id=str(row["node_id"]),
            file_id=int(row["file_id"]),
            file_uri=str(row["file_uri"]),
            file_path=str(row["file_path"]),
            kind=str(row["kind"]),
            name=str(row["name"]),
            qualified_name=str(row["qualified_name"]),
            canonical_signature=str(row["canonical_signature"]),
            declaration_ordinal=int(row["declaration_ordinal"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            source_blob_oid=str(row["source_blob_oid"]),
            snippet=str(row["snippet"]),
            score=score,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        allowed_file_ids: Optional[Iterable[int]] = None,
    ) -> list[CodeGraphHit]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        match = _fts_query(query)
        if match is None:
            return []

        conn = self._connect()
        try:
            self._register_acl_filter(conn, allowed_file_ids)
            rows = conn.execute(
                """
                SELECT
                    n.*, f.file_uri, f.file_path,
                    bm25(node_fts, 5.0, 4.0, 2.0, 2.0, 1.0) AS rank
                FROM node_fts
                JOIN nodes n ON n.ordinal = node_fts.rowid
                JOIN files f ON f.file_id = n.file_id
                WHERE node_fts MATCH ?
                  AND ov_file_allowed(n.file_id) = 1
                ORDER BY rank, n.qualified_name
                LIMIT ? OFFSET ?
                """,
                (match, limit, offset),
            ).fetchall()
            return [self._hit_from_row(row, score=-float(row["rank"])) for row in rows]
        finally:
            conn.close()

    def expand(
        self,
        seed_ids: Sequence[str],
        *,
        max_depth: int = 2,
        max_fanout: int = 20,
        max_nodes: int = 50,
        allowed_file_ids: Optional[Iterable[int]] = None,
    ) -> GraphExpansion:
        if max_depth < 0 or max_fanout <= 0 or max_nodes <= 0:
            raise ValueError("invalid graph traversal budget")
        if not seed_ids:
            return GraphExpansion(nodes=(), edges=(), truncated=False)

        conn = self._connect()
        try:
            self._register_acl_filter(conn, allowed_file_ids)
            nodes: dict[str, CodeGraphHit] = {}
            frontier: deque[tuple[str, int]] = deque()
            truncated = False

            for node_id in dict.fromkeys(seed_ids):
                row = conn.execute(
                    """
                    SELECT n.*, f.file_uri, f.file_path
                    FROM nodes n
                    JOIN files f ON f.file_id = n.file_id
                    WHERE n.node_id = ? AND ov_file_allowed(n.file_id) = 1
                    """,
                    (node_id,),
                ).fetchone()
                if row is None:
                    continue
                nodes[node_id] = self._hit_from_row(row)
                frontier.append((node_id, 0))
                if len(nodes) >= max_nodes:
                    truncated = len(seed_ids) > len(nodes)
                    break

            selected_edges: list[GraphEdge] = []
            expanded: set[str] = set()
            while frontier:
                source_id, depth = frontier.popleft()
                if source_id in expanded or depth >= max_depth:
                    continue
                expanded.add(source_id)
                rows = conn.execute(
                    """
                    SELECT
                        e.*,
                        target.file_id AS file_id,
                        target.node_id,
                        target.kind,
                        target.name,
                        target.qualified_name,
                        target.canonical_signature,
                        target.declaration_ordinal,
                        target.start_line,
                        target.end_line,
                        target.source_blob_oid,
                        target.content_hash,
                        target.snippet,
                        files.file_uri,
                        files.file_path
                    FROM edges e
                    JOIN nodes source ON source.node_id = e.source_id
                    JOIN nodes target ON target.node_id = e.target_id
                    JOIN files ON files.file_id = target.file_id
                    WHERE e.source_id = ?
                      AND ov_file_allowed(source.file_id) = 1
                      AND ov_file_allowed(target.file_id) = 1
                      AND ov_file_allowed(e.evidence_file_id) = 1
                    ORDER BY e.confidence DESC, e.kind, target.qualified_name
                    LIMIT ?
                    """,
                    (source_id, max_fanout + 1),
                ).fetchall()
                if len(rows) > max_fanout:
                    truncated = True
                    rows = rows[:max_fanout]

                for row in rows:
                    target_id = str(row["target_id"])
                    if target_id not in nodes and len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    selected_edges.append(
                        GraphEdge(
                            source_id=str(row["source_id"]),
                            target_id=target_id,
                            target_name=str(row["target_name"]),
                            kind=str(row["kind"]),
                            resolution=str(row["resolution"]),
                            confidence=float(row["confidence"]),
                            evidence_file_id=int(row["evidence_file_id"]),
                            evidence_line=int(row["evidence_line"]),
                        )
                    )
                    if target_id in nodes:
                        continue
                    nodes[target_id] = self._hit_from_row(row)
                    frontier.append((target_id, depth + 1))

            return GraphExpansion(
                nodes=tuple(nodes.values()),
                edges=tuple(selected_edges),
                truncated=truncated,
            )
        finally:
            conn.close()
