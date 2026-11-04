# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persisted Artifact Relations contracts; no storage or lifecycle side effects."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1


class RelationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blob_oid: str = Field(min_length=1)
    links: list[str]


class RelationSources(_Record):
    """Only a complete, published baseline has a non-null build_id.

    Compile extraction invalidates this stamp before merging successful pages.
    An empty links list is a parsed page; a missing page has not been parsed.
    """

    parser_version: str = Field(min_length=1)
    build_id: str | None = Field(default=None, min_length=1)
    compile_operation_id: str | None = Field(default=None, min_length=1)
    pages: dict[str, RelationSource] = Field(default_factory=dict)


class RelationEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_uri: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    type: Literal["contains", "references"]


class ArtifactRelations(_Record):
    """Immutable build payload. Directory node URIs end with a slash."""

    build_id: str = Field(min_length=1)
    target_snapshot_id: str = Field(min_length=1)
    generated_at: float = Field(ge=0, allow_inf_nan=False)
    nodes: list[str]
    edges: list[RelationEdge]


class ArtifactRelationsState(_Record):
    """The requested build and its durable follow-up notification.

    target_snapshot_id is fixed for this build, never replaced by a newer edit.
    pending records work arriving during this build; its version is wiki_head.
    Compile pins its snapshot here and waits by operation ID plus snapshot ID.
    """

    status: Literal["building", "ready", "failed"]
    target_generation: str = Field(default="legacy", min_length=1)
    target_snapshot_id: str = Field(min_length=1)
    build_id: str | None = Field(default=None, min_length=1)
    phase: Literal["queued", "computing", "publishing"] | None = None
    compile_operation_id: str | None = Field(default=None, min_length=1)
    attempts: int = Field(default=0, ge=0)
    pending: bool = False
    error: str | None = None
    error_code: str | None = None
    # Wall-clock request time survives retries/restarts; None is a legacy record.
    requested_at: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    published_build_id: str | None = Field(default=None, min_length=1)
    revision: str | None = Field(default=None, min_length=1)


def can_use_incremental_sources(
    sources: RelationSources | None,
    graph: ArtifactRelations | None,
    *,
    parser_version: str,
) -> bool:
    """Check baseline identity only; the builder must also validate page coverage."""
    return (
        sources is not None
        and graph is not None
        and sources.build_id is not None
        and sources.build_id == graph.build_id
        and sources.parser_version == parser_version
    )


def can_recover_publication(
    state: ArtifactRelationsState,
    graph: ArtifactRelations | None,
) -> bool:
    """A matching whole graph permits completing an interrupted publication.

    Call only with a coherent read under the publication lock. This predicate
    does not commit state, authorize a worker, or validate graph structure.
    The store must CAS the state before exposing this candidate as published.
    """
    return (
        state.status == "building"
        and state.phase == "publishing"
        and state.build_id is not None
        and graph is not None
        and state.build_id == graph.build_id
        and state.target_snapshot_id == graph.target_snapshot_id
    )
