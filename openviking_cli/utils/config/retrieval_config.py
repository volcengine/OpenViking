# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pydantic import BaseModel, Field


class RelationExpansionConfig(BaseModel):
    """Opt-in single-hop expansion along explicit ``related`` links (issue #4645)."""

    enabled: bool = Field(
        default=False,
        description=(
            "Follow `related` viking:// links declared in the YAML frontmatter of the "
            "top-ranked file hits, and evaluate the linked resources as extra candidates."
        ),
    )
    max_seeds: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Top-ranked file hits that passed the score threshold whose links are read.",
    )
    max_edges: int = Field(
        default=16,
        ge=1,
        le=200,
        description="Total link entries examined across all seeds of one query.",
    )
    max_added: int = Field(
        default=3,
        ge=1,
        le=50,
        description="Linked resources that may be added to one query's results.",
    )


class RetrievalConfig(BaseModel):
    """Configuration for query planning and context assembly."""

    recall_intent_timeout_s: float = Field(
        default=5.0,
        gt=0.0,
        description="Timeout in seconds for session intent analysis and context query expansion.",
    )
    recall_rewrite_timeout_s: float = Field(
        default=30.0,
        gt=0.0,
        description="Timeout in seconds for optional context digest rewriting.",
    )
    enable_intent: bool = Field(
        default=True,
        description=(
            "Whether search() loads session context and runs LLM intent analysis / query "
            "planning when session_id is present. false skips session load, "
            "get_context_for_search, and IntentAnalyzer — searches with the raw query only "
            "(same path as no-session search)."
        ),
    )
    relation_expansion: RelationExpansionConfig = Field(
        default_factory=RelationExpansionConfig,
        description="Opt-in single-hop expansion along explicit resource links.",
    )
