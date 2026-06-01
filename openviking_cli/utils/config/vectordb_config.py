# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, model_validator

from openviking_cli.utils.logger import get_logger

COLLECTION_NAME = "context"
DEFAULT_PROJECT_NAME = "default"
DEFAULT_INDEX_NAME = "default"
logger = get_logger(__name__)


class VolcengineConfig(BaseModel):
    """Configuration for Volcengine VikingDB."""

    ak: Optional[str] = Field(default=None, description="Volcengine Access Key")
    sk: Optional[str] = Field(default=None, description="Volcengine Secret Key")
    api_key: Optional[str] = Field(
        default=None,
        description="Optional VikingDB Data API key for data-plane-only access",
    )
    session_token: Optional[str] = Field(
        default=None,
        description="Optional Volcengine STS security token for temporary credentials",
    )
    region: Optional[str] = Field(
        default=None, description="Volcengine region (e.g., 'cn-beijing')"
    )
    host: Optional[str] = Field(
        default=None,
        description=(
            "Optional VikingDB data API host. "
            "Used together with `api_key` for data-plane-only access."
        ),
    )

    model_config = {"extra": "forbid"}


class VikingDBConfig(BaseModel):
    """Configuration for VikingDB private deployment."""

    host: Optional[str] = Field(default=None, description="VikingDB service host")
    headers: Optional[Dict[str, str]] = Field(
        default_factory=dict, description="Custom headers for requests"
    )

    model_config = {"extra": "forbid"}


class QdrantConfig(BaseModel):
    """Configuration for Qdrant backend."""

    url: Optional[str] = Field(default=None, description="Qdrant service URL")
    api_key: Optional[str] = Field(default=None, description="Optional Qdrant API key")
    timeout_seconds: int = Field(default=10, description="HTTP timeout for Qdrant requests")
    dense_vector_name: str = Field(
        default="vector",
        description="Named dense vector field in Qdrant collection.",
    )
    sparse_vector_name: str = Field(
        default="sparse_vector",
        description="Named sparse vector field in Qdrant collection.",
    )
    meta_collection_name: str = Field(
        default="__openviking_meta",
        description="Sidecar collection name for OpenViking metadata in Qdrant.",
    )
    enable_text_index: bool = Field(
        default=True,
        description="Whether to create text payload indexes for supported text fields.",
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
        description=(
            "VectorDB backend type: 'local', 'http', "
            "'volcengine' (AK/SK signed or API key data-plane only), "
            "'vikingdb' (private deployment), or 'qdrant'"
        ),
    )

    name: Optional[str] = Field(default=COLLECTION_NAME, description="Collection name for VectorDB")

    path: Optional[str] = Field(
        default=None,
        description="[Deprecated in favor of `storage.workspace`] Local storage path for 'local' type. This will be ignored if `storage.workspace` is set.",
    )

    url: Optional[str] = Field(
        default=None,
        description="Remote service URL for 'http' type (e.g., 'http://localhost:5000')",
    )

    project_name: Optional[str] = Field(
        default=DEFAULT_PROJECT_NAME, description="project name", alias="project"
    )

    index_name: Optional[str] = Field(
        default=DEFAULT_INDEX_NAME,
        description="Default index name for VectorDB operations",
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
        default_factory=VolcengineConfig,
        description="Volcengine VikingDB configuration for 'volcengine' type",
    )

    # VikingDB private deployment mode
    vikingdb: Optional[VikingDBConfig] = Field(
        default_factory=VikingDBConfig,
        description="VikingDB private deployment configuration for 'vikingdb' type",
    )

    qdrant: Optional[QdrantConfig] = Field(
        default_factory=QdrantConfig,
        description="Qdrant configuration for 'qdrant' type",
    )

    custom_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Custom parameters for custom backend adapters",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        standard_backends = ["local", "http", "volcengine", "vikingdb", "qdrant"]

        # Allow custom backend classes (containing dot) without standard validation
        if "." in self.backend:
            logger.info("Using custom VectorDB backend: %s", self.backend)
            return self

        if self.backend not in standard_backends:
            raise ValueError(
                f"Invalid VectorDB backend: '{self.backend}'. Must be one of: {standard_backends} "
                "or a valid Python class path."
            )

        if self.backend == "local":
            pass

        elif self.backend == "http":
            if not self.url:
                raise ValueError("VectorDB http backend requires 'url' to be set")

        elif self.backend == "volcengine":
            if self.volcengine and self.volcengine.host:
                self.volcengine.host = self.volcengine.host.strip().rstrip("/")

            uses_api_key = bool(self.volcengine and self.volcengine.api_key)
            if uses_api_key:
                if not self.volcengine or not (self.volcengine.host or self.volcengine.region):
                    raise ValueError(
                        "VectorDB volcengine backend with 'api_key' requires 'host' or 'region' to be set"
                    )
            else:
                if not self.volcengine or not self.volcengine.ak or not self.volcengine.sk:
                    raise ValueError(
                        "VectorDB volcengine backend requires 'ak' and 'sk' to be set "
                        "when 'api_key' is not configured"
                    )
                if not self.volcengine.region:
                    raise ValueError("VectorDB volcengine backend requires 'region' to be set")
            if self.volcengine and self.volcengine.host and not uses_api_key:
                logger.warning(
                    "VectorDB volcengine backend: 'volcengine.host' is ignored in AK/SK mode. "
                    "Using region-based console/data hosts for region='%s'.",
                    self.volcengine.region or "",
                )

        elif self.backend == "vikingdb":
            if not self.vikingdb or not self.vikingdb.host:
                raise ValueError("VectorDB vikingdb backend requires 'host' to be set")

        elif self.backend == "qdrant":
            qdrant_url = (
                (self.qdrant.url if self.qdrant else None)
                or self.url
                or self.custom_params.get("url")
            )
            if not qdrant_url:
                raise ValueError("VectorDB qdrant backend requires 'qdrant.url' or 'url' to be set")
            if self.qdrant is None:
                self.qdrant = QdrantConfig()
            self.qdrant.url = str(qdrant_url).strip().rstrip("/")
            if self.url:
                self.url = self.url.strip().rstrip("/")

        return self
