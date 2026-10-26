# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Model configuration for rule compilation and applicability matching."""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from .jev_config import JevConfig
from .vlm_config import VLMConfig


class RulesConfig(BaseModel):
    """Models dedicated to the rules module."""

    archived_retention_days: int = Field(
        default=30,
        ge=1,
        description="Days a rule must remain archived before permanent cleanup",
    )
    archived_cleanup_interval_seconds: int = Field(
        default=2_592_000,
        ge=60,
        description="Interval between archived-rule cleanup scans",
    )
    archived_cleanup_enabled: bool = Field(
        default=True,
        description="Whether to periodically clean up rules archived past retention",
    )
    compiler_context_window: int = Field(
        default=0,
        ge=0,
        description=(
            "Compiler model context window in tokens; 0 auto-detects it from the model "
            "configuration and falls back to a conservative default"
        ),
    )
    jev_rule_matcher: Optional[JevConfig] = Field(
        default=None,
        description="Optional Jev Noul model for independent rule applicability scoring",
    )
    scenario_irrelevance_threshold: float = Field(
        default=0.9,
        ge=0.5,
        le=1.0,
        description=(
            "Skip a recalled Scenario only when Jev's probability that it is completely "
            "unrelated to the current context meets this threshold"
        ),
    )
    compiler: Optional[VLMConfig] = Field(
        default=None,
        description="Optional VLM used to compile source material into rules",
    )

    @model_validator(mode="after")
    def validate_rules_config(self) -> "RulesConfig":
        if 0 < self.compiler_context_window < 2048:
            raise ValueError("rules.compiler_context_window must be 0 or at least 2048")
        return self

    def get_compiler(self) -> Optional[VLMConfig]:
        """Return the dedicated compiler, treating an empty block as unset."""
        if self.compiler is not None and self.compiler._has_any_config():
            return self.compiler
        return None
