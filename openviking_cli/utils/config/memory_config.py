# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict

from pydantic import BaseModel, Field, field_validator, model_validator


class MemoryConfig(BaseModel):
    """Memory configuration for OpenViking."""

    version: str = Field(
        default="v2",
        description="Memory implementation version. Only 'v2' is supported.",
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
            "Enable trajectory/experience memory extraction. When true, "
            "a two-phase pipeline runs after user-memory extraction: Phase 1 extracts "
            "execution trajectories from the conversation; Phase 2 consolidates them "
            "into higher-level experience memories."
        ),
    )
    experimental_memory_switch: bool = Field(
        default=False,
        description=(
            "Experimental memory switch for experimental testing. When enabled, "
            "experimental memory templates are loaded and agent_memory_enabled defaults "
            "to true unless explicitly configured."
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
    session_skill_extraction_enabled: bool = Field(
        default=False,
        description=(
            "When enabled, session commit also extracts reusable skills from the archived "
            "conversation and writes them into the current user's skill directory. Disabled by "
            "default."
        ),
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

    @model_validator(mode="before")
    @classmethod
    def default_agent_memory_for_experimental_switch(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("experimental_memory_switch") is True:
            data = data.copy()
            data.setdefault("agent_memory_enabled", True)
        return data

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value != "v2":
            raise ValueError("memory.version only supports 'v2'; legacy memory v1 has been removed")
        return value

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "MemoryConfig":
        """Create configuration from dictionary."""
        return cls(**config)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()
