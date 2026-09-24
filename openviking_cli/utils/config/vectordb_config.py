# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, StrictInt, model_validator

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


class VikingDBConfig(BaseModel):
    """Configuration for VikingDB private deployment."""

    host: Optional[str] = Field(default=None, description="VikingDB service host")
    headers: Optional[Dict[str, str]] = Field(
        default_factory=dict, description="Custom headers for requests"
    )


_OPENGAUSS_MODES = frozenset({"standalone", "distributed"})
# Distance metrics with a DataVec operator class; l1 is plain-HNSW only.
_OPENGAUSS_DISTANCE_METRICS = frozenset({"cosine", "l2", "ip", "l1"})
_OPENGAUSS_INDEX_TYPES = frozenset(
    {
        "hnsw",
        "hnsw-pq",
        "hnsw-rabitq",
        "ivfflat",
        "ivf-pq",
        "ivf-rabitq",
        "diskann",
    }
)
_OPENGAUSS_INDEX_TYPE_ALIASES = {
    "hnsw_pq": "hnsw-pq",
    "hnswpq": "hnsw-pq",
    "hnsw_rabitq": "hnsw-rabitq",
    "hnswrabitq": "hnsw-rabitq",
    "ivf_flat": "ivfflat",
    "ivfflat-pq": "ivf-pq",
    "ivf_pq": "ivf-pq",
    "ivfpq": "ivf-pq",
    "ivfflat-rabitq": "ivf-rabitq",
    "ivf_rabitq": "ivf-rabitq",
    "ivfrabitq": "ivf-rabitq",
}
_OPENGAUSS_COMMON_QUANT_BUILD_PARAMS = frozenset(
    {"pq_m", "pq_ksub", "rabitq_refine_type", "rabitq_fht"}
)
_OPENGAUSS_BUILD_PARAMS = {
    "hnsw": frozenset({"m", "ef_construction"}) | _OPENGAUSS_COMMON_QUANT_BUILD_PARAMS,
    "ivfflat": frozenset({"lists", "by_residual"}) | _OPENGAUSS_COMMON_QUANT_BUILD_PARAMS,
    "diskann": frozenset({"index_size"}),
}
_OPENGAUSS_SEARCH_PARAMS = {
    "hnsw": frozenset({"ef_search", "earlystop_threshold", "rbq_query_bits", "rbq_refinek"}),
    "ivfflat": frozenset({"probes", "ivfpq_kreorder", "rbq_query_bits", "rbq_refinek"}),
    "diskann": frozenset({"probes"}),
}
_OPENGAUSS_SEARCH_ALIASES = {
    "hnsw": {"hnsw_ef_search": "ef_search", "hnsw_earlystop_threshold": "earlystop_threshold"},
    "ivfflat": {"ivfflat_probes": "probes"},
    "diskann": {"diskann_probes": "probes"},
}


def normalize_opengauss_index_type(index_type: str | None) -> str:
    value = (index_type or "hnsw").strip().lower()
    value = _OPENGAUSS_INDEX_TYPE_ALIASES.get(value, value)
    if value not in _OPENGAUSS_INDEX_TYPES:
        raise ValueError(
            f"Invalid openGauss index_type: {value!r}. "
            f"Must be one of: {sorted(_OPENGAUSS_INDEX_TYPES)}"
        )
    return value


def resolve_opengauss_index_spec(index_type: str) -> tuple[str, Optional[str]]:
    normalized = normalize_opengauss_index_type(index_type)
    if normalized.startswith("hnsw"):
        access_method = "hnsw"
    elif normalized.startswith("ivf"):
        access_method = "ivfflat"
    else:
        access_method = "diskann"

    if normalized.endswith("-pq"):
        quantization = "pq"
    elif normalized.endswith("-rabitq"):
        quantization = "rabitq"
    else:
        quantization = None
    return access_method, quantization


