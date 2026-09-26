# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-owned vector configuration exposed by the runtime configuration API."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, model_validator

from openviking_cli.utils.config.runtime_field import RuntimeField


class AccountEmbeddingCredential(BaseModel):
    """One Account-owned provider binding; credential arrays replace as a unit."""

    id: Optional[str] = RuntimeField(default=None)
    provider: str = RuntimeField(min_length=1)
    model: Optional[str] = RuntimeField(default=None, min_length=1)
    api_key: Optional[str] = RuntimeField(default=None)
    api_base: Optional[str] = RuntimeField(default=None)
    api_version: Optional[str] = RuntimeField(default=None)
    ak: Optional[str] = RuntimeField(default=None)
    sk: Optional[str] = RuntimeField(default=None)
    region: Optional[str] = RuntimeField(default=None)
    host: Optional[str] = RuntimeField(default=None)
    extra_headers: Optional[Dict[str, str]] = RuntimeField(default=None)


class AccountEmbeddingModelConfig(BaseModel):
    """Account-selectable embedding deployment and immutable vector contract."""

    # True model identity and vector-space fields are fixed at Account creation.
    model: str = RuntimeField(dynamic=False, min_length=1)
    dimension: int = RuntimeField(dynamic=False, gt=0)
    input: Optional[Literal["text", "multimodal"]] = RuntimeField(
        default=None,
        dynamic=False,
    )
    query_param: Optional[str] = RuntimeField(default=None, dynamic=False)
    document_param: Optional[str] = RuntimeField(default=None, dynamic=False)
    version: Optional[str] = RuntimeField(default=None, dynamic=False)

    # Provider bindings may rotate without changing the declared vector space.
    credentials: List[AccountEmbeddingCredential] = RuntimeField(min_length=1)
    failback_timeout_seconds: Optional[float] = RuntimeField(default=None, gt=0)
    failback_request_count: Optional[int] = RuntimeField(default=None, ge=1)

    @model_validator(mode="after")
    def validate_credentials(self) -> "AccountEmbeddingModelConfig":
        """Validate each Account-owned provider binding without Cluster fallback."""
        from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

        for credential in self.credentials:
            values = {
                "model": credential.model or self.model,
                "dimension": self.dimension,
                "provider": credential.provider,
                "api_key": credential.api_key,
                "api_base": credential.api_base,
                "api_version": credential.api_version,
                "ak": credential.ak,
                "sk": credential.sk,
                "region": credential.region,
                "host": credential.host,
            }
            for field_name in ("input", "query_param", "document_param", "version"):
                value = getattr(self, field_name)
                if value is not None:
                    values[field_name] = value
            EmbeddingModelConfig.model_validate(values)
        return self


class AccountEmbeddingCircuitBreakerConfig(BaseModel):
    failure_threshold: Optional[int] = RuntimeField(default=None, ge=1)
    reset_timeout: Optional[float] = RuntimeField(default=None, gt=0)
    max_reset_timeout: Optional[float] = RuntimeField(default=None, gt=0)

    @model_validator(mode="after")
    def validate_section(self) -> "AccountEmbeddingCircuitBreakerConfig":
        if not self.model_fields_set:
            raise ValueError("circuit_breaker configuration must not be empty")
        return self


class AccountEmbeddingConfig(BaseModel):
    """Embedding settings an Account may own.

    A section may contain only Account runtime policy, but every declared model
    mode is a complete Account-owned binding. Cluster binding is used only when
    no Account-owned model mode is declared.
    """

    dense: Optional[AccountEmbeddingModelConfig] = RuntimeField(default=None)
    sparse: Optional[AccountEmbeddingModelConfig] = RuntimeField(default=None)
    hybrid: Optional[AccountEmbeddingModelConfig] = RuntimeField(default=None)
    max_concurrent: Optional[int] = RuntimeField(default=None, ge=1)
    max_retries: Optional[int] = RuntimeField(default=None, ge=0)
    circuit_breaker: Optional[AccountEmbeddingCircuitBreakerConfig] = RuntimeField(
        default=None
    )
    text_source: Optional[Literal["content_only", "summary_first"]] = RuntimeField(
        default=None,
        dynamic=False,
    )
    max_input_tokens: Optional[int] = RuntimeField(default=None, dynamic=False, ge=100)

    @model_validator(mode="after")
    def validate_section(self) -> "AccountEmbeddingConfig":
        """Validate mode combinations; an empty section is an explicit no-op override."""
        if self.hybrid is not None and (self.dense is not None or self.sparse is not None):
            raise ValueError("embedding.hybrid cannot be combined with dense or sparse")
        return self


class AccountVolcengineVectorConfig(BaseModel):
    ak: Optional[str] = RuntimeField(default=None, dynamic=False)
    sk: Optional[str] = RuntimeField(default=None, dynamic=False)
    api_key: Optional[str] = RuntimeField(default=None, dynamic=False)
    session_token: Optional[str] = RuntimeField(default=None, dynamic=False)
    region: Optional[str] = RuntimeField(default=None, dynamic=False)
    host: Optional[str] = RuntimeField(default=None, dynamic=False)


class AccountVikingDBVectorConfig(BaseModel):
    host: Optional[str] = RuntimeField(default=None, dynamic=False)
    headers: Optional[Dict[str, str]] = RuntimeField(default=None, dynamic=False)


class AccountVectorDBConfig(BaseModel):
    """Complete create-only Account VectorDB identity and schema contract.

    Local paths, cuVS tuning and custom adapter parameters are intentionally not
    exposed. Account-owned backends are remote only; local/cuvs remain Cluster
    backends.
    """

    backend: Literal["http", "volcengine", "vikingdb"] = RuntimeField(dynamic=False)
    name: str = RuntimeField(dynamic=False, min_length=1)
    url: Optional[str] = RuntimeField(default=None, dynamic=False)
    project_name: str = RuntimeField(
        default="default",
        alias="project",
        dynamic=False,
        min_length=1,
    )
    index_name: str = RuntimeField(dynamic=False, min_length=1)
    distance_metric: Literal["cosine", "l2", "ip"] = RuntimeField(
        default="cosine",
        dynamic=False,
    )
    dimension: int = RuntimeField(dynamic=False, gt=0)
    sparse_weight: float = RuntimeField(default=0.0, dynamic=False, ge=0)
    volcengine: Optional[AccountVolcengineVectorConfig] = RuntimeField(
        default=None,
        dynamic=False,
    )
    vikingdb: Optional[AccountVikingDBVectorConfig] = RuntimeField(
        default=None,
        dynamic=False,
    )

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_connection(self) -> "AccountVectorDBConfig":
        """Require a complete connection block for the selected remote backend."""
        if self.backend == "http" and not self.url:
            raise ValueError("Account HTTP VectorDB requires url")
        if self.backend == "vikingdb":
            if self.vikingdb is None or not self.vikingdb.host:
                raise ValueError("Account VikingDB requires vikingdb.host")
        if self.backend == "volcengine":
            config = self.volcengine
            if config is None:
                raise ValueError("Account Volcengine VectorDB requires volcengine configuration")
            if config.api_key:
                if not (config.host or config.region):
                    raise ValueError(
                        "Account Volcengine api_key mode requires host or region"
                    )
            elif not (config.ak and config.sk and config.region):
                raise ValueError(
                    "Account Volcengine VectorDB requires ak, sk and region "
                    "when api_key is not configured"
                )
        return self
