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

    model_config = {"extra": "forbid"}


class VikingDBConfig(BaseModel):
    """Configuration for VikingDB private deployment."""

    host: Optional[str] = Field(default=None, description="VikingDB service host")
    headers: Optional[Dict[str, str]] = Field(
        default_factory=dict, description="Custom headers for requests"
    )

    model_config = {"extra": "forbid"}



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
    {
        "enable_pq",
        "enable_rabitq",
        "pq_m",
        "pq_ksub",
        "rabitq_refine_type",
        "rabitq_fht",
    }
)
_OPENGAUSS_BUILD_PARAMS = {
    "hnsw": frozenset({"m", "ef_construction"}) | _OPENGAUSS_COMMON_QUANT_BUILD_PARAMS,
    "ivfflat": frozenset({"lists", "by_residual"}) | _OPENGAUSS_COMMON_QUANT_BUILD_PARAMS,
    "diskann": frozenset({"index_size"}),
}
_OPENGAUSS_SEARCH_PARAMS = {
    "hnsw": frozenset(
        {
            "ef_search",
            "hnsw_ef_search",
            "earlystop_threshold",
            "hnsw_earlystop_threshold",
            "rbq_query_bits",
            "rbq_refinek",
        }
    ),
    "ivfflat": frozenset(
        {
            "probes",
            "ivfflat_probes",
            "ivfpq_kreorder",
            "rbq_query_bits",
            "rbq_refinek",
        }
    ),
    "diskann": frozenset({"probes", "diskann_probes"}),
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


def validate_opengauss_vector_constraints(
    *,
    index_type: str,
    build_params: Optional[Dict[str, Any]],
    dimension: int,
) -> None:
    """Reject resolved dimensions that DataVec cannot index.

    Startup may leave ``dimension=0`` and fill it later from embedding
    config. Call this again after that fill so DiskANN/RabitQ limits and
    ``dimension % pq_m`` are not skipped.
    """
    dimension = int(dimension or 0)
    if dimension <= 0:
        return

    access_method, quantization = resolve_opengauss_index_spec(index_type)
    params = dict(build_params or {})
    # Explicit build_params flags are as authoritative as the index_type
    # suffix: ``hnsw`` + ``enable_rabitq`` still builds a RabitQ index.
    effective_pq = bool(params.get("enable_pq")) or quantization == "pq"
    effective_rabitq = bool(params.get("enable_rabitq")) or quantization == "rabitq"
    if access_method == "diskann" and effective_pq:
        raise ValueError("VectorDB opengauss does not support DiskANN-PQ")
    if access_method == "diskann" and dimension > 1536:
        raise ValueError(
            "VectorDB opengauss DiskANN supports vector dimensions up to 1536"
        )
    if effective_rabitq and dimension > 2000:
        raise ValueError(
            "VectorDB opengauss RabitQ supports vector dimensions up to 2000"
        )
    if access_method in {"hnsw", "ivfflat"} and dimension > 16000:
        raise ValueError(
            "VectorDB opengauss HNSW/IVFFlat supports vector dimensions up to 16000"
        )
    if effective_pq:
        pq_m = int(params.get("pq_m", 8))
        if pq_m <= 0 or dimension % pq_m != 0:
            raise ValueError(
                "VectorDB opengauss PQ requires dimension to be divisible by build_params.pq_m"
            )


def _require_integer_range(
    params: Dict[str, Any],
    name: str,
    minimum: int,
    maximum: int,
    *,
    parameter_group: str,
) -> Optional[int]:
    value = params.get(name)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"openGauss {parameter_group}.{name} must be an integer")
    try:
        integer_value = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"openGauss {parameter_group}.{name} must be an integer"
        ) from error
    if integer_value != value or not minimum <= integer_value <= maximum:
        raise ValueError(
            f"openGauss {parameter_group}.{name} must be in [{minimum}, {maximum}]"
        )
    params[name] = integer_value
    return integer_value


def _require_boolean(params: Dict[str, Any], name: str, *, parameter_group: str) -> bool:
    if name not in params:
        return False
    value = params[name]
    if not isinstance(value, bool):
        raise ValueError(f"openGauss {parameter_group}.{name} must be a boolean")
    params[name] = value
    return value


