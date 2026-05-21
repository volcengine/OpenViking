# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict

from pydantic import BaseModel, Field, field_validator


class MemoryConfig(BaseModel):
    """Memory configuration for OpenViking."""

    version: str = Field(
        default="v2",
        description="Memory implementation version: 'v1' (legacy) or 'v2' (new templating system)",
    )
    agent_scope_mode: str = Field(
        default="user+agent",
        description=(
            "Deprecated and ignored. Kept only for backward compatibility with older ov.conf files. "
            "Agent/user namespace behavior is now controlled by per-account namespace policy."
        ),
    )

    custom_templates_dir: str = Field(
        default="",
        description="Custom memory templates directory. If set, templates from this directory will be loaded in addition to built-in templates",
    )
    v2_lock_retry_interval_seconds: float = Field(
        default=0.2,
        ge=0.0,
        description=(
            "Retry interval (seconds) when SessionCompressorV2 fails to acquire memory subtree "
            "locks. Set to 0 for immediate retries."
        ),
    )
    v2_lock_max_retries: int = Field(
        default=0,
        ge=0,
        description=(
            "Maximum retries for SessionCompressorV2 memory lock acquisition. "
            "0 means unlimited retries."
        ),
    )
    agent_memory_enabled: bool = Field(
        default=False,
        description=(
            "Enable agent-scope trajectory/experience memory extraction. When true, "
            "a two-phase pipeline runs after user-memory extraction: Phase 1 extracts "
            "execution trajectories from the conversation; Phase 2 consolidates them "
            "into higher-level experience memories."
        ),
    )
    eager_prefetch: bool = Field(
        default=True,
        description=(
            "When enabled, prefetch will execute search + read to preload all memory file contents "
            "into the context, and no read/search tools will be provided to the LLM. "
            "When disabled (default), LLM has read tool and reads files on-demand."
        ),
    )
    prefetch_search_topn: int = Field(
        default=5,
        ge=1,
        description=(
            "Number of top search results to read during prefetch. "
            "Only applies when eager_prefetch is enabled. "
            "When multiple directories are searched, results are merged and top-N are read."
        ),
    )
    extraction_enabled: bool = Field(
        default=True,
        description=(
            "When enabled (default), memory extraction runs on session commit "
            "to produce long-term memories. When disabled, sessions are archived "
            "but no memory extraction is performed. Useful for read-only or "
            "stateless deployments."
        ),
    )
    enable_vaka_template: bool = Field(
        default=False,
        description=(
            "When enabled, use vaka-specific memory templates (entities, profile) "
            "from the bundled vaka/ subdirectory to override default templates."
        ),
    )
    enable_role_id_memory_isolate: bool = Field(
        default=False,
        description=(
            "When enabled, memory extraction uses role_id from messages to determine "
            "which user/agent the memory belongs to. When disabled (default), role_id "
            "is ignored and the login user from the request context is used instead."
        ),
    )
    wm_v2_preprocess_enabled: bool = Field(
        default=False,
        description=(
            "Enable compact pre-processing for Working Memory v2 incremental update prompts. "
            "When disabled, WM v2 update uses the original full archived messages."
        ),
    )
    wm_v2_preprocess_max_span_tokens: int = Field(
        default=1200,
        ge=100,
        description="Maximum estimated tokens to spend on selected evidence spans.",
    )
    wm_v2_preprocess_min_span_tokens: int = Field(
        default=200,
        ge=0,
        description="Minimum span budget floor after adaptive preprocessing adjustments.",
    )
    wm_v2_preprocess_max_span_chars: int = Field(
        default=1600,
        ge=100,
        description="Maximum characters allowed in each selected evidence span.",
    )
    wm_v2_preprocess_fallback_ratio: float = Field(
        default=0.9,
        ge=0.1,
        le=10.0,
        description=(
            "Fallback to full messages when compact packet tokens are greater than this "
            "ratio of full message tokens."
        ),
    )
    wm_v2_preprocess_min_full_tokens: int = Field(
        default=600,
        ge=0,
        description=(
            "Skip compact preprocessing when the estimated full message tokens are "
            "below this threshold. Set to 0 to force compact even for short sessions."
        ),
    )
    wm_v2_preprocess_min_absolute_savings_tokens: int = Field(
        default=500,
        ge=0,
        description=(
            "Fallback to full messages when compact preprocessing saves fewer than "
            "this many estimated tokens, even if the ratio threshold passes."
        ),
    )
    wm_v2_preprocess_mmr_similarity_threshold: float = Field(
        default=0.72,
        ge=0.0,
        le=1.0,
        description=(
            "Maximum Jaccard similarity allowed between selected non-tool evidence "
            "spans before they are considered redundant."
        ),
    )
    wm_v2_preprocess_max_tool_spans: int = Field(
        default=3,
        ge=0,
        description=(
            "Maximum number of tool-heavy spans that can bypass normal MMR "
            "deduplication in a compact packet."
        ),
    )
    wm_v2_preprocess_expand_budget_on_risk: bool = Field(
        default=True,
        description=(
            "When enabled, risk flags can expand the evidence span budget before "
            "compaction fallback is decided."
        ),
    )
    wm_v2_preprocess_max_facts_total: int = Field(
        default=24,
        ge=0,
        description="Maximum structured facts retained in a compact packet.",
    )
    wm_v2_preprocess_max_tool_output_chars: int = Field(
        default=300,
        ge=0,
        description="Maximum characters preserved from each tool output in normalized spans.",
    )
    link_enabled: bool = Field(
        default=False,
        description=(
            "When enabled, memory extraction supports link extraction between "
            "memory items (page_id, links field, and link resolution). When disabled (default), "
            "no page_id or link fields are generated, and link resolution is skipped."
        ),
    )

    model_config = {"extra": "forbid"}

    @field_validator("agent_scope_mode")
    @classmethod
    def validate_agent_scope_mode(cls, value: str) -> str:
        if value not in {"user+agent", "agent"}:
            raise ValueError("memory.agent_scope_mode must be 'user+agent' or 'agent'")
        return value

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "MemoryConfig":
        """Create configuration from dictionary."""
        return cls(**config)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()
