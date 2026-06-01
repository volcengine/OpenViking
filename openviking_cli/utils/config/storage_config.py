# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from openviking_cli.utils.logger import get_logger

from .agfs_config import AGFSConfig
from .transaction_config import TransactionConfig
from .vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class TaskTrackerConfig(BaseModel):
    """Configuration for async task tracking backend."""

    backend: Literal["memory", "persistent"] = Field(
        default="memory",
        description="Task tracker backend. 'persistent' enables cross-instance task lookup.",
    )


class CoordinationConfig(BaseModel):
    """Configuration for cross-instance coordination backend.

    Coordination unifies the process-local trackers (semantic coalesce
    version, request-wait tracker, embedding task tracker, request stats)
    behind a shared backend so multiple load-balanced server instances stay
    consistent.

    This is an explicit deployment-topology switch and is intentionally NOT
    derived from `queuefs.backend`: sqlite on a non-shared local disk (single
    machine, no coordination needed) and sqlite on a shared mount (multi
    instance, coordination needed) are indistinguishable from config. Default
    'memory' keeps single-machine deployments unchanged with no new dependency.
    """

    backend: Literal["memory", "redis"] = Field(
        default="memory",
        description="Coordination backend. 'redis' enables multi-instance consistency.",
    )

    dsn: Optional[str] = Field(
        default=None,
        description="Redis DSN (e.g. redis://host:6379/0). Required when backend='redis'. "
        "Falls back to the OPENVIKING_COORD_DSN environment variable when not set; "
        "never hardcode credentials.",
    )

    key_prefix: str = Field(
        default="ov:coord:",
        description="Prefix applied to all coordination keys for tenant/cluster isolation.",
    )

    ttl_sec: int = Field(
        default=3600,
        description="Default TTL (seconds) applied to mutated coordination keys so abandoned "
        "request/message state self-expires. Set 0 to disable expiry.",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        if self.ttl_sec < 0:
            raise ValueError("coordination ttl_sec must be >= 0")
        return self


class StorageConfig(BaseModel):
    """Configuration for storage backend.

    The `workspace` field is the primary configuration for local data storage.
    When `workspace` is set, it overrides the deprecated `path` fields in
    `agfs` and `vectordb` configurations.
    """

    workspace: str = Field(default="./data", description="Local data storage path (primary)")

    agfs: AGFSConfig = Field(default_factory=AGFSConfig, description="AGFS configuration")

    transaction: TransactionConfig = Field(
        default_factory=TransactionConfig,
        description="Transaction mechanism configuration",
    )

    vectordb: VectorDBBackendConfig = Field(
        default_factory=VectorDBBackendConfig,
        description="VectorDB backend configuration",
    )

    task_tracker: TaskTrackerConfig = Field(
        default_factory=TaskTrackerConfig,
        description="Task tracker backend configuration",
    )

    coordination: CoordinationConfig = Field(
        default_factory=CoordinationConfig,
        description="Cross-instance coordination backend configuration",
    )

    params: Dict[str, Any] = Field(
        default_factory=dict, description="Additional storage-specific parameters"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def resolve_paths(self):
        if self.agfs.path is not None:
            logger.warning(
                f"StorageConfig: 'agfs.path' is deprecated and will be ignored. "
                f"Using '{self.workspace}' from workspace instead of '{self.agfs.path}'"
            )

        if self.vectordb.path is not None:
            logger.warning(
                f"StorageConfig: 'vectordb.path' is deprecated and will be ignored. "
                f"Using '{self.workspace}' from workspace instead of '{self.vectordb.path}'"
            )

        # Update paths to use workspace (expand ~ first)
        workspace_path = Path(self.workspace).expanduser().resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)
        self.workspace = str(workspace_path)
        self.agfs.path = self.workspace
        self.vectordb.path = self.workspace
        # logger.info(f"StorageConfig: Using workspace '{self.workspace}' for storage")
        return self

    def get_upload_temp_dir(self) -> Path:
        """Get the temporary directory for file uploads.

        Returns:
            Path to {workspace}/temp/upload directory
        """
        workspace_path = Path(self.workspace).expanduser().resolve()
        upload_temp_dir = workspace_path / "temp" / "upload"
        upload_temp_dir.mkdir(parents=True, exist_ok=True)
        return upload_temp_dir

    def build_task_tracker(self, agfs: Any):
        """Build a TaskTracker from storage config."""
        from openviking.service.task_store import PersistentTaskStore
        from openviking.service.task_tracker import TaskTracker

        if self.task_tracker.backend == "memory":
            return TaskTracker()
        return TaskTracker(store=PersistentTaskStore(agfs))

    def build_coordinator(self):
        """Build a Coordinator from storage config.

        Returns an in-process coordinator for the default 'memory' backend
        (single-machine, zero new dependency) or a Redis-backed coordinator
        for multi-instance consistency. The Redis DSN comes from the config
        or, when absent, the OPENVIKING_COORD_DSN environment variable.
        """
        from openviking.service.coordinator import InProcessCoordinator, RedisCoordinator

        if self.coordination.backend == "memory":
            return InProcessCoordinator()

        dsn = self.coordination.dsn or os.environ.get("OPENVIKING_COORD_DSN")
        if not dsn:
            raise ValueError(
                "storage.coordination.backend='redis' requires a DSN. "
                "Set storage.coordination.dsn or the OPENVIKING_COORD_DSN environment variable."
            )
        return RedisCoordinator(
            dsn,
            key_prefix=self.coordination.key_prefix,
            default_ttl_sec=self.coordination.ttl_sec,
        )
