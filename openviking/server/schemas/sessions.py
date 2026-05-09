# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/sessions endpoints."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class UserInfo(BaseModel):
    """User identifier tuple emitted by ``UserIdentifier.to_dict()``."""

    account_id: str
    user_id: str
    agent_id: str


class MemoryExtractionCounts(BaseModel):
    """Per-category memory extraction counters from ``SessionMeta``."""

    profile: int = 0
    preferences: int = 0
    entities: int = 0
    events: int = 0
    cases: int = 0
    patterns: int = 0
    tools: int = 0
    skills: int = 0
    total: int = 0


class LLMTokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class EmbeddingTokenUsage(BaseModel):
    total_tokens: int = 0


class SessionMeta(BaseModel):
    """Session metadata (mirrors ``SessionMeta.to_dict()``).

    ``extra='allow'`` preserves any historical field that is not yet modeled
    here — silent field drop would be a wire-format regression.
    """

    model_config = ConfigDict(extra="allow")

    session_id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0
    commit_count: int = 0
    memories_extracted: Optional[MemoryExtractionCounts] = None
    last_commit_at: Optional[str] = None
    llm_token_usage: Optional[LLMTokenUsage] = None
    embedding_token_usage: Optional[EmbeddingTokenUsage] = None


class SessionCreatedResult(BaseModel):
    """Result payload of ``POST /api/v1/sessions``."""

    session_id: str
    user: UserInfo


class SessionListItem(BaseModel):
    """Item shape of ``GET /api/v1/sessions`` list entries."""

    session_id: str
    uri: str
    is_dir: bool


class SessionDetail(SessionMeta):
    """Result payload of ``GET /api/v1/sessions/{session_id}``.

    Extends :class:`SessionMeta` with live per-request fields.
    """

    user: UserInfo
    pending_tokens: int = 0


class ArchiveAbstract(BaseModel):
    """Pre-archive abstract entry inside session context."""

    archive_id: str
    abstract: str


class ContextStats(BaseModel):
    """Aggregated archive stats produced by ``get_session_context``.

    Field names mirror the existing JSON shape (camelCase) to preserve
    backward compatibility.
    """

    totalArchives: int = 0
    includedArchives: int = 0
    droppedArchives: int = 0
    failedArchives: int = 0
    activeTokens: int = 0
    archiveTokens: int = 0


class SessionContextResult(BaseModel):
    """Result of ``GET /api/v1/sessions/{session_id}/context``.

    ``messages`` is kept as a permissive ``Dict[str, Any]`` list: each item is
    the output of ``Message.to_dict()`` which is itself composed from
    heterogeneous part types; tightening it is a follow-up per the
    API schema guidelines (start loose, narrow later). ``extra='allow'``
    protects against silent drop of future fields added server-side.
    """

    model_config = ConfigDict(extra="allow")

    latest_archive_overview: Optional[str] = None
    pre_archive_abstracts: List[ArchiveAbstract] = Field(default_factory=list)
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    estimatedTokens: int = 0
    stats: Optional[ContextStats] = None


class SessionArchiveDetail(BaseModel):
    """Result of ``GET /api/v1/sessions/{session_id}/archives/{archive_id}``.

    ``extra='allow'`` preserves any archive field not yet modeled.
    """

    model_config = ConfigDict(extra="allow")

    archive_id: str
    abstract: str
    overview: str
    messages: List[Dict[str, Any]] = Field(default_factory=list)


class SessionDeletedResult(BaseModel):
    """Result payload of ``DELETE /api/v1/sessions/{session_id}``."""

    session_id: str


class CommitResult(BaseModel):
    """Result payload of ``POST /api/v1/sessions/{session_id}/commit``.

    ``task_id`` is ``None`` when the session has no messages to archive;
    callers should branch on this rather than polling. ``extra='allow'``
    preserves any commit-service field not yet modeled.
    """

    model_config = ConfigDict(extra="allow")

    session_id: str
    status: str
    task_id: Optional[str] = None
    archive_uri: Optional[str] = None
    archived: bool = False
    trace_id: Optional[str] = None


class ContextItem(BaseModel):
    """A ``Context.to_dict()`` projection emitted by the extract endpoint.

    Field set is the union across ``context_type`` values (memory / skill /
    resource); the loose typing mirrors the historical dict shape.
    ``extra='allow'`` preserves context fields not yet modeled here (the
    ``Context`` class grows field-by-field over time).
    """

    model_config = ConfigDict(extra="allow")

    id: str
    uri: str
    parent_uri: Optional[str] = None
    temp_uri: Optional[str] = None
    is_leaf: Optional[bool] = None
    abstract: Optional[str] = None
    context_type: Optional[str] = None
    category: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    active_count: Optional[int] = None
    vector: Optional[List[float]] = None
    meta: Optional[Dict[str, Any]] = None
    related_uri: List[str] = Field(default_factory=list)
    session_id: Optional[str] = None
    account_id: Optional[str] = None
    owner_space: Optional[str] = None
    user: Optional[UserInfo] = None
    level: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class MessageAddedResult(BaseModel):
    """Result payload of ``POST /api/v1/sessions/{session_id}/messages``."""

    session_id: str
    message_count: int


class UsageRecordedResult(BaseModel):
    """Result payload of ``POST /api/v1/sessions/{session_id}/used``."""

    session_id: str
    contexts_used: int
    skills_used: int
