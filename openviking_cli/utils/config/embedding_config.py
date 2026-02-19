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
        default="volcengine", description="Provider type: 'openai', 'volcengine', 'vikingdb'"
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

        if self.provider not in ["openai", "volcengine", "vikingdb"]:
            raise ValueError(
                f"Invalid embedding provider: '{self.provider}'. Must be one of: 'openai', 'volcengine', 'vikingdb'"
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
    sparse: Optional[EmbeddingModelConfig] = Field(default=None)
    hybrid: Optional[EmbeddingModelConfig] = Field(default=None)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        if not self.dense and not self.sparse and not self.hybrid:
            raise ValueError(
                "At least one embedding configuration (dense, sparse, or hybrid) is required"
            )
        return self

    def _create_embedder(self, provider: str, embedder_type: str, config: EmbeddingModelConfig):
        """Factory method to create embedder instance based on provider and type.

        Args:
            provider: Provider type ('openai', 'volcengine', 'vikingdb')
            embedder_type: Embedder type ('dense', 'sparse', 'hybrid')
            config: EmbeddingModelConfig instance

        Returns:
            Embedder instance

        Raises:
            ValueError: If provider/type combination is not supported
        """
        from openviking.models.embedder import (
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

        if self.hybrid:
            return self._create_embedder(self.hybrid.provider.lower(), "hybrid", self.hybrid)

        if self.dense and self.sparse:
            dense_embedder = self._create_embedder(self.dense.provider.lower(), "dense", self.dense)
            sparse_embedder = self._create_embedder(
                self.sparse.provider.lower(), "sparse", self.sparse
            )
            return CompositeHybridEmbedder(dense_embedder, sparse_embedder)

        if self.dense:
            return self._create_embedder(self.dense.provider.lower(), "dense", self.dense)

        raise ValueError("No embedding configuration found (dense, sparse, or hybrid)")

    @property
    def dimension(self) -> int:
        """Get dimension from active config."""
        return self.get_dimension()

    def get_dimension(self) -> int:
        """Helper to get dimension from active config"""
        if self.hybrid:
            return self.hybrid.dimension or 2048
        if self.dense:
            return self.dense.dimension or 2048
        return 2048
