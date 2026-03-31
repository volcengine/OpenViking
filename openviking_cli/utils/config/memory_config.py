# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import re
from datetime import timedelta
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class MemoryConfig(BaseModel):
    """Memory configuration for OpenViking."""

    version: str = Field(
        default="v1",
        description="Memory implementation version: 'v1' (legacy) or 'v2' (new templating system)",
    )
    agent_scope_mode: str = Field(
        default="user+agent",
        description=(
            "Agent memory namespace mode: 'user+agent' keeps agent memory isolated by "
            "(user_id, agent_id), while 'agent' shares agent memory across users of the same agent."
        ),
    )

    custom_templates_dir: str = Field(
        default="",
        description="Custom memory templates directory. If set, templates from this directory will be loaded in addition to built-in templates",
    )
    default_ttl: Optional[str] = Field(
        default=None,
        description="Default TTL for new memories (e.g. '7d', '24h'). None = no expiration",
    )
    ttl_by_type: Dict[str, Optional[str]] = Field(
        default_factory=dict, description="Per memory-type TTL overrides"
    )

    model_config = {"extra": "forbid"}

    @field_validator("agent_scope_mode")
    @classmethod
    def validate_agent_scope_mode(cls, value: str) -> str:
        if value not in {"user+agent", "agent"}:
            raise ValueError("memory.agent_scope_mode must be 'user+agent' or 'agent'")
        return value

    @staticmethod
    def parse_ttl(ttl_str: Optional[str]) -> Optional[timedelta]:
        """Parse TTL values like '7d', '24h', '30m' into timedelta."""
        if ttl_str is None:
            return None
        match = re.fullmatch(r"\s*(\d+)\s*([smhdw])\s*", ttl_str.lower())
        if not match:
            raise ValueError(f"Invalid TTL format: {ttl_str}")
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "s":
            return timedelta(seconds=value)
        if unit == "m":
            return timedelta(minutes=value)
        if unit == "h":
            return timedelta(hours=value)
        if unit == "d":
            return timedelta(days=value)
        return timedelta(weeks=value)

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "MemoryConfig":
        """Create configuration from dictionary."""
        return cls(**config)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()
