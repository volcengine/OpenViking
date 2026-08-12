# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration for the local keyword (SQLite FTS5) sidecar."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TokenizerMode = Literal["auto", "char", "jieba"]
ContentSource = Literal["content", "summary", "both"]
CjkMode = Literal["char", "bigram"]


class KeywordConfig(BaseModel):
    """Configuration for the local SQLite FTS5 keyword sidecar."""

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the local keyword index. Off by default. "
            "When enabled and the sidecar is ready, grep/find can use "
            "search-time BM25 recall without a remote VikingDB full-text index."
        ),
    )
    tokenizer: TokenizerMode = Field(
        default="auto",
        description=(
            "CJK tokenizer mode: 'auto' uses jieba when installed and falls back "
            "to character-level splitting; 'char' always splits CJK per character; "
            "'jieba' requires the optional 'jieba' dependency."
        ),
    )
    content_source: ContentSource = Field(
        default="content",
        description=(
            "Text source indexed by the keyword sidecar: 'content' uses the same "
            "source as embedding.text_source for leaves, 'summary' uses the L0/L1 "
            "summaries, 'both' indexes content and summaries."
        ),
    )
    max_doc_bytes: int = Field(
        default=65536,
        ge=1,
        description="Skip documents whose indexed text exceeds this byte size.",
    )
    cjk_mode: CjkMode = Field(
        default="char",
        description=(
            "CJK splitting granularity when a word tokenizer is not used: 'char' "
            "indexes each Han character as a token (high recall), 'bigram' also "
            "emits overlapping bigrams (higher precision for short queries)."
        ),
    )
    respect_encryption: bool = Field(
        default=True,
        description=(
            "When true, the keyword sidecar is disabled on deployments with "
            "at-rest encryption enabled, because the sidecar stores plaintext text."
        ),
    )
    db_dir: str = Field(
        default="",
        description=(
            "Optional override for the sidecar directory. When empty, the sidecar "
            "is placed under <storage.workspace>/_system/keyword/."
        ),
    )
    max_candidates: int = Field(
        default=100000,
        ge=1,
        description="Upper bound on FTS5 candidate rows recalled per query.",
    )

    model_config = {"extra": "forbid"}


class HybridRetrievalConfig(BaseModel):
    """Configuration for fusing keyword recall into find/search."""

    enabled: bool = Field(
        default=False,
        description="When true, find/search also recall keyword candidates and fuse them.",
    )
    fusion: Literal["rrf", "weighted"] = Field(
        default="rrf",
        description=(
            "Score fusion strategy: 'rrf' uses Reciprocal Rank Fusion (robust, "
            "no score calibration); 'weighted' blends normalized BM25 with the "
            "dense score."
        ),
    )
    rrf_k: float = Field(default=60.0, gt=0.0, description="RRF constant k.")
    keyword_weight: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Keyword weight used by the 'weighted' fusion.",
    )
    min_token_query_len: int = Field(
        default=2,
        ge=1,
        description="Skip keyword recall when the query yields fewer tokens than this.",
    )

    model_config = {"extra": "forbid"}
