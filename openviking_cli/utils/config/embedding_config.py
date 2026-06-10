# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, ClassVar, List, Literal, Optional, Tuple, cast

from pydantic import BaseModel, Field, model_validator


class EmbeddingCredential(BaseModel):
    """Single embedding credential configuration for multi-credential failover."""

    id: Optional[str] = Field(default=None, description="Unique identifier for this credential")
    provider: Optional[str] = Field(default=None, description="Provider type")
    model: Optional[str] = Field(
        default=None,
        description=(
            "Model name (or endpoint id) for this credential. "
            "Overrides the parent EmbeddingModelConfig.model when set, allowing "
            "each credential to point to a different deployment / endpoint."
        ),
    )
    api_key: Optional[str] = Field(default=None, description="API key")
    api_base: Optional[str] = Field(default=None, description="API base URL")
    api_version: Optional[str] = Field(default=None, description="API version")
    ak: Optional[str] = Field(default=None, description="Access Key ID for VikingDB API")
    sk: Optional[str] = Field(default=None, description="Access Key Secret for VikingDB API")
    region: Optional[str] = Field(default=None, description="Region for VikingDB API")
    host: Optional[str] = Field(default=None, description="Host for VikingDB API")
    extra_headers: Optional[dict[str, str]] = Field(default=None, description="Extra HTTP headers")

    model_config = {"extra": "forbid"}


