# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reindex executor runtime configuration."""

from pydantic import BaseModel, Field


class ReindexConfig(BaseModel):
    """Runtime limits for admin reindex execution."""

    vector_enqueue_concurrency: int = Field(
        default=8,
        gt=0,
        description="Maximum number of vector records prepared and enqueued concurrently",
    )
    max_vector_enqueue_concurrency: int = Field(
        default=64,
        gt=0,
        description="Hard cap for vector enqueue concurrency",
    )

    model_config = {"extra": "forbid"}
