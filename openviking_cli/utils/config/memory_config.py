# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict

from pydantic import BaseModel, Field, field_validator, model_validator

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class MemoryConfig(BaseModel):
    """Memory configuration for OpenViking."""

    version: str = Field(
        default="v3",
        description="Deprecated and ignored. Memory extraction always uses v3.",
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
    experimental_memory_switch: bool = Field(
        default=False,
        description=(
            "Experimental memory switch for experimental testing. When enabled, "
            "experimental memory templates are loaded."
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
    def drop_deprecated_memory_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "agent_memory_enabled" in data:
                data.pop("agent_memory_enabled", None)
                logger.debug(
                    "memory.agent_memory_enabled is deprecated and ignored; "
                    "use session memory_policy.memory_types to control trajectory/experience extraction"
                )
            if "working_memory_enabled" in data:
                data.pop("working_memory_enabled", None)
                logger.debug(
                    "memory.working_memory_enabled is deprecated and ignored; "
                    "use session memory_policy.working_memory.enabled to control archive summaries"
                )
        return data

    @field_validator("version", mode="before")
    @classmethod
    def accept_deprecated_version(cls, value: Any) -> str:
        if value not in (None, ""):
            logger.debug(
                "memory.version is deprecated and ignored; memory extraction always uses v3"
            )
        return "v3"

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "MemoryConfig":
        """Create configuration from dictionary."""
        return cls(**config)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()
