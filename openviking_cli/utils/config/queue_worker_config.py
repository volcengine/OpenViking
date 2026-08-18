# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue worker runtime configuration."""

from pydantic import BaseModel, Field


class QueueWorkerConfig(BaseModel):
    """Runtime limits for one queue worker."""

    max_concurrent: int = Field(
        default=4,
        gt=0,
        description="Maximum number of jobs processed concurrently",
    )

    model_config = {"extra": "forbid"}


class QueueWorkersConfig(BaseModel):
    """Runtime limits for QueueFS consumers."""

    external_parse: QueueWorkerConfig = Field(default_factory=QueueWorkerConfig)
    add_resource: QueueWorkerConfig = Field(default_factory=QueueWorkerConfig)
    session_commit: QueueWorkerConfig = Field(
        default_factory=lambda: QueueWorkerConfig(max_concurrent=8)
    )

    model_config = {"extra": "forbid"}
