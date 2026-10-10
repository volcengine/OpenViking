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


class AddResourceQueueWorkerConfig(QueueWorkerConfig):
    """Runtime limits for add-resource queue workers."""

    file_operation_concurrency: int = Field(
        default=16,
        gt=0,
        description="Maximum concurrent file-level commit and comparison operations within one add-resource job",
    )

    file_vectorization_concurrency: int = Field(
        default=8,
        gt=0,
        description="Maximum number of files read, prepared, and enqueued concurrently within one vectors-only job",
    )


class SessionCommitQueueWorkerConfig(QueueWorkerConfig):
    """Runtime limits for session-commit queue workers."""

    stalled_predecessor_timeout_seconds: float = Field(
        default=1800.0,
        ge=0,
        description=(
            "Seconds a running session commit may go without a progress signal before a "
            "later commit of the same session cancels it to release the per-session serial "
            "chain. Progress signals are task state updates, completed Phase 2 steps and "
            "successful model responses. 0 disables the cancellation; blocked successors "
            "are still logged"
        ),
    )


class QueueWorkersConfig(BaseModel):
    """Runtime limits for QueueFS consumers."""

    external_parse: QueueWorkerConfig = Field(default_factory=QueueWorkerConfig)
    add_resource: AddResourceQueueWorkerConfig = Field(default_factory=AddResourceQueueWorkerConfig)
    reindex: QueueWorkerConfig = Field(default_factory=QueueWorkerConfig)
    session_commit: SessionCommitQueueWorkerConfig = Field(
        default_factory=lambda: SessionCommitQueueWorkerConfig(max_concurrent=8)
    )
    external_task: QueueWorkerConfig = Field(
        default_factory=lambda: QueueWorkerConfig(max_concurrent=10)
    )
