# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
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


class RedisCoordinationConfig(BaseModel):
    """Backend-specific configuration for Redis coordination."""

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

    Backend-specific settings are nested under the corresponding key (mirrors
    the vectordb config pattern for consistency):

        {"backend": "redis", "redis": {"dsn": "redis://...", ...}}
    """

    backend: str = Field(
        default="memory",
        description=(
            "Coordination backend. Built-in values: 'memory' (default, single-instance) "
            "or 'redis' (multi-instance). For custom backends, set to a full dotted class path "
            "(e.g. 'mycompany.module.CredisCoordinator'). The class must "
            "implement a 'from_config(cfg: CoordinationConfig)' classmethod."
        ),
    )

    redis: RedisCoordinationConfig = Field(
        default_factory=RedisCoordinationConfig,
        description="Redis backend configuration. Used when backend='redis'.",
    )

    embedding_completion_timeout_sec: int = Field(
        default=1800,
        description=(
            "Distributed-only timeout (seconds) for one semantic root's embedding "
            "completion barrier. When > 0, a distributed coordinator watchdog marks "
            "the semantic root failed and runs timeout cleanup if remaining embedding "
            "tasks never drain. Ignored when coordination backend is local/in-process."
        ),
    )

    custom_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Custom parameters passed to from_config() for third-party coordinator backends.",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        if self.embedding_completion_timeout_sec < 0:
            raise ValueError("coordination embedding_completion_timeout_sec must be >= 0")
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
        """Build a Coordinator from storage config."""
        from openviking.service.coordinator_factory import create_coordinator

        return create_coordinator(self.coordination)
