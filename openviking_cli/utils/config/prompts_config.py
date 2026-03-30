# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict

from pydantic import BaseModel, Field


class PromptsConfig(BaseModel):
    """Prompt template configuration for OpenViking."""

    templates_dir: str = Field(
        default="",
        description=(
            "Custom prompt templates directory. If set, PromptManager loads prompt "
            "templates from this directory instead of the bundled templates."
        ),
    )

    model_config = {"extra": "forbid"}

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "PromptsConfig":
        """Create configuration from dictionary."""
        return cls(**config)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()
