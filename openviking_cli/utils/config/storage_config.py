# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict

from pydantic import BaseModel, Field

from .agfs_config import AGFSConfig
from .vectordb_config import VectorDBBackendConfig


class StorageConfig(BaseModel):
    """Configuration for storage backend."""

    agfs: AGFSConfig = Field(default_factory=lambda: AGFSConfig(), description="AGFS configuration")

    vectordb: VectorDBBackendConfig = Field(
        default_factory=lambda: VectorDBBackendConfig(),
        description="VectorDB backend configuration",
    )

    params: Dict[str, Any] = Field(
        default_factory=dict, description="Additional storage-specific parameters"
    )

    model_config = {"extra": "forbid"}
