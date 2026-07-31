# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict, Literal, Optional

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


class CuVSConfig(BaseModel):
    """Configuration for GPU dense-vector search through NVIDIA cuVS."""

    dtype: Literal["float32", "float16"] = Field(
        default="float32",
        description=(
            "GPU dataset and query dtype. float16 is an opt-in direct cast and "
            "must be benchmarked for recall; it does not change native CPU quantization."
        ),
    )
    algorithm: Literal["brute_force", "cagra"] = Field(
        default="brute_force",
        description=(
            "cuVS index algorithm. Start with brute_force for functional validation; "
            "use cagra for approximate search at larger scale."
        ),
    )
    build_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional keyword arguments passed to cuVS CAGRA IndexParams.",
    )
    search_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional keyword arguments passed to cuVS CAGRA SearchParams.",
    )
    fallback_to_native: bool = Field(
        default=True,
        description=(
            "Use OpenViking's native local index for sparse/hybrid search or other "
            "operations outside cuVS dense top-k."
        ),
    )
    auto_enable: bool = Field(
        default=False,
        description=(
            "When the VectorDB backend is 'local', automatically use cuVS dense search "
            "only when a visible GPU has enough free memory. The default is disabled."
        ),
    )
    auto_memory_reserve_mb: int = Field(
        default=1024,
        ge=0,
        description=("Free GPU memory kept outside the cuVS auto-admission budget, in MiB."),
    )
    auto_memory_safety_factor: float = Field(
        default=2.0,
        ge=1.0,
        description=(
            "Multiplier applied to the estimated cuVS vector, graph, build, and filter "
            "memory before auto-enabling GPU search."
        ),
    )
    auto_filter_native_threshold: int = Field(
        default=2000,
        ge=0,
        description=(
            "In cuVS auto mode, route filtered queries with at most this many "
            "eligible vectors to the native index. Set to zero to disable "
            "latency-aware filter routing."
        ),
    )
    auto_path_filter_native_threshold: int = Field(
        default=200,
        ge=0,
        description=(
            "In cuVS auto mode, use this lower native-routing threshold for path "
            "filters, whose native Trie/bitmap construction cost can dominate wider "
            "subtree queries. Set to zero to keep all path filters on cuVS."
        ),
    )
    filter_cache_size: int = Field(
        default=16,
        ge=0,
        description=(
            "Maximum number of repeated scalar-filter bitsets retained on the GPU. "
            "Set to zero to disable caching."
        ),
    )
    max_concurrent_gpu_searches: int = Field(
        default=1,
        ge=1,
        description=(
            "Maximum in-flight cuVS GPU search calls per index. Host-side filter and "
            "snapshot work remains concurrent; increase only after hardware-specific tuning."
        ),
    )
    micro_batching_enabled: bool = Field(
        default=False,
        description=(
            "Coalesce compatible concurrent cuVS dense queries into one matrix-search call. "
            "This OpenViking scheduler is opt-in and distinct from cuVS Dynamic Batching."
        ),
    )
    micro_batching_max_batch_size: int = Field(
        default=8,
        ge=1,
        le=8,
        description="Maximum compatible queries submitted in one cuVS search call.",
    )
    micro_batching_max_wait_ms: float = Field(
        default=1.0,
        ge=0.0,
        le=100.0,
        allow_inf_nan=False,
        description=(
            "Maximum collection window for a compatible cuVS micro-batch, in milliseconds. "
            "Zero performs opportunistic batching without an intentional wait."
        ),
    )
    auto_background_rebuild: bool = Field(
        default=False,
        description=(
            "Build dirty auto-cuVS snapshots in a coalescing background worker. "
            "Queries use the native index until the new GPU snapshot is committed."
        ),
    )
    auto_rebuild_debounce_ms: int = Field(
        default=500,
        ge=0,
        description=(
            "Quiet period used to coalesce consecutive mutations before an auto-cuVS "
            "background rebuild."
        ),
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_micro_batching(self):
        if not self.micro_batching_enabled:
            return self
        if self.algorithm != "brute_force":
            raise ValueError("cuVS micro-batching currently supports algorithm='brute_force' only")
        if self.max_concurrent_gpu_searches != 1:
            raise ValueError("cuVS micro-batching currently requires max_concurrent_gpu_searches=1")
        return self


_OPENGAUSS_MODES = {"standalone", "distributed"}


class OpenGaussConfig(BaseModel):
    """Configuration for openGauss native vector backend."""

    host: Optional[str] = Field(
        default="127.0.0.1",
        description="openGauss host address. Use the CN address when mode=distributed.",
    )
    port: int = Field(default=5432, description="openGauss port")
    user: str = Field(default="omm", description="Database user")
    password: str = Field(default="", description="Database password")
    db_name: str = Field(default="postgres", description="Database name")
    schema_name: str = Field(
        default="public",
        alias="schema",
        description="Database schema for OpenViking tables",
    )
    mode: str = Field(
        default="standalone",
        description="openGauss deployment mode: 'standalone' or 'distributed'",
    )
    shard_count: int = Field(
        default=32,
        description="Shard count for create_distributed_table when mode=distributed",
    )
    connect_timeout: int = Field(default=10, description="Database connection timeout in seconds")
    dense_vector_name: str = Field(default="vector", description="Dense vector column name")
    sparse_vector_name: str = Field(
        default="sparse_vector", description="Sparse vector JSON column name"
    )

    model_config = {"extra": "forbid", "populate_by_name": True}

    @model_validator(mode="after")
    def validate_mode(self):
        self.schema_name = (self.schema_name or "public").strip()
        if not self.schema_name:
            raise ValueError("openGauss schema must not be empty")
        self.mode = (self.mode or "standalone").strip().lower()
        if self.mode not in _OPENGAUSS_MODES:
            raise ValueError(
                f"Invalid openGauss mode: '{self.mode}'. Must be one of: {sorted(_OPENGAUSS_MODES)}"
            )
        self.dense_vector_name = (self.dense_vector_name or "vector").strip()
        self.sparse_vector_name = (self.sparse_vector_name or "sparse_vector").strip()
        return self


class VectorDBBackendConfig(BaseModel):
    """
    Configuration for VectorDB backend.

    This configuration class consolidates all settings related to the VectorDB backend,
    including type, connection details, and backend-specific parameters.
    """

    backend: str = Field(
        default="local",
        description=(
            "VectorDB backend type: 'local', 'cuvs', 'http', "
            "'volcengine' (AK/SK signed or API key data-plane only), "
            "'vikingdb' (private deployment), 'qdrant', or 'opengauss'"
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

    cuvs: Optional[CuVSConfig] = Field(
        default_factory=CuVSConfig,
        description="NVIDIA cuVS dense-vector search configuration for the 'cuvs' backend",
    )

    opengauss: Optional[OpenGaussConfig] = Field(
        default_factory=OpenGaussConfig,
        description="openGauss configuration for 'opengauss' type",
    )

    custom_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Custom parameters for custom backend adapters",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        standard_backends = [
            "local",
            "cuvs",
            "http",
            "volcengine",
            "vikingdb",
            "qdrant",
            "opengauss",
        ]

        # Allow custom backend classes (containing dot) without standard validation
        if "." in self.backend:
            logger.info("Using custom VectorDB backend: %s", self.backend)
            return self

        if self.backend not in standard_backends:
            raise ValueError(
                f"Invalid VectorDB backend: '{self.backend}'. Must be one of: {standard_backends} "
                "or a valid Python class path."
            )

        if self.backend in {"local", "cuvs"}:
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

        elif self.backend == "opengauss":
            if self.opengauss is None:
                self.opengauss = OpenGaussConfig()
            if not self.opengauss.host:
                raise ValueError("VectorDB opengauss backend requires 'opengauss.host' to be set")
            self.opengauss.host = self.opengauss.host.strip()

        return self
