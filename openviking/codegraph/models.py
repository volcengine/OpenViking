# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Data models for the local CodeGraph prototype."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional

GraphNodeKind = Literal["file", "class", "function", "method"]
GraphEdgeKind = Literal["defines", "calls"]
EdgeResolution = Literal["resolved", "best_effort", "ambiguous", "unresolved"]


def stable_file_key(account_id: str, repo_id: str, canonical_uri: str) -> str:
    """Return the stable ACL identity for a source path."""
    if not account_id or not repo_id or not canonical_uri:
        raise ValueError("account_id, repo_id, and canonical_uri are required")
    payload = "\0".join(("file-v1", account_id, repo_id, canonical_uri))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FileAccessScope:
    """Current authorization result passed to a CodeGraph query."""

    account_id: str
    repo_id: str
    acl_revision: int
    allowed_file_keys: frozenset[str]
    allow_all: bool = False

    def __post_init__(self) -> None:
        if not self.account_id or not self.repo_id:
            raise ValueError("account_id and repo_id are required")
        if self.acl_revision < 0:
            raise ValueError("acl_revision must be non-negative")
        if self.allow_all and self.allowed_file_keys:
            raise ValueError("allow_all cannot be combined with allowed_file_keys")

    @classmethod
    def restricted(
        cls,
        *,
        account_id: str,
        repo_id: str,
        acl_revision: int,
        allowed_file_keys: Iterable[str],
    ) -> "FileAccessScope":
        return cls(
            account_id=account_id,
            repo_id=repo_id,
            acl_revision=acl_revision,
            allowed_file_keys=frozenset(allowed_file_keys),
        )

    @classmethod
    def unrestricted(
        cls,
        *,
        account_id: str,
        repo_id: str,
        acl_revision: int,
    ) -> "FileAccessScope":
        return cls(
            account_id=account_id,
            repo_id=repo_id,
            acl_revision=acl_revision,
            allowed_file_keys=frozenset(),
            allow_all=True,
        )


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
    account_id: str
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
    file_key: str
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
    account_id: str
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