class OpenGaussIndexConfig(BaseModel):
    """Canonical adapter options; database-specific numerical limits belong to DataVec."""

    index_type: str = "hnsw"
    build_params: Dict[str, Any] = Field(default_factory=dict)
    search_params: Dict[str, Any] = Field(default_factory=dict)
    parallel_workers: StrictInt = Field(default=0, ge=0)
    maintenance_work_mem_mb: StrictInt = Field(default=64, gt=0)
    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def normalize_index_options(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        index_type = normalize_opengauss_index_type(data.get("index_type", "hnsw"))
        method, quantization = resolve_opengauss_index_spec(index_type)
        build = data.get("build_params", {})
        search = data.get("search_params", {})
        if not isinstance(build, dict) or not isinstance(search, dict):
            raise ValueError("openGauss build_params and search_params must be objects")
        build, search = dict(build), dict(search)
        flags = {}
        for name in ("enable_pq", "enable_rabitq"):
            if name in build:
                flags[name] = build.pop(name)
                if type(flags[name]) is not bool:
                    raise ValueError(f"openGauss build_params.{name} must be a boolean")
        for name, kind in (("enable_pq", "pq"), ("enable_rabitq", "rabitq")):
            if flags.get(name) is False and quantization == kind:
                raise ValueError(f"openGauss {name}=false conflicts with index_type={index_type}")
            if flags.get(name):
                if quantization and quantization != kind:
                    raise ValueError("openGauss PQ and RabitQ cannot be enabled together")
                if method == "diskann":
                    raise ValueError("openGauss DiskANN does not support PQ or RabitQ")
                quantization = kind
        if quantization:
            index_type = f"{'ivf' if method == 'ivfflat' else method}-{quantization}"
        for alias, canonical in _OPENGAUSS_SEARCH_ALIASES[method].items():
            if alias in search:
                alias_value = search.pop(alias)
                if canonical in search and search[canonical] != alias_value:
                    raise ValueError(f"Conflicting openGauss search_params {alias} and {canonical}")
                search[canonical] = alias_value
        for group, params, allowed in (
            ("build_params", build, _OPENGAUSS_BUILD_PARAMS[method]),
            ("search_params", search, _OPENGAUSS_SEARCH_PARAMS[method]),
        ):
            unknown = sorted(set(params) - allowed)
            if unknown:
                raise ValueError(f"Unsupported openGauss {index_type} {group}: {unknown}")
            for name, parameter in params.items():
                if name in {"by_residual", "rabitq_fht"}:
                    if type(parameter) is not bool:
                        raise ValueError(f"openGauss {group}.{name} must be a boolean")
                elif name == "rabitq_refine_type":
                    if not isinstance(parameter, str):
                        raise ValueError(f"openGauss {group}.{name} must be a string")
                    params[name] = parameter.lower()
                elif type(parameter) is not int:
                    raise ValueError(f"openGauss {group}.{name} must be an integer")
        if quantization != "pq" and (
            {"pq_m", "pq_ksub", "by_residual"} & build.keys() or "ivfpq_kreorder" in search
        ):
            raise ValueError("openGauss PQ parameters require a *-pq index_type")
        if quantization != "rabitq" and (
            {"rabitq_refine_type", "rabitq_fht"} & build.keys()
            or {"rbq_query_bits", "rbq_refinek"} & search.keys()
        ):
            raise ValueError("openGauss RabitQ parameters require a *-rabitq index_type")
        data.update(index_type=index_type, build_params=build, search_params=search)
        return data

    @property
    def access_method(self) -> str:
        return resolve_opengauss_index_spec(self.index_type)[0]

    @property
    def quantization(self) -> Optional[str]:
        return resolve_opengauss_index_spec(self.index_type)[1]

    def validate_capabilities(self, *, distributed: bool, distance: str = "cosine") -> None:
        if distributed and self.index_type != "hnsw":
            raise ValueError(
                "openGauss SPQ distributed mode currently supports only plain HNSW; "
                "use standalone mode for PQ, RabitQ, IVF, or DiskANN indexes"
            )
        if distance not in _OPENGAUSS_DISTANCE_METRICS:
            raise ValueError("Unsupported openGauss distance_metric")
        if distance == "l1" and self.index_type != "hnsw":
            raise ValueError("openGauss distance='l1' requires plain hnsw without PQ or RabitQ")


class OpenGaussConfig(OpenGaussIndexConfig):
    """Configuration for openGauss DataVec vector database."""

    host: str = Field(
        default="127.0.0.1",
        description="openGauss host address (CN node when mode=distributed)",
    )
    port: int = Field(default=5432, ge=1, le=65535, description="openGauss port")
    user: str = Field(default="gaussdb", description="Database user")
    password: str = Field(default="", description="Database password")
    db_name: str = Field(default="openviking", description="Database name")
    mode: Literal["standalone", "distributed"] = Field(
        default="standalone",
        description="Deployment mode; distributed connects to an spq CN node.",
    )
    shard_count: int = Field(
        default=32,
        ge=1,
        description="Number of shards per distributed collection table.",
    )
    connection_pool_min_size: int = Field(default=1, ge=1, le=64)
    connection_pool_max_size: int = Field(default=8, ge=1, le=128)

    model_config = {"extra": "forbid"}

    @property
    def is_distributed(self) -> bool:
        return self.mode == "distributed"

    @model_validator(mode="after")
    def validate_opengauss(self):
        self.validate_capabilities(distributed=self.is_distributed)
        if self.connection_pool_min_size > self.connection_pool_max_size:
            raise ValueError(
                "openGauss connection_pool_min_size cannot exceed connection_pool_max_size"
            )
        return self


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

    @model_validator(mode="after")
    def validate_micro_batching(self):
        if not self.micro_batching_enabled:
            return self
        if self.algorithm != "brute_force":
            raise ValueError("cuVS micro-batching currently supports algorithm='brute_force' only")
        if self.max_concurrent_gpu_searches != 1:
            raise ValueError("cuVS micro-batching currently requires max_concurrent_gpu_searches=1")
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
            "'vikingdb' (private deployment), or 'opengauss'"
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

    cuvs: Optional[CuVSConfig] = Field(
        default_factory=CuVSConfig,
        description="NVIDIA cuVS dense-vector search configuration for the 'cuvs' backend",
    )

    opengauss: Optional[OpenGaussConfig] = Field(
        default_factory=OpenGaussConfig,
        description="openGauss DataVec configuration for the 'opengauss' backend",
    )

    custom_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Custom parameters for custom backend adapters",
    )

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        standard_backends = [
            "local",
            "cuvs",
            "http",
            "volcengine",
            "vikingdb",
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

        elif self.backend == "opengauss":
            if not self.opengauss:
                raise ValueError("VectorDB opengauss backend requires 'opengauss' config")
            if not self.opengauss.host:
                raise ValueError("VectorDB opengauss backend requires 'opengauss.host' to be set")
            if self.sparse_weight > 0.0:
                raise ValueError("VectorDB opengauss backend does not support sparse_weight > 0")
            distance = (self.distance_metric or "cosine").lower()
            if distance not in _OPENGAUSS_DISTANCE_METRICS:
                raise ValueError(
                    "VectorDB opengauss backend supports distance_metric values: "
                    + ", ".join(sorted(_OPENGAUSS_DISTANCE_METRICS))
                )
            self.distance_metric = distance
            self.opengauss.validate_capabilities(
                distributed=self.opengauss.is_distributed, distance=distance
            )

        return self

    def apply_resolved_dimension(self, embedding_dimension: Any) -> None:
        """Resolve an unspecified collection dimension from the embedding configuration."""
        if int(self.dimension or 0) == 0 and embedding_dimension:
            self.dimension = int(embedding_dimension)
