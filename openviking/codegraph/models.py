# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Data models for the local CodeGraph prototype."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

GraphNodeKind = Literal["file", "class", "function", "method"]
GraphEdgeKind = Literal["defines", "calls"]
EdgeResolution = Literal["resolved", "best_effort", "ambiguous", "unresolved"]


@dataclass(frozen=True)
class SourceFile:
    """One source file in the immutable input snapshot."""

    relative_path: str
    uri: str
    content: str
    source_blob_oid: Optional[str] = None


@dataclass(frozen=True)
class SymbolNode:
    node_id: str
    repo_id: str
    graph_generation: str
    file_id: int
    file_uri: str
    file_path: str
    language: str
    kind: GraphNodeKind
    name: str
    qualified_name: str
    canonical_signature: str
    declaration_ordinal: int
    start_line: int
    end_line: int
    source_blob_oid: str
    content_hash: str
    snippet: str


@dataclass(frozen=True)
class GraphEdge:
    source_id: str
    target_id: Optional[str]
    target_name: str
    kind: GraphEdgeKind
    resolution: EdgeResolution
    confidence: float
    evidence_file_id: int
    evidence_line: int


@dataclass(frozen=True)
class PendingCall:
    source_id: str
    source_qualified_name: str
    source_file_id: int
    source_file_path: str
    containing_class: Optional[str]
    target_name: str
    receiver: Optional[str]
    evidence_line: int


@dataclass(frozen=True)
class ExtractedFile:
    file_node: SymbolNode
    nodes: tuple[SymbolNode, ...]
    edges: tuple[GraphEdge, ...]
    pending_calls: tuple[PendingCall, ...]


@dataclass(frozen=True)
class GraphManifest:
    repo_id: str
    revision_id: str
    commit_sha: str
    source_snapshot_oid: str
    graph_generation: str
    graph_id_schema_version: int
    index_path: Path
    file_count: int
    node_count: int
    edge_count: int


@dataclass(frozen=True)
class CodeGraphHit:
    node_id: str
    file_id: int
    file_uri: str
    file_path: str
    kind: str
    name: str
    qualified_name: str
    canonical_signature: str
    declaration_ordinal: int
    start_line: int
    end_line: int
    source_blob_oid: str
    snippet: str
    score: float


@dataclass(frozen=True)
class GraphExpansion:
    nodes: tuple[CodeGraphHit, ...]
    edges: tuple[GraphEdge, ...]
    truncated: bool


@dataclass(frozen=True)
class RevisionRef:
    repo_id: str
    revision_id: str
    commit_sha: str
    source_snapshot_oid: str
    graph_generation: str
    index_path: Path
    published_epoch: int


@dataclass(frozen=True)
class ReadView:
    """Prototype read view. Production tokens also need scope, signature, and lease."""

    account_id: str
    epoch: int
