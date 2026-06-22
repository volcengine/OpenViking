# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field, model_validator

from openviking_cli.utils.logger import get_logger

from .agfs_config import AGFSConfig
from .transaction_config import TransactionConfig
from .vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class ResourceRetentionConfig(BaseModel):
    """Configuration for resource version retention."""

    max_versions: int = Field(
        default=0,
        description=(
            "Maximum number of numbered version copies to keep per resource base name. "
            "When exceeded, oldest versions are pruned after creating new version. "
            "0 = no limit (current behavior)."
        ),
    )
    max_age_days: int = Field(
        default=0,
        description=(
            "Maximum age in days for numbered version copies. "
            "Versions older than this may be pruned. 0 = no limit."
        ),
    )
    prune_on_import: bool = Field(
        default=True,
        description="Automatically prune old versions when importing new resource.",
    )

    model_config = {"extra": "forbid"}


class DiskPressureConfig(BaseModel):
    """Configuration for disk pressure monitoring."""

    enabled: bool = Field(
        default=True,
        description="Enable disk pressure monitoring.",
    )
    check_interval_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Interval between disk usage checks in seconds.",
    )
    warning_threshold_percent: float = Field(
        default=85.0,
        ge=0,
        le=100,
        description=(
            "Disk usage percentage that triggers WARNING state. "
            "Operations continue but warnings are logged."
        ),
    )
    critical_threshold_percent: float = Field(
        default=95.0,
        ge=0,
        le=100,
        description=(
            "Disk usage percentage that triggers CRITICAL state. "
            "Write operations are blocked until disk usage decreases."
        ),
    )
    min_free_bytes: int = Field(
        default=1073741824,  # 1 GB
        ge=0,
        description=(
            "Minimum free bytes required. Triggers CRITICAL if free space "
            "falls below this value, regardless of percentage."
        ),
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_thresholds(self):
        if self.critical_threshold_percent <= self.warning_threshold_percent:
            raise ValueError(
                "storage.disk_pressure.critical_threshold_percent must be > warning_threshold_percent"
            )
        return self



class StorageConfig(BaseModel):
    """Configuration for storage backend.

    The `workspace` field is the primary configuration for local data storage.
    When `workspace` is set, it overrides the deprecated `path` fields in
    `agfs` and `vectordb` configurations.
    """

    workspace: str = Field(default="./data", description="Local data storage path (primary)")
    skip_process_lock: bool = Field(
        default=False,
        description=(
            "Skip the startup PID lock for the workspace directory. Use only when you "
            "explicitly accept the risk of multi-process storage contention."
        ),
    )

    agfs: AGFSConfig = Field(default_factory=AGFSConfig, description="AGFS configuration")

    transaction: TransactionConfig = Field(
        default_factory=TransactionConfig,
        description="Transaction mechanism configuration",
    )

    vectordb: VectorDBBackendConfig = Field(
        default_factory=VectorDBBackendConfig,
        description="VectorDB backend configuration",
    )

    retention: ResourceRetentionConfig = Field(
        default_factory=ResourceRetentionConfig,
        description="Resource version retention configuration",
    )

    disk_pressure: DiskPressureConfig = Field(
        default_factory=DiskPressureConfig,
        description="Disk pressure monitoring configuration",
    )


    params: Dict[str, Any] = Field(
        default_factory=dict, description="Additional storage-specific parameters"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def ignore_deprecated_task_tracker(cls, data: Any) -> Any:
        if isinstance(data, dict) and "task_tracker" in data:
            data = dict(data)
            data.pop("task_tracker", None)
            logger.warning(
                "StorageConfig: 'task_tracker' is deprecated and ignored. "
                "Task records are always persisted."
            )
        return data

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
        """Build the persistent TaskTracker from storage config."""
        from openviking.service.task_store import PersistentTaskStore
        from openviking.service.task_tracker import TaskTracker

        return TaskTracker(store=PersistentTaskStore(agfs))
