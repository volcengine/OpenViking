# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Optional retrieval views over existing OV memories, with no new evidence types."""

from pydantic import BaseModel, Field


class MemoryTriggerConfig(BaseModel):
    enabled: bool = False
    recall_enabled: bool = True
    max_concurrent: int = Field(default=32, ge=1, le=256)
    max_triggers: int = Field(default=12, ge=1, le=24)
    min_confidence: float = Field(default=0.7, ge=0, le=1)
    candidate_k: int = Field(default=100, ge=1, le=1000)
    max_source_tokens: int = Field(default=8000, ge=512)
    max_output_tokens: int = Field(default=4096, ge=512)