class EmbeddingModelConfig(BaseModel):
    """Configuration for a specific embedding model"""

    model: Optional[str] = Field(default=None, description="Model name")
    api_key: Optional[str] = Field(default=None, description="API key")
    api_base: Optional[str] = Field(default=None, description="API base URL")
    dimension: Optional[int] = Field(default=None, description="Embedding dimension")
    batch_size: int = Field(default=32, description="Batch size for embedding generation")
    input: str = Field(default="multimodal", description="Input type: 'text' or 'multimodal'")
    query_param: Optional[str] = Field(
        default=None,
        description=(
            "Parameter value for query-side embeddings when calling embed(is_query=True). "
            "For OpenAI-compatible models, this maps to 'input_type' (e.g., 'query', 'search_query'). "
            "For Jina models, this maps to 'task' (e.g., 'retrieval.query'). "
            "Setting this or document_param activates non-symmetric mode. "
            "Leave both unset for symmetric models."
        ),
    )
    document_param: Optional[str] = Field(
        default=None,
        description=(
            "Parameter value for document-side embeddings when calling embed(is_query=False). "
            "For OpenAI-compatible models, this maps to 'input_type' (e.g., 'passage', 'document'). "
            "For Jina models, this maps to 'task' (e.g., 'retrieval.passage'). "
            "Setting this or query_param activates non-symmetric mode. "
            "Leave both unset for symmetric models."
        ),
    )
    provider: Optional[str] = Field(
        default="volcengine",
        description=(
            "Provider type: 'openai', 'volcengine', 'vikingdb', 'jina', 'ollama', 'gemini', 'voyage', 'dashscope', 'minimax', 'cohere', 'litellm', 'local'. "
            "For OpenRouter or other OpenAI-compatible providers, use 'litellm' with "
            "api_base and api_key, or 'openai' with api_base and extra_headers."
        ),
    )
    backend: Optional[str] = Field(
        default="volcengine",
        description="Backend type (Deprecated, use 'provider' instead): 'openai', 'volcengine', 'vikingdb', 'voyage', 'local'",
    )
    version: Optional[str] = Field(default=None, description="Model version")
    ak: Optional[str] = Field(default=None, description="Access Key ID for VikingDB API")
    sk: Optional[str] = Field(default=None, description="Access Key Secretfor VikingDB API")
    region: Optional[str] = Field(default=None, description="Region for VikingDB API")
    host: Optional[str] = Field(default=None, description="Host for VikingDB API")
    extra_headers: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "Extra HTTP headers for API requests. Passed as default_headers to the OpenAI client. "
            "Useful for OpenRouter (e.g., {'HTTP-Referer': '...', 'X-Title': '...'}) "
            "or other OpenAI-compatible providers that require custom headers."
        ),
    )
    encoding_format: Optional[Literal["float", "base64"]] = Field(
        default=None,
        description=(
            "Wire format for embedding values. Applies to OpenAI / Azure providers. "
            "Leave unset to use the OpenAI Python SDK default (currently 'base64', "
            "which is bandwidth-efficient and decoded client-side). Set to 'float' "
            "to send/receive plain JSON arrays. This is the recommended workaround "
            "when the upstream gateway cannot serialize base64 responses correctly."
        ),
    )
    api_version: Optional[str] = Field(
        default=None,
        description="API version for Azure OpenAI (e.g., '2025-01-01-preview').",
    )
    model_path: Optional[str] = Field(
        default=None,
        description="Explicit local GGUF model path for provider='local'.",
    )
    cache_dir: Optional[str] = Field(
        default=None,
        description="Local model cache directory for provider='local'.",
    )
    enable_fusion: Optional[bool] = Field(
        default=None,
        description="Enable multimodal fusion for DashScope provider (multimodal models only).",
    )
    res_level: Optional[int] = Field(
        default=None,
        description="Resolution level for DashScope multimodal models (multimodal models only).",
    )
    max_video_frames: Optional[int] = Field(
        default=None,
        description="Maximum video frames for DashScope multimodal models (multimodal models only).",
    )

    # New multi-credential configuration
    credentials: List[EmbeddingCredential] = Field(
        default_factory=list,
        description="Ordered list of credentials for failover. Call order matches array index (0 is highest priority).",
    )

    failback_timeout_seconds: float = Field(
        default=600.0, description="Time in seconds after which to attempt failback to primary"
    )
    failback_request_count: int = Field(
        default=50, description="Number of backup requests after which to attempt failback"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def sync_provider_backend(cls, data: Any) -> Any:
        if isinstance(data, dict):
            provider = data.get("provider")
            backend = data.get("backend")

            if backend is not None and provider is None:
                data["provider"] = backend
            for key in ("query_param", "document_param"):
                value = data.get(key)
                if isinstance(value, str):
                    data[key] = value.lower()
        return data

    _VALID_PROVIDERS: ClassVar[Tuple[str, ...]] = (
        "openai",
        "azure",
        "volcengine",
        "vikingdb",
        "jina",
        "ollama",
        "gemini",
        "voyage",
        "dashscope",
        "minimax",
        "cohere",
        "litellm",
        "local",
    )

    @classmethod
    def _validate_provider_auth(
        cls,
        *,
        label: str,
        provider: Optional[str],
        api_key: Optional[str],
        api_base: Optional[str],
        ak: Optional[str],
        sk: Optional[str],
        region: Optional[str],
    ) -> None:
        """Validate provider name and provider-specific auth requirements.

        Shared by both the top-level config validator (single-credential mode)
        and the per-credential validator (multi-credential mode).

        Args:
            label: Prefix used in error messages, e.g. "Embedding" or
                "credentials[ark-primary]".
            provider: Provider name (already lowercased / normalized).
            api_key, api_base, ak, sk, region: Resolved auth fields, with
                credential values taking precedence over parent fallbacks
                in multi-credential mode.
        """
        if not provider:
            raise ValueError(f"{label}: provider is required")
        if provider not in cls._VALID_PROVIDERS:
            raise ValueError(
                f"{label}: invalid provider '{provider}'. Must be one of: "
                + ", ".join(f"'{p}'" for p in cls._VALID_PROVIDERS)
            )

        if provider == "openai":
            # Allow missing api_key when api_base is set (e.g. local OpenAI-compatible servers)
            if not api_key and not api_base:
                raise ValueError(f"{label}: OpenAI provider requires 'api_key' to be set")
        elif provider == "azure":
            if not api_key:
                raise ValueError(f"{label}: Azure provider requires 'api_key' to be set")
            if not api_base:
                raise ValueError(
                    f"{label}: Azure provider requires 'api_base' (Azure endpoint) to be set"
                )
        elif provider in {
            "volcengine",
            "jina",
            "gemini",
            "voyage",
            "minimax",
            "cohere",
            "dashscope",
        }:
            if not api_key:
                provider_label = {
                    "volcengine": "Volcengine",
                    "jina": "Jina",
                    "gemini": "Gemini",
                    "voyage": "Voyage",
                    "minimax": "MiniMax",
                    "cohere": "Cohere",
                    "dashscope": "DashScope",
                }[provider]
                raise ValueError(f"{label}: {provider_label} provider requires 'api_key' to be set")
        elif provider == "vikingdb":
            missing = [n for n, v in (("ak", ak), ("sk", sk), ("region", region)) if not v]
            if missing:
                raise ValueError(
                    f"{label}: VikingDB provider requires the following fields: "
                    f"{', '.join(missing)}"
                )
        # ollama / litellm / local: no auth requirement enforced here

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        if self.backend and not self.provider:
            self.provider = self.backend

        if not self.model and not any(c.model for c in self.credentials):
            raise ValueError("Embedding model name is required")

        # When credentials are configured, defer provider/api_key validation to
        # per-credential checks; the parent-level provider/api_key may be left
        # blank because each credential carries its own settings.
        if self.credentials:
            self._validate_credentials()
            return self

        self._validate_provider_auth(
            label="Embedding",
            provider=self.provider,
            api_key=self.api_key,
            api_base=self.api_base,
            ak=self.ak,
            sk=self.sk,
            region=self.region,
        )

        # Provider-specific extras that depend on the model/input fields rather
        # than on credentials (kept only on the parent path).
        if self.provider == "gemini":
            _GEMINI_TASK_TYPES = {
                "RETRIEVAL_QUERY",
                "RETRIEVAL_DOCUMENT",
                "SEMANTIC_SIMILARITY",
                "CLASSIFICATION",
                "CLUSTERING",
                "QUESTION_ANSWERING",
                "FACT_VERIFICATION",
                "CODE_RETRIEVAL_QUERY",
            }
            for field_name, value in [
                ("query_param", self.query_param),
                ("document_param", self.document_param),
            ]:
                if value and value.upper() not in _GEMINI_TASK_TYPES:
                    raise ValueError(
                        f"Invalid {field_name} '{value}' for Gemini. "
                        f"Valid task_types: {', '.join(sorted(_GEMINI_TASK_TYPES))}"
                    )

        elif self.provider == "dashscope":
            if self.input == "text" and (
                self.enable_fusion is not None
                or self.res_level is not None
                or self.max_video_frames is not None
            ):
                raise ValueError(
                    "Parameters enable_fusion, res_level, and max_video_frames only apply to multimodal input mode"
                )

        elif self.provider == "litellm":
            # litellm handles auth via env vars or explicit api_key; no strict requirement
            if not self.dimension:
                raise ValueError(
                    "LiteLLM provider requires 'dimension' to be set explicitly. "
                    "Check your embedding model's documentation for the correct dimension."
                )

        elif self.provider == "local":
            from openviking.models.embedder.local_embedders import get_local_model_spec

            get_local_model_spec(self.model)

        return self

    def _validate_credentials(self) -> None:
        """Validate each credential when credentials list is non-empty.

        Each credential must resolve a provider (from itself or the parent) and
        meet that provider's required fields. The parent-level provider/api_key
        are used as fallbacks where appropriate.
        """
        for idx, cred in enumerate(self.credentials):
            cred_id = cred.id or f"credential-{idx}"
            self._validate_provider_auth(
                label=f"credentials[{cred_id}]",
                provider=(cred.provider or self.provider or "").lower() or None,
                api_key=cred.api_key or self.api_key,
                api_base=cred.api_base or self.api_base,
                ak=cred.ak or self.ak,
                sk=cred.sk or self.sk,
                region=cred.region or self.region,
            )

    def get_effective_dimension(self) -> int:
        """Resolve the dimension used for schema creation and validation."""
        if self.dimension is not None:
            return self.dimension

        provider = (self.provider or "").lower()
        if provider in {"openai", "azure"}:
            openai_model_dimensions = {
                "text-embedding-ada-002": 1536,
                "text-embedding-3-small": 1536,
                "text-embedding-3-large": 3072,
            }
            model_lower = (self.model or "").lower()
            if model_lower in openai_model_dimensions:
                return openai_model_dimensions[model_lower]

        if provider == "voyage":
            from openviking.models.embedder.voyage_embedders import (
                get_voyage_model_default_dimension,
            )

            return get_voyage_model_default_dimension(self.model)

        if provider == "cohere":
            from openviking.models.embedder.cohere_embedders import (
                get_cohere_model_default_dimension,
            )

            return get_cohere_model_default_dimension(self.model)

        if provider == "gemini":
            from openviking.models.embedder.gemini_embedders import GeminiDenseEmbedder

            return GeminiDenseEmbedder._default_dimension(self.model)

        if provider == "ollama":
            # Common Ollama embedding models and their dimensions
            # Users should set dimension explicitly for other models
            ollama_model_dimensions = {
                "nomic-embed-text": 768,
                "nomic-embed-text-v1": 768,
                "nomic-embed-text-v1.5": 768,
                "mxbai-embed-large": 1024,
                "mxbai-embed-large-v1": 1024,
                "all-minilm": 384,
                "all-minilm-l6-v2": 384,
                "snowflake-arctic-embed": 1024,
                "snowflake-arctic-embed-l": 1024,
                "qwen3-embedding": 1024,
                "qwen3-embedding:0.6b": 1024,
                "qwen3-embedding:4b": 1024,
                "qwen3-embedding:8b": 1024,
                "embeddinggemma": 768,
                "embeddinggemma:300m": 768,
            }
            model_lower = (self.model or "").lower()
            if model_lower in ollama_model_dimensions:
                return ollama_model_dimensions[model_lower]
            # For unknown Ollama models, require explicit dimension
            raise ValueError(
                f"Unknown dimension for Ollama model '{self.model}'. "
                f"Please set 'dimension' explicitly in your embedding config. "
                f"Known models: {list(ollama_model_dimensions.keys())}"
            )

        if provider == "local":
            from openviking.models.embedder.local_embedders import get_local_model_default_dimension

            return get_local_model_default_dimension(self.model)

        if provider == "dashscope":
            try:
                from openviking.models.embedder.dashscope_embedders import (
                    get_dashscope_model_default_dimension,
                )

                return get_dashscope_model_default_dimension(self.model)
            except ImportError:
                # Fallback dimension if dashscope_embedders module doesn't exist yet
                return 1024

        return 2048


class EmbeddingCircuitBreakerConfig(BaseModel):
    failure_threshold: int = Field(
        default=5,
        ge=1,
        description="Consecutive failures required to open the embedding circuit breaker",
    )
    reset_timeout: float = Field(
        default=60.0,
        gt=0,
        description="Base circuit breaker reset timeout in seconds",
    )
    max_reset_timeout: float = Field(
        default=600.0,
        gt=0,
        description="Maximum circuit breaker reset timeout in seconds",
    )

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.max_reset_timeout < self.reset_timeout:
            raise ValueError("embedding.circuit_breaker.max_reset_timeout must be >= reset_timeout")
        return self


class EmbeddingConfig(BaseModel):
    """
    Embedding configuration, supports OpenAI, VolcEngine, VikingDB, Jina, Gemini, Voyage, or LiteLLM APIs.

    Structure:
    - dense: Configuration for dense embedder
    - sparse: Configuration for sparse embedder
    - hybrid: Configuration for hybrid embedder (single model returning both)

    Environment variables are mapped to these configurations.
    """

    dense: Optional[EmbeddingModelConfig] = Field(default=None)
    sparse: Optional[EmbeddingModelConfig] = Field(default=None)
    hybrid: Optional[EmbeddingModelConfig] = Field(default=None)
    circuit_breaker: EmbeddingCircuitBreakerConfig = Field(
        default_factory=EmbeddingCircuitBreakerConfig
    )

    max_concurrent: int = Field(
        default=10, description="Maximum number of concurrent embedding requests"
    )
    max_retries: int = Field(
        default=3,
        description="Maximum retry attempts for embedding provider calls (0 disables retry)",
    )
    text_source: str = Field(
        default="content_only",
        description="Text source for file vectorization: summary_first|summary_only|content_only",
    )
    max_input_tokens: int = Field(
        default=4096,
        ge=100,
        description="Maximum estimated tokens sent to embeddings when raw text fallback is used",
    )
    allow_metadata_override: bool = Field(
        default=False,
        description=(
            "When true, allow starting up against an existing collection whose "
            "embedding metadata (provider/model) differs from the current config, "
            "as long as dimension is unchanged. The collection metadata will be "
            "rewritten to the new config and a warning will be logged. "
            "Useful when migrating an existing index to a new model deployment "
            "(e.g. switching ARK endpoint id) while keeping previously indexed "
            "vectors. Note: vector semantics may drift if the underlying model "
            "actually changed; only enable when you understand the implication."
        ),
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def apply_default_local_dense(cls, data: Any) -> Any:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            return data

        if not data.get("dense") and not data.get("sparse") and not data.get("hybrid"):
            data = dict(data)
            data["dense"] = {
                "provider": "local",
                "model": "bge-small-zh-v1.5-f16",
            }
        return data

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        if not self.dense and not self.sparse and not self.hybrid:
            raise ValueError(
                "At least one embedding configuration (dense, sparse, or hybrid) is required"
            )
        if self.text_source not in {"summary_first", "summary_only", "content_only"}:
            raise ValueError(
                "embedding.text_source must be one of: summary_first, summary_only, content_only"
            )
        return self

    def _create_embedder(
        self,
        provider: str,
        embedder_type: str,
        config: EmbeddingModelConfig,
    ):
        """Factory method to create embedder instance based on provider and type.

        Args:
            provider: Provider type ('openai', 'volcengine', 'vikingdb', 'jina', 'ollama', 'gemini', 'voyage', 'litellm')
            embedder_type: Embedder type ('dense', 'sparse', 'hybrid')
            config: EmbeddingModelConfig instance

        Returns:
            Embedder instance

        Raises:
            ValueError: If provider/type combination is not supported
        """
        from openviking.models.embedder import (
            CohereDenseEmbedder,
            DashScopeDenseEmbedder,
            GeminiDenseEmbedder,
            JinaDenseEmbedder,
            LiteLLMDenseEmbedder,
            LocalDenseEmbedder,
            MinimaxDenseEmbedder,
            OpenAIDenseEmbedder,
            VikingDBDenseEmbedder,
            VikingDBHybridEmbedder,
            VikingDBSparseEmbedder,
            VolcengineDenseEmbedder,
            VolcengineHybridEmbedder,
            VolcengineSparseEmbedder,
            VoyageDenseEmbedder,
        )

        if provider == "litellm" and LiteLLMDenseEmbedder is None:
            raise ValueError("LiteLLM is not installed. Install it with: pip install litellm")

        # Factory registry: (provider, type) -> (embedder_class, param_builder)
        runtime_config = {
            "max_retries": self.max_retries,
            "max_concurrent": self.max_concurrent,
            "max_input_tokens": self.max_input_tokens,
        }

        factory_registry = {
            ("openai", "dense"): (
                OpenAIDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key
                    or "no-key",  # Placeholder for local OpenAI-compatible servers
                    "api_base": cfg.api_base,
                    "api_version": cfg.api_version,
                    "dimension": cfg.dimension,
                    "provider": "openai",
                    "configured_provider": "openai",
                    "config": dict(runtime_config),
                    **({"query_param": cfg.query_param} if cfg.query_param else {}),
                    **({"document_param": cfg.document_param} if cfg.document_param else {}),
                    **({"extra_headers": cfg.extra_headers} if cfg.extra_headers else {}),
                    **(
                        {"encoding_format": cfg.encoding_format}
                        if cfg.encoding_format is not None
                        else {}
                    ),
                },
            ),
            ("azure", "dense"): (
                OpenAIDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "api_version": cfg.api_version,
                    "dimension": cfg.dimension,
                    "provider": "azure",
                    "configured_provider": "azure",
                    "config": dict(runtime_config),
                    **({"query_param": cfg.query_param} if cfg.query_param else {}),
                    **({"document_param": cfg.document_param} if cfg.document_param else {}),
                    **({"extra_headers": cfg.extra_headers} if cfg.extra_headers else {}),
                    **(
                        {"encoding_format": cfg.encoding_format}
                        if cfg.encoding_format is not None
                        else {}
                    ),
                },
            ),
            ("volcengine", "dense"): (
                VolcengineDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
                    "input_type": cfg.input,
                    "config": dict(runtime_config),
                },
            ),
            ("volcengine", "sparse"): (
                VolcengineSparseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "config": dict(runtime_config),
                },
            ),
            ("volcengine", "hybrid"): (
                VolcengineHybridEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
                    "input_type": cfg.input,
                    "config": dict(runtime_config),
                },
            ),
            ("vikingdb", "dense"): (
                VikingDBDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "model_version": cfg.version,
                    "ak": cfg.ak,
                    "sk": cfg.sk,
                    "region": cfg.region,
                    "host": cfg.host,
                    "dimension": cfg.dimension,
                    "input_type": cfg.input,
                    "config": dict(runtime_config),
                    **({"query_param": cfg.query_param} if cfg.query_param else {}),
                    **({"document_param": cfg.document_param} if cfg.document_param else {}),
                },
            ),
            ("vikingdb", "sparse"): (
                VikingDBSparseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "model_version": cfg.version,
                    "ak": cfg.ak,
                    "sk": cfg.sk,
                    "region": cfg.region,
                    "host": cfg.host,
                    "config": dict(runtime_config),
                },
            ),
            ("vikingdb", "hybrid"): (
                VikingDBHybridEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "model_version": cfg.version,
                    "ak": cfg.ak,
                    "sk": cfg.sk,
                    "region": cfg.region,
                    "host": cfg.host,
                    "dimension": cfg.dimension,
                    "input_type": cfg.input,
                    "config": dict(runtime_config),
                    **({"query_param": cfg.query_param} if cfg.query_param else {}),
                    **({"document_param": cfg.document_param} if cfg.document_param else {}),
                },
            ),
            ("jina", "dense"): (
                JinaDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
                    "config": dict(runtime_config),
                    **({"query_param": cfg.query_param} if cfg.query_param else {}),
                    **({"document_param": cfg.document_param} if cfg.document_param else {}),
                },
            ),
            ("gemini", "dense"): (
                GeminiDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "dimension": cfg.dimension,
                    "config": dict(runtime_config),
                    **({"query_param": cfg.query_param} if cfg.query_param else {}),
                    **({"document_param": cfg.document_param} if cfg.document_param else {}),
                },
            ),
            # Ollama: local OpenAI-compatible embedding server, no real API key needed
            ("ollama", "dense"): (
                OpenAIDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key
                    or "no-key",  # Ollama ignores the key, but client requires non-empty
                    "api_base": cfg.api_base or "http://localhost:11434/v1",
                    "dimension": cfg.dimension,
                    "configured_provider": "ollama",
                    "config": dict(runtime_config),
                },
            ),
            ("voyage", "dense"): (
                VoyageDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
                    "config": dict(runtime_config),
                },
            ),
            ("minimax", "dense"): (
                MinimaxDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
                    "config": dict(runtime_config),
                    **({"query_param": cfg.query_param} if cfg.query_param else {}),
                    **({"document_param": cfg.document_param} if cfg.document_param else {}),
                    **({"extra_headers": cfg.extra_headers} if cfg.extra_headers else {}),
                },
            ),
            ("dashscope", "dense"): (
                DashScopeDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
                    "input_type": cfg.input,
                    "config": dict(runtime_config),
                    **(
                        {"enable_fusion": cfg.enable_fusion}
                        if cfg.enable_fusion is not None
                        else {}
                    ),
                    **({"res_level": cfg.res_level} if cfg.res_level is not None else {}),
                    **(
                        {"max_video_frames": cfg.max_video_frames}
                        if cfg.max_video_frames is not None
                        else {}
                    ),
                },
            ),
            ("cohere", "dense"): (
                CohereDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
                    "config": dict(runtime_config),
                },
            ),
            ("litellm", "dense"): (
                LiteLLMDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
                    "config": dict(runtime_config),
                    **({"query_param": cfg.query_param} if cfg.query_param else {}),
                    **({"document_param": cfg.document_param} if cfg.document_param else {}),
                    **({"extra_headers": cfg.extra_headers} if cfg.extra_headers else {}),
                },
            ),
            ("local", "dense"): (
                LocalDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "model_path": cfg.model_path,
                    "cache_dir": cfg.cache_dir,
                    "dimension": cfg.dimension,
                    "config": dict(runtime_config),
                },
            ),
        }

        key = (provider, embedder_type)
        if key not in factory_registry:
            raise ValueError(
                f"Unsupported combination: provider='{provider}', type='{embedder_type}'. "
                f"Supported combinations: {list(factory_registry.keys())}"
            )

        embedder_class, param_builder = factory_registry[key]
        params = param_builder(config)
        return embedder_class(**params)

    def get_embedder(self):
        """Get embedder instance based on configuration.

        Returns:
            Embedder instance (Dense, Sparse, Hybrid, or Composite)

        Raises:
            ValueError: If configuration is invalid or unsupported
        """
        from openviking.models.embedder import CompositeHybridEmbedder
        from openviking.models.embedder.base import DenseEmbedderBase, SparseEmbedderBase

        if self.hybrid:
            if self.hybrid.credentials:
                return self._create_failover_embedder("hybrid", self.hybrid)
            provider = self._require_provider(self.hybrid.provider)
            return self._create_embedder(provider, "hybrid", self.hybrid)

        if self.dense and self.sparse:
            # Handle failover for both dense and sparse if credentials are configured
            dense_embedder = self._create_single_or_failover_embedder("dense", self.dense)
            sparse_embedder = self._create_single_or_failover_embedder("sparse", self.sparse)
            return CompositeHybridEmbedder(
                cast(DenseEmbedderBase, dense_embedder),
                cast(SparseEmbedderBase, sparse_embedder),
            )

        if self.dense:
            return self._create_single_or_failover_embedder("dense", self.dense)

        raise ValueError("No embedding configuration found (dense, sparse, or hybrid)")

    def _create_single_or_failover_embedder(
        self, embedder_type: str, config: "EmbeddingModelConfig"
    ):
        """Create either a single embedder or a FailoverEmbedder based on credentials config."""
        if config.credentials:
            return self._create_failover_embedder(embedder_type, config)
        provider = self._require_provider(config.provider)
        return self._create_embedder(provider, embedder_type, config)

    def _create_failover_embedder(self, embedder_type: str, config: "EmbeddingModelConfig"):
        """Create a FailoverEmbedder with multiple credentials."""
        from openviking.models.embedder import FailoverEmbedder

        embedders = []
        credential_ids = []

        for cred in config.credentials:
            # Create a temporary config merged from the model config and credential
            merged_config = EmbeddingModelConfig(
                model=cred.model or config.model,
                dimension=config.dimension,
                batch_size=config.batch_size,
                input=config.input,
                query_param=config.query_param,
                document_param=config.document_param,
                provider=cred.provider or config.provider,
                version=config.version,
                ak=cred.ak or config.ak,
                sk=cred.sk or config.sk,
                region=cred.region or config.region,
                host=cred.host or config.host,
                api_key=cred.api_key,
                api_base=cred.api_base,
                api_version=cred.api_version,
                extra_headers=cred.extra_headers or config.extra_headers,
                # Model-behavior fields are shared by all credentials of the
                # same model and live only on the parent config.
                encoding_format=config.encoding_format,
                model_path=config.model_path,
                cache_dir=config.cache_dir,
                enable_fusion=config.enable_fusion,
                res_level=config.res_level,
                max_video_frames=config.max_video_frames,
            )
            provider = self._require_provider(merged_config.provider)
            embedders.append(self._create_embedder(provider, embedder_type, merged_config))
            credential_ids.append(cred.id or f"credential-{len(credential_ids)}")

        if len(embedders) == 1:
            return embedders[0]

        return FailoverEmbedder(
            embedders=embedders,
            credential_ids=credential_ids,
            failback_timeout_seconds=config.failback_timeout_seconds,
            failback_request_count=config.failback_request_count,
        )

    @property
    def dimension(self) -> int:
        """Get dimension from active config."""
        return self.get_dimension()

    def get_dimension(self) -> int:
        """Helper to get dimension from active config"""
        if self.hybrid:
            return self.hybrid.get_effective_dimension()
        if self.dense:
            return self.dense.get_effective_dimension()
        return 2048

    @staticmethod
    def _require_provider(provider: Optional[str]) -> str:
        if not provider:
            raise ValueError("Embedding provider is required")
        return provider.lower()
