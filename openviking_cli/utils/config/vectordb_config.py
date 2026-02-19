# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Dict, Optional

from pydantic import BaseModel, Field, model_validator

COLLECTION_NAME = "context"


class VolcengineConfig(BaseModel):
    """Configuration for Volcengine VikingDB."""

    ak: Optional[str] = Field(default=None, description="Volcengine Access Key")
    sk: Optional[str] = Field(default=None, description="Volcengine Secret Key")
    region: Optional[str] = Field(
        default=None, description="Volcengine region (e.g., 'cn-beijing')"
    )
    host: Optional[str] = Field(default=None, description="Volcengine VikingDB host (optional)")

    model_config = {"extra": "forbid"}


class VikingDBConfig(BaseModel):
    """Configuration for VikingDB private deployment."""

    host: Optional[str] = Field(default=None, description="VikingDB service host")
    headers: Optional[Dict[str, str]] = Field(
        default_factory=dict, description="Custom headers for requests"
    )

    model_config = {"extra": "forbid"}


class VectorDBBackendConfig(BaseModel):
    """
    Configuration for VectorDB backend.

    This configuration class consolidates all settings related to the VectorDB backend,
    including type, connection details, and backend-specific parameters.
    """

    backend: str = Field(
        default="local",
        description="VectorDB backend type: 'local' (file-based), 'http' (remote service), or 'volcengine' (VikingDB)",
    )

    name: Optional[str] = Field(default=COLLECTION_NAME, description="Collection name for VectorDB")

    path: Optional[str] = Field(default="./data", description="Local storage path for 'local' type")

    url: Optional[str] = Field(
        default=None,
        description="Remote service URL for 'http' type (e.g., 'http://localhost:5000')",
    )

    distance_metric: str = Field(
        default="cosine",
        description="Distance metric for vector similarity search (e.g., 'cosine', 'l2', 'ip')",
    )

    dimension: int = Field(
        default=0,
        description="Dimension of vector embeddings",
    )

    sparse_weight: float = Field(
        default=0.0,
        description=(
            "Sparse weight for hybrid vector search. "
            "When > 0, sparse vectors are used for index build and search."
        ),
    )

    volcengine: Optional[VolcengineConfig] = Field(
        default_factory=lambda: VolcengineConfig(),
        description="Volcengine VikingDB configuration for 'volcengine' type",
    )

    # VikingDB private deployment mode
    vikingdb: Optional[VikingDBConfig] = Field(
        default_factory=lambda: VikingDBConfig(),
        description="VikingDB private deployment configuration for 'vikingdb' type",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        if self.backend not in ["local", "http", "volcengine", "vikingdb"]:
            raise ValueError(
                f"Invalid VectorDB backend: '{self.backend}'. Must be one of: 'local', 'http', 'volcengine', 'vikingdb'"
            )

        if self.backend == "local":
            if not self.path:
                raise ValueError("VectorDB local backend requires 'path' to be set")

        elif self.backend == "http":
            if not self.url:
                raise ValueError("VectorDB http backend requires 'url' to be set")

        elif self.backend == "volcengine":
            if not self.volcengine or not self.volcengine.ak or not self.volcengine.sk:
                raise ValueError("VectorDB volcengine backend requires 'ak' and 'sk' to be set")
            if not self.volcengine.region:
                raise ValueError("VectorDB volcengine backend requires 'region' to be set")

        elif self.backend == "vikingdb":
            if not self.vikingdb or not self.vikingdb.host:
                raise ValueError("VectorDB vikingdb backend requires 'host' to be set")

        return self
