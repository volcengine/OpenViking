# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class EmbeddingModelConfig(BaseModel):
    """Configuration for a specific embedding model"""

    model: Optional[str] = Field(default=None, description="Model name")
    api_key: Optional[str] = Field(default=None, description="API key")
    api_base: Optional[str] = Field(default=None, description="API base URL")
    dimension: Optional[int] = Field(default=None, description="Embedding dimension")
    batch_size: int = Field(default=32, description="Batch size for embedding generation")
    input: str = Field(default="multimodal", description="Input type: 'text' or 'multimodal'")
    provider: Optional[str] = Field(
        default="volcengine",
        description="Provider type: 'openai', 'volcengine', 'vikingdb', 'jina'",
    )
    backend: Optional[str] = Field(
        default="volcengine",
        description="Backend type (Deprecated, use 'provider' instead): 'openai', 'volcengine', 'vikingdb'",
    )
    version: Optional[str] = Field(default=None, description="Model version")
    ak: Optional[str] = Field(default=None, description="Access Key ID for VikingDB API")
    sk: Optional[str] = Field(default=None, description="Access Key Secretfor VikingDB API")
    region: Optional[str] = Field(default=None, description="Region for VikingDB API")
    host: Optional[str] = Field(default=None, description="Host for VikingDB API")

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def sync_provider_backend(cls, data: Any) -> Any:
        if isinstance(data, dict):
            provider = data.get("provider")
            backend = data.get("backend")

            if backend is not None and provider is None:
                data["provider"] = backend
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

        if self.provider not in ["openai", "volcengine", "vikingdb", "jina"]:
            raise ValueError(
                f"Invalid embedding provider: '{self.provider}'. Must be one of: 'openai', 'volcengine', 'vikingdb', 'jina'"
            )

        # Provider-specific validation
        if self.provider == "openai":
            if not self.api_key:
                raise ValueError("OpenAI provider requires 'api_key' to be set")

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

        return self

    def apply_override(self, override: Optional["EmbeddingContextConfig"]) -> "EmbeddingModelConfig":
        """Merge a partial query/document override onto this base config."""
        if override is None:
            return self

        merged = self.model_dump()
        for key, value in override.model_dump(exclude_none=True).items():
            merged[key] = value
        return EmbeddingModelConfig(**merged)


class EmbeddingContextConfig(BaseModel):
    """Partial override for query/document dense embedding configuration."""

    model: Optional[str] = Field(default=None, description="Model override")
    api_key: Optional[str] = Field(default=None, description="API key override")
    api_base: Optional[str] = Field(default=None, description="API base URL override")
    dimension: Optional[int] = Field(default=None, description="Embedding dimension override")
    batch_size: Optional[int] = Field(default=None, description="Batch size override")
    input: Optional[str] = Field(default=None, description="Input type override")
    provider: Optional[str] = Field(default=None, description="Provider override")
    backend: Optional[str] = Field(default=None, description="Deprecated backend override")
    version: Optional[str] = Field(default=None, description="Model version override")
    ak: Optional[str] = Field(default=None, description="Access Key ID override")
    sk: Optional[str] = Field(default=None, description="Access Key Secret override")
    region: Optional[str] = Field(default=None, description="Region override")
    host: Optional[str] = Field(default=None, description="Host override")

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def sync_provider_backend(cls, data: Any) -> Any:
        if isinstance(data, dict):
            provider = data.get("provider")
            backend = data.get("backend")

            if backend is not None and provider is None:
                data["provider"] = backend
        return data

    @model_validator(mode="after")
    def validate_config(self):
        """Validate partial override consistency."""
        if self.provider and self.provider not in ["openai", "volcengine", "vikingdb", "jina"]:
            raise ValueError(
                f"Invalid embedding provider: '{self.provider}'. Must be one of: 'openai', 'volcengine', 'vikingdb', 'jina'"
            )
        return self


