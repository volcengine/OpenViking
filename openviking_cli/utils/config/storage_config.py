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

        # Update paths to use workspace
        workspace_path = Path(self.workspace).resolve()
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
        workspace_path = Path(self.workspace).resolve()
        upload_temp_dir = workspace_path / "temp" / "upload"
        upload_temp_dir.mkdir(parents=True, exist_ok=True)
        return upload_temp_dir