class OpenGaussConfig(BaseModel):
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
    index_type: str = Field(
        default="hnsw",
        description=(
            "DataVec logical ANN index type: hnsw, hnsw-pq, hnsw-rabitq, "
            "ivfflat, ivf-pq, ivf-rabitq, or diskann."
        ),
    )
    build_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="DataVec CREATE INDEX WITH parameters for the selected index type.",
    )
    search_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="DataVec session search parameters for the selected index type.",
    )
    parallel_workers: StrictInt = Field(
        default=0,
        ge=0,
        le=32,
        description="Table parallel_workers used while building or rebuilding an ANN index.",
    )
    maintenance_work_mem_mb: int = Field(
        default=64,
        ge=16,
        le=1_048_576,
        description=(
            "Transaction-local maintenance_work_mem in MiB used while building or "
            "rebuilding an ANN index."
        ),
    )
    connection_pool_min_size: int = Field(default=1, ge=1, le=64)
    connection_pool_max_size: int = Field(default=8, ge=1, le=128)

    model_config = {"extra": "forbid"}

    @property
    def is_distributed(self) -> bool:
        return self.mode == "distributed"

    @property
    def access_method(self) -> str:
        return resolve_opengauss_index_spec(self.index_type)[0]

    @property
    def quantization(self) -> Optional[str]:
        return resolve_opengauss_index_spec(self.index_type)[1]

    @model_validator(mode="after")
    def validate_opengauss(self):
        self.index_type = normalize_opengauss_index_type(self.index_type)
        access_method, quantization = resolve_opengauss_index_spec(self.index_type)
        build = dict(self.build_params or {})
        search = dict(self.search_params or {})

        unknown_build = sorted(set(build) - _OPENGAUSS_BUILD_PARAMS[access_method])
        if unknown_build:
            raise ValueError(
                f"Unsupported openGauss {self.index_type} build_params: {unknown_build}"
            )
        unknown_search = sorted(set(search) - _OPENGAUSS_SEARCH_PARAMS[access_method])
        if unknown_search:
            raise ValueError(
                f"Unsupported openGauss {self.index_type} search_params: {unknown_search}"
            )

        requested_pq = _require_boolean(build, "enable_pq", parameter_group="build_params")
        requested_rabitq = _require_boolean(
            build, "enable_rabitq", parameter_group="build_params"
        )
        if quantization == "pq":
            requested_pq = True
            build["enable_pq"] = True
        elif quantization == "rabitq":
            requested_rabitq = True
            build["enable_rabitq"] = True
        if requested_pq and requested_rabitq:
            raise ValueError("openGauss PQ and RabitQ cannot be enabled together")
        if access_method == "diskann" and requested_rabitq:
            raise ValueError("openGauss DiskANN does not support RabitQ")
        if self.is_distributed and self.index_type != "hnsw":
            raise ValueError(
                "openGauss SPQ distributed mode currently supports only plain HNSW; "
                "use standalone mode for PQ, RabitQ, IVF, or DiskANN indexes"
            )

        if access_method == "hnsw":
            m = _require_integer_range(
                build, "m", 2, 100, parameter_group="build_params"
            )
            ef_construction = _require_integer_range(
                build,
                "ef_construction",
                4,
                1000,
                parameter_group="build_params",
            )
            effective_m = m if m is not None else 16
            effective_ef = ef_construction if ef_construction is not None else 64
            if effective_ef < 2 * effective_m:
                raise ValueError(
                    "openGauss hnsw build_params.ef_construction must be >= 2 * m"
                )
            for parameter_name in ("ef_search", "hnsw_ef_search"):
                _require_integer_range(
                    search,
                    parameter_name,
                    1,
                    32768,
                    parameter_group="search_params",
                )
            for parameter_name in ("earlystop_threshold", "hnsw_earlystop_threshold"):
                _require_integer_range(
                    search,
                    parameter_name,
                    0,
                    32767,
                    parameter_group="search_params",
                )
        elif access_method == "ivfflat":
            _require_integer_range(
                build, "lists", 1, 32768, parameter_group="build_params"
            )
            for parameter_name in ("probes", "ivfflat_probes"):
                _require_integer_range(
                    search,
                    parameter_name,
                    1,
                    32768,
                    parameter_group="search_params",
                )
            _require_integer_range(
                search,
                "ivfpq_kreorder",
                0,
                32768,
                parameter_group="search_params",
            )
            if "by_residual" in build:
                _require_boolean(build, "by_residual", parameter_group="build_params")
        else:
            _require_integer_range(
                build, "index_size", 16, 1000, parameter_group="build_params"
            )
            for parameter_name in ("probes", "diskann_probes"):
                _require_integer_range(
                    search,
                    parameter_name,
                    1,
                    32768,
                    parameter_group="search_params",
                )

        if requested_pq:
            _require_integer_range(
                build, "pq_m", 1, 2000, parameter_group="build_params"
            )
            _require_integer_range(
                build, "pq_ksub", 1, 256, parameter_group="build_params"
            )
        else:
            if "pq_m" in build:
                _require_integer_range(
                    build, "pq_m", 1, 2000, parameter_group="build_params"
                )
            if "pq_ksub" in build:
                _require_integer_range(
                    build, "pq_ksub", 1, 256, parameter_group="build_params"
                )
            if build.get("by_residual"):
                raise ValueError("openGauss build_params.by_residual requires IVF-PQ")
            if "ivfpq_kreorder" in search:
                raise ValueError(
                    "openGauss search_params.ivfpq_kreorder requires IVF-PQ"
                )

        if requested_rabitq:
            refine_type = str(build.get("rabitq_refine_type", "none")).lower()
            if refine_type not in {"none", "sq8", "fp32"}:
                raise ValueError(
                    "openGauss build_params.rabitq_refine_type must be none, SQ8, or FP32"
                )
            build["rabitq_refine_type"] = refine_type
            if "rabitq_fht" in build:
                _require_boolean(build, "rabitq_fht", parameter_group="build_params")
            _require_integer_range(
                search,
                "rbq_query_bits",
                1,
                8,
                parameter_group="search_params",
            )
            _require_integer_range(
                search,
                "rbq_refinek",
                0,
                32768,
                parameter_group="search_params",
            )
        else:
            forbidden_rabitq_parameters = sorted(
                parameter_name
                for parameter_name in ("rabitq_refine_type", "rabitq_fht")
                if parameter_name in build
            )
            forbidden_rabitq_parameters.extend(
                parameter_name
                for parameter_name in ("rbq_query_bits", "rbq_refinek")
                if parameter_name in search
            )
            if forbidden_rabitq_parameters:
                raise ValueError(
                    "openGauss RabitQ parameters require enable_rabitq=true or a "
                    f"*-rabitq index_type: {sorted(forbidden_rabitq_parameters)}"
                )

        if self.connection_pool_min_size > self.connection_pool_max_size:
            raise ValueError(
                "openGauss connection_pool_min_size cannot exceed connection_pool_max_size"
            )
        self.build_params = build
        self.search_params = search
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
                raise ValueError(
                    "VectorDB opengauss backend does not support sparse_weight > 0"
                )
            distance = (self.distance_metric or "cosine").lower()
            if distance not in _OPENGAUSS_DISTANCE_METRICS:
                raise ValueError(
                    "VectorDB opengauss backend supports distance_metric values: "
                    + ", ".join(sorted(_OPENGAUSS_DISTANCE_METRICS))
                )
            self.distance_metric = distance
            access_method, quantization = resolve_opengauss_index_spec(
                self.opengauss.index_type
            )
            build = self.opengauss.build_params or {}
            effectively_quantized = (
                quantization is not None
                or bool(build.get("enable_pq"))
                or bool(build.get("enable_rabitq"))
            )
            if distance == "l1" and (access_method != "hnsw" or effectively_quantized):
                raise ValueError(
                    "VectorDB opengauss backend: distance_metric='l1' requires plain "
                    "index_type='hnsw' without PQ or RabitQ"
                )

            self.validate_opengauss_vector_constraints()

        return self

    def apply_resolved_dimension(self, embedding_dimension: Any) -> None:
        """Copy embedding dimension when unset, then re-run openGauss checks."""
        if int(self.dimension or 0) == 0 and embedding_dimension:
            self.dimension = int(embedding_dimension)
        self.validate_opengauss_vector_constraints()

    def validate_opengauss_vector_constraints(self) -> None:
        if self.backend != "opengauss" or not self.opengauss:
            return
        validate_opengauss_vector_constraints(
            index_type=self.opengauss.index_type,
            build_params=self.opengauss.build_params,
            dimension=int(self.dimension or 0),
        )