class EmbeddingConfig(BaseModel):
    """
    Embedding configuration, supports OpenAI or VolcEngine compatible APIs.

    Structure:
    - dense: Configuration for dense embedder
    - sparse: Configuration for sparse embedder
    - hybrid: Configuration for hybrid embedder (single model returning both)

    Environment variables are mapped to these configurations.
    """

    dense: Optional[EmbeddingModelConfig] = Field(default=None)
    dense_query: Optional[EmbeddingContextConfig] = Field(default=None)
    dense_document: Optional[EmbeddingContextConfig] = Field(default=None)
    sparse: Optional[EmbeddingModelConfig] = Field(default=None)
    hybrid: Optional[EmbeddingModelConfig] = Field(default=None)

    max_concurrent: int = Field(
        default=10, description="Maximum number of concurrent embedding requests"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        if (self.dense_query or self.dense_document) and not self.dense:
            raise ValueError(
                "dense_query/dense_document require a base dense embedding configuration"
            )
        if not self.dense and not self.sparse and not self.hybrid:
            raise ValueError(
                "At least one embedding configuration (dense, sparse, or hybrid) is required"
            )
        return self

    def _create_embedder(
        self,
        provider: str,
        embedder_type: str,
        config: EmbeddingModelConfig,
        context: Optional[str] = None,
    ):
        """Factory method to create embedder instance based on provider and type.

        Args:
            provider: Provider type ('openai', 'volcengine', 'vikingdb', 'jina')
            embedder_type: Embedder type ('dense', 'sparse', 'hybrid')
            config: EmbeddingModelConfig instance
            context: Optional embedding context ('query' or 'document')

        Returns:
            Embedder instance

        Raises:
            ValueError: If provider/type combination is not supported
        """
        from openviking.models.embedder import (
            JinaDenseEmbedder,
            OpenAIDenseEmbedder,
            VikingDBDenseEmbedder,
            VikingDBHybridEmbedder,
            VikingDBSparseEmbedder,
            VolcengineDenseEmbedder,
            VolcengineHybridEmbedder,
            VolcengineSparseEmbedder,
        )

        # Factory registry: (provider, type) -> (embedder_class, param_builder)
        factory_registry = {
            ("openai", "dense"): (
                OpenAIDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
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
                },
            ),
            ("volcengine", "sparse"): (
                VolcengineSparseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
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
                },
            ),
            ("jina", "dense"): (
                JinaDenseEmbedder,
                lambda cfg: {
                    "model_name": cfg.model,
                    "api_key": cfg.api_key,
                    "api_base": cfg.api_base,
                    "dimension": cfg.dimension,
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
        if context and embedder_type == "dense":
            params = embedder_class.contextualize_init_params(params, context)
        return embedder_class(**params)

    def _resolve_dense_config(self, context: str) -> EmbeddingModelConfig:
        """Get the dense config for a given context, including overrides."""
        if not self.dense:
            raise ValueError("Dense embedding configuration is required")

        override = self.dense_query if context == "query" else self.dense_document
        return self.dense.apply_override(override)

    def get_query_embedder(self):
        """Get the embedder used for query-time retrieval."""
        from openviking.models.embedder import CompositeHybridEmbedder

        if self.hybrid:
            return self._create_embedder(self.hybrid.provider.lower(), "hybrid", self.hybrid)

        if self.dense and self.sparse:
            query_config = self._resolve_dense_config("query")
            dense_embedder = self._create_embedder(
                query_config.provider.lower(),
                "dense",
                query_config,
                context="query",
            )
            sparse_embedder = self._create_embedder(
                self.sparse.provider.lower(), "sparse", self.sparse
            )
            return CompositeHybridEmbedder(dense_embedder, sparse_embedder)

        if self.dense:
            query_config = self._resolve_dense_config("query")
            return self._create_embedder(
                query_config.provider.lower(), "dense", query_config, context="query"
            )

        raise ValueError("No embedding configuration found (dense, sparse, or hybrid)")

    def get_document_embedder(self):
        """Get the embedder used for document/index-time embedding."""
        from openviking.models.embedder import CompositeHybridEmbedder

        if self.hybrid:
            return self._create_embedder(self.hybrid.provider.lower(), "hybrid", self.hybrid)

        if self.dense and self.sparse:
            document_config = self._resolve_dense_config("document")
            dense_embedder = self._create_embedder(
                document_config.provider.lower(),
                "dense",
                document_config,
                context="document",
            )
            sparse_embedder = self._create_embedder(
                self.sparse.provider.lower(), "sparse", self.sparse
            )
            return CompositeHybridEmbedder(dense_embedder, sparse_embedder)

        if self.dense:
            document_config = self._resolve_dense_config("document")
            return self._create_embedder(
                document_config.provider.lower(),
                "dense",
                document_config,
                context="document",
            )

        raise ValueError("No embedding configuration found (dense, sparse, or hybrid)")

    def get_embedder(self):
        """Get embedder instance based on configuration.

        Returns:
            Embedder instance (Dense, Sparse, Hybrid, or Composite)

        Raises:
            ValueError: If configuration is invalid or unsupported
        """
        return self.get_query_embedder()

    @property
    def dimension(self) -> int:
        """Get dimension from active config."""
        return self.get_dimension()

    def get_dimension(self) -> int:
        """Get the document/index embedding dimension."""
        if self.hybrid:
            return self.hybrid.dimension or 2048
        if self.dense:
            return self._resolve_dense_config("document").dimension or 2048
        return 2048
