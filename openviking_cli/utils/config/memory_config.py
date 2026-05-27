# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class MemoryConfig(BaseModel):
    """Memory configuration for OpenViking."""

    version: str = Field(
        default="v2",
        description="Memory implementation version. Only 'v2' is supported.",
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
    experimental_memory_switch: bool = Field(
        default=False,
        description=(
            "Experimental memory switch for experimental testing. When enabled, "
            "experimental memory templates are loaded and agent_memory_enabled defaults "
            "to true unless explicitly configured."
        ),
    )
    agent_experience_per_trajectory_max_concurrency: int = Field(
        default=4,
        ge=1,
        description=(
            "Maximum number of per-trajectory experience consolidation phases to run "
            "concurrently within one committed session when agent experience apply uses "
            "operation_exact. Tree-lock apply still runs serially to preserve the existing "
            "safe behavior."
        ),
    )
    agent_experience_consolidation_mode: str = Field(
        default="per_trajectory",
        description=(
            "Deprecated and ignored. Kept only for backward compatibility with older "
            "ov.conf files that enabled the removed batch experience consolidation mode."
        ),
    )
    agent_experience_batch_max_trajectories: int = Field(
        default=5,
        ge=1,
        description=(
            "Deprecated and ignored. Kept only for backward compatibility with older "
            "ov.conf files that configured the removed batch experience consolidation mode."
        ),
    )
    agent_experience_apply_lock_mode: Literal["tree", "operation_exact"] = Field(
        default="tree",
        description=(
            "Experimental lock scope for agent experience apply. 'tree' preserves the existing "
            "schema-directory lock around read/LLM/apply. 'operation_exact' lets the read/LLM phase "
            "run before acquiring exact locks for the concrete files that will be written."
        ),
    )
    agent_trajectory_apply_lock_mode: Literal["tree", "operation_exact"] = Field(
        default="tree",
        description=(
            "Experimental lock scope for agent trajectory apply. 'tree' preserves the existing "
            "schema-directory lock around trajectory read/LLM/apply. 'operation_exact' lets the "
            "trajectory LLM phase run before acquiring exact locks for the concrete trajectory files "
            "and overview that will be written."
        ),
    )
    long_term_apply_lock_mode: Literal["tree", "operation_exact"] = Field(
        default="tree",
        description=(
            "Experimental lock scope for standard long-term memory apply, including tool and skill "
            "memories. 'tree' preserves the existing schema-directory lock around read/LLM/apply. "
            "'operation_exact' lets read/LLM run before acquiring exact locks for concrete memory "
            "files and directory overviews that will be written."
        ),
    )
    operation_exact_apply_window_seconds: float = Field(
        default=10.0,
        ge=0.0,
        description=(
            "Server-side apply window for operation_exact phases. Requests for the same "
            "concrete target set queue behind one in-process window owner. After the window "
            "closes, the owner acquires the union of exact file locks and applies the queued "
            "patches in arrival order while reading the latest locked content. Set to 0 to "
            "disable the window."
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
            "to produce memories. When disabled, sessions are archived "
            "but no standard or agent memory extraction is performed. Useful for "
            "read-only or stateless deployments."
        ),
    )
    long_term_extraction_enabled: bool = Field(
        default=True,
        description=(
            "When enabled (default), session commit extracts standard long-term "
            "memories such as user memories, tool memories, and skill memories. "
            "Set to false to skip this standard extraction while still allowing "
            "archive summaries, agent memory extraction, and session skill "
            "extraction when their own switches are enabled."
        ),
    )
    session_skill_extraction_enabled: bool = Field(
        default=False,
        description=(
            "When enabled, session commit also extracts reusable skills from the archived "
            "conversation and writes them into the agent skill directory. Disabled by "
            "default."
        ),
    )
    role_id_memory_isolation_enabled: bool = Field(
        default=False,
        description=(
            "When enabled, memory extraction uses role_id from messages to determine "
            "which user/agent the memory belongs to. When disabled (default), role_id "
            "is ignored and the login user from the request context is used instead."
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

    @field_validator("agent_scope_mode")
    @classmethod
    def validate_agent_scope_mode(cls, value: str) -> str:
        if value not in {"user+agent", "agent"}:
            raise ValueError("memory.agent_scope_mode must be 'user+agent' or 'agent'")
        return value

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
