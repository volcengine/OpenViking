# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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
    input_type: Optional[str] = Field(
        default=None,
        description="Input type for OpenAI/Jina: 'query', 'document', or 'passage'",
    )
    input_type_query: Optional[str] = Field(
        default=None,
        description="OpenAI input type for query embeddings (e.g. 'query')",
    )
    input_type_document: Optional[str] = Field(
        default=None,
        description="OpenAI input type for document/passage embeddings (e.g. 'document' or 'passage')",
    )
    task_query: Optional[str] = Field(
        default=None,
        description="Jina task for query embeddings (e.g. 'retrieval.query')",
    )
    task_document: Optional[str] = Field(
        default=None,
        description="Jina task for document embeddings (e.g. 'retrieval.passage')",
    )
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
            for key in (
                "input_type",
                "input_type_query",
                "input_type_document",
                "task_query",
                "task_document",
            ):
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

    max_concurrent: int = Field(
        default=10, description="Maximum number of concurrent embedding requests"
    )

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
                    "input_type": cfg.input_type,
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
                    "task": cfg.task_document,
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

    def get_query_embedder(self):
        """Get embedder instance for query embeddings."""
        return self._get_contextual_embedder("query")

    def get_document_embedder(self):
        """Get embedder instance for document/passage embeddings."""
        return self._get_contextual_embedder("document")

    def _get_contextual_embedder(self, context: str):
        from openviking.models.embedder import (
            JinaDenseEmbedder,
            OpenAIDenseEmbedder,
        )

        if not self.dense:
            return self.get_embedder()

        provider = (self.dense.provider or "").lower()
        if provider == "openai":
            if self.dense.input_type:
                input_type = "query" if context == "query" else self.dense.input_type
            else:
                input_type = None

            if not self.dense.model:
                raise ValueError("Embedding model name is required")

            return OpenAIDenseEmbedder(
                model_name=self.dense.model,
                api_key=self.dense.api_key,
                api_base=self.dense.api_base,
                dimension=self.dense.dimension,
                input_type=input_type,
            )

        if provider == "jina":
            if self.dense.task_document:
                task = "retrieval.query" if context == "query" else self.dense.task_document
            else:
                task = None

            if not self.dense.model:
                raise ValueError("Embedding model name is required")

            return JinaDenseEmbedder(
                model_name=self.dense.model,
                api_key=self.dense.api_key,
                api_base=self.dense.api_base,
                dimension=self.dense.dimension,
                task=task,
            )

        return self.get_embedder()

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

    @staticmethod
    def _require_provider(provider: Optional[str]) -> str:
        if not provider:
            raise ValueError("Embedding provider is required")
        return provider.lower()
