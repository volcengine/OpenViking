# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict

from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    """Memory configuration for OpenViking."""

    version: str = Field(
        default="v1",
        description="Memory implementation version: 'v1' (legacy) or 'v2' (new templating system)",
    )

    model_config = {"extra": "forbid"}

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "MemoryConfig":
        """Create configuration from dictionary."""
        return cls(**config)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()
