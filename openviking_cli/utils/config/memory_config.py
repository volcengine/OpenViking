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
    eager_prefetch: bool = Field(
        default=False,
        description=(
            "When enabled, prefetch will execute search + read to preload all memory file contents "
            "into the context, and no read/search tools will be provided to the LLM. "
            "When disabled (default), LLM has read tool and reads files on-demand."
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
