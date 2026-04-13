# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Optional, cast

from pydantic import BaseModel, Field, model_validator


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
            "Provider type: 'openai', 'volcengine', 'vikingdb', 'jina', 'ollama', 'gemini', 'voyage', 'litellm'. "
            "For OpenRouter or other OpenAI-compatible providers, use 'litellm' with "
            "api_base and api_key, or 'openai' with api_base and extra_headers."
        ),
    )
    backend: Optional[str] = Field(
        default="volcengine",
        description="Backend type (Deprecated, use 'provider' instead): 'openai', 'volcengine', 'vikingdb', 'voyage'",
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
    api_version: Optional[str] = Field(
        default=None,
        description="API version for Azure OpenAI (e.g., '2025-01-01-preview').",
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

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        if self.backend and not self.provider:
            self.provider = self.backend

        if not self.model:
            raise ValueError("Embedding model name is required")

        if not self.provider:
            raise ValueError("Embedding provider is required")

        if self.provider not in [
            "openai",
            "azure",
            "volcengine",
            "vikingdb",
            "jina",
            "ollama",
            "gemini",
            "voyage",
            "minimax",
            "cohere",
            "litellm",
        ]:
            raise ValueError(
                f"Invalid embedding provider: '{self.provider}'. Must be one of: "
                "'openai', 'azure', 'volcengine', 'vikingdb', 'jina', 'ollama', 'gemini', 'voyage', 'minimax', 'cohere', 'litellm'"
            )

        # Provider-specific validation
        if self.provider == "openai":
            # Allow missing api_key when api_base is set (e.g. local OpenAI-compatible servers)
            if not self.api_key and not self.api_base:
                raise ValueError("OpenAI provider requires 'api_key' to be set")

        elif self.provider == "azure":
            if not self.api_key:
                raise ValueError("Azure provider requires 'api_key' to be set")
            if not self.api_base:
                raise ValueError("Azure provider requires 'api_base' (Azure endpoint) to be set")

        elif self.provider == "ollama":
            # Ollama runs locally, no API key required
            pass

        elif self.provider == "volcengine":
            if not self.api_key:
                raise ValueError("Volcengine provider requires 'api_key' to be set")

        elif self.provider == "vikingdb":
            missing = []
            if not self.ak:
                missing.append("ak")
            if not self.sk:
                missing.append("sk")
            if not self.region:
                missing.append("region")

            if missing:
                raise ValueError(
                    f"VikingDB provider requires the following fields: {', '.join(missing)}"
                )

        elif self.provider == "jina":
            if not self.api_key:
                raise ValueError("Jina provider requires 'api_key' to be set")

        elif self.provider == "gemini":
            if not self.api_key:
                raise ValueError("Gemini provider requires 'api_key' to be set")
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

        elif self.provider == "voyage":
            if not self.api_key:
                raise ValueError("Voyage provider requires 'api_key' to be set")

        elif self.provider == "minimax":
            if not self.api_key:
                raise ValueError("MiniMax provider requires 'api_key' to be set")

        elif self.provider == "cohere":
            if not self.api_key:
                raise ValueError("Cohere provider requires 'api_key' to be set")

        elif self.provider == "litellm":
            # litellm handles auth via env vars or explicit api_key; no strict requirement
            if not self.dimension:
                raise ValueError(
                    "LiteLLM provider requires 'dimension' to be set explicitly. "
                    "Check your embedding model's documentation for the correct dimension."
                )

        return self

    def get_effective_dimension(self) -> int:
        """Resolve the dimension used for schema creation and validation."""
        if self.dimension is not None:
            return self.dimension

        provider = (self.provider or "").lower()
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
        default="summary_first",
        description="Text source for file vectorization: summary_first|summary_only|content_only",
    )
    max_input_chars: int = Field(
        default=1000,
        ge=100,
        description="Maximum characters sent to embeddings when raw text fallback is used",
    )

    model_config = {"extra": "forbid"}

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
            GeminiDenseEmbedder,
            JinaDenseEmbedder,
            LiteLLMDenseEmbedder,
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
            provider = self._require_provider(self.hybrid.provider)
            return self._create_embedder(provider, "hybrid", self.hybrid)

        if self.dense and self.sparse:
            dense_provider = self._require_provider(self.dense.provider)
            dense_embedder = cast(
                DenseEmbedderBase,
                self._create_embedder(dense_provider, "dense", self.dense),
            )
            sparse_embedder = self._create_embedder(
                self._require_provider(self.sparse.provider), "sparse", self.sparse
            )
            sparse_embedder = cast(SparseEmbedderBase, sparse_embedder)
            return CompositeHybridEmbedder(dense_embedder, sparse_embedder)

        if self.dense:
            provider = self._require_provider(self.dense.provider)
            return self._create_embedder(provider, "dense", self.dense)

        raise ValueError("No embedding configuration found (dense, sparse, or hybrid)")

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
