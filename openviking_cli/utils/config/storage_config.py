# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field, model_validator

from openviking_cli.utils.logger import get_logger

from .agfs_config import AGFSConfig
from .vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class StorageConfig(BaseModel):
    """Configuration for storage backend.

    The `workspace` field is the primary configuration for local data storage.
    When `workspace` is set, it overrides the deprecated `path` fields in
    `agfs` and `vectordb` configurations.
    """

    workspace: str = Field(default="./data", description="Local data storage path (primary)")

    agfs: AGFSConfig = Field(default_factory=lambda: AGFSConfig(), description="AGFS configuration")

    vectordb: VectorDBBackendConfig = Field(
        default_factory=lambda: VectorDBBackendConfig(),
        description="VectorDB backend configuration",
    )

    params: Dict[str, Any] = Field(
        default_factory=dict, description="Additional storage-specific parameters"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def resolve_paths(self):
        """Resolve path conflicts between workspace and individual path configs.

        When workspace is set:
        - Ignore agfs.path and vectordb.path
        - Set agfs.path to {workspace}/.agfs
        - Set vectordb.path to {workspace}/vectordb
        - Warn if agfs.path or vectordb.path were explicitly set to different values
        """
        workspace_path = Path(self.workspace).resolve()

        # Check for AGFS path conflict
        if self.agfs.path is not None:  # User explicitly set agfs.path
            agfs_path = Path(self.agfs.path).resolve()
            expected_agfs_path = workspace_path / ".agfs"
            if agfs_path != expected_agfs_path:
                logger.warning(
                    f"StorageConfig: 'agfs.path' is deprecated and will be ignored. "
                    f"Using '{expected_agfs_path}' from workspace instead of '{agfs_path}'"
                )

        # Check for VectorDB path conflict
        if self.vectordb.path is not None:  # User explicitly set vectordb.path
            vectordb_path = Path(self.vectordb.path).resolve()
            expected_vectordb_path = workspace_path / "vectordb"
            if vectordb_path != expected_vectordb_path:
                logger.warning(
                    f"StorageConfig: 'vectordb.path' is deprecated and will be ignored. "
                    f"Using '{expected_vectordb_path}' from workspace instead of '{vectordb_path}'"
                )

        # Update paths to use workspace
        self.agfs.path = str(workspace_path / ".agfs")
        self.vectordb.path = str(workspace_path / "vectordb")

        return self

    def get_upload_temp_dir(self) -> Path:
        """Get the temporary directory for file uploads.

        Returns:
            Path to {workspace}/temp/upload directory
        """
        workspace_path = Path(self.workspace).resolve()
        upload_temp_dir = workspace_path / "temp" / "upload"
        upload_temp_dir.mkdir(parents=True, exist_ok=True)
        return upload_temp_dir
