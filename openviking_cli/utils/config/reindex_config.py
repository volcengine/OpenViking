# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reindex executor runtime configuration."""

from pydantic import BaseModel, Field


class ReindexConfig(BaseModel):
    """Runtime limits for admin reindex execution."""

    file_vectorization_concurrency: int = Field(
        default=8,
        gt=0,
        description="Maximum number of files read, prepared, and enqueued concurrently",
    )

    model_config = {"extra": "forbid"}
