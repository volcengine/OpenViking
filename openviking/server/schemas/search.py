# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/search endpoints."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SearchHit(BaseModel):
    """Single hit in a search result category (memories / resources / skills).

    ``extra='allow'`` preserves hit-level fields added by future retriever
    upgrades (e.g. new scoring signals or provenance annotations).
    """

    model_config = ConfigDict(extra="allow")

    context_type: Optional[str] = None
    uri: Optional[str] = None
    level: Optional[int] = None
    score: Optional[float] = None
    category: Optional[str] = None
    match_reason: Optional[str] = None
    relations: Optional[List[Dict[str, Any]]] = None
    abstract: Optional[str] = None
    overview: Optional[str] = None
    tags: Optional[List[str]] = None


class QueryPlanItem(BaseModel):
    """Single rewritten / expanded query inside a ``QueryPlan``."""

    model_config = ConfigDict(extra="allow")

    query: str
    context_type: Optional[str] = None
    intent: Optional[str] = None
    priority: Optional[int] = None


class QueryPlan(BaseModel):
    """LLM-produced retrieval plan wrapping ``queries``."""

    model_config = ConfigDict(extra="allow")

    reasoning: Optional[str] = None
    queries: List[QueryPlanItem] = Field(default_factory=list)


class SearchResult(BaseModel):
    """Result of ``POST /find`` and ``POST /search`` (``FindResult.to_dict()``).

    ``provenance`` is populated only when the request sets
    ``include_provenance=True``; otherwise it is absent.
    """

    model_config = ConfigDict(extra="allow")

    memories: List[SearchHit] = Field(default_factory=list)
    resources: List[SearchHit] = Field(default_factory=list)
    skills: List[SearchHit] = Field(default_factory=list)
    total: int = 0
    query_plan: Optional[QueryPlan] = None
    provenance: Optional[List[Dict[str, Any]]] = None


class GrepResult(BaseModel):
    """Result of ``POST /api/v1/search/grep``.

    The grep payload shape is defined by AGFS and carries nested match
    entries with line context / byte offsets. Kept loose to avoid
    over-specifying the AGFS contract from the Python side.
    """

    model_config = ConfigDict(extra="allow")


class GlobResult(BaseModel):
    """Result of ``POST /api/v1/search/glob``."""

    model_config = ConfigDict(extra="allow")

    matches: List[str] = Field(default_factory=list)
    count: Optional[int] = None
