# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, Literal

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

    # [liclaw] scope_mode: 控制记忆写入的 scope 路由策略
    # "default" = 按类别分 scope（PROFILE/PREFERENCES/ENTITIES/EVENTS → user, CASES/PATTERNS → agent）
    # "isolated" = 所有类别都写入 agent scope，实现 agent 间完全记忆隔离
    scope_mode: Literal["default", "isolated"] = Field(
        default="isolated",
        description=(
            "Memory scope routing. [liclaw] default is 'isolated' so OpenClaw-integrated "
            "deployments align with full agent memory isolation. 'default' routes user-level "
            "memories (profile/preferences/entities/events) to shared user space and "
            "agent-level memories (cases/patterns) to isolated agent space; "
            "'isolated' routes ALL categories to agent space for full isolation."
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
