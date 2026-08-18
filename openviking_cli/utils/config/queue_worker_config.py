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


class AddResourceQueueWorkerConfig(QueueWorkerConfig):
    """Runtime limits for add-resource queue workers."""

    vector_enqueue_concurrency: int = Field(
        default=8,
        gt=0,
        description="Maximum number of files enqueued concurrently within one vectors-only job",
    )
    max_vector_enqueue_concurrency: int = Field(
        default=64,
        gt=0,
        description="Hard cap for vectors-only file enqueue concurrency",
    )


class QueueWorkersConfig(BaseModel):
    """Runtime limits for QueueFS consumers."""

    external_parse: QueueWorkerConfig = Field(default_factory=QueueWorkerConfig)
    add_resource: AddResourceQueueWorkerConfig = Field(
        default_factory=AddResourceQueueWorkerConfig
    )
    session_commit: QueueWorkerConfig = Field(
        default_factory=lambda: QueueWorkerConfig(max_concurrent=8)
    )

    model_config = {"extra": "forbid"}
