# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb_adapters.factory import create_collection_adapter
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    _safe_identifier,
    _vector_literal,
)
from openviking.storage.vectordb_adapters.pgvector_adapter import (
    PgVectorCollection,
    PgVectorCollectionAdapter,
)
from openviking_cli.utils.config.vectordb_config import (
    PgVectorConfig,
    VectorDBBackendConfig,
)


def _build_config() -> VectorDBBackendConfig:
    return VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "project": "default",
            "name": "context",
            "index_name": "default",
            "distance_metric": "cosine",
            "pgvector": {
                "host": "127.0.0.1",
                "port": 5432,
                "user": "postgres",
                "password": "postgres",
                "db_name": "postgres",
                "schema": "public",
                "dense_vector_name": "vector",
                "sparse_vector_name": "sparse_vector",
            },
        }
    )


def test_pgvector_backend_config_validation():
    config = _build_config()

    assert config.backend == "pgvector"
    assert config.pgvector is not None
    assert isinstance(config.pgvector, PgVectorConfig)
    assert config.pgvector.host == "127.0.0.1"
    assert config.pgvector.port == 5432
    assert config.pgvector.db_name == "postgres"
    assert config.pgvector.schema_name == "public"


def test_vector_literal_and_identifier_safety():
    assert _vector_literal([1, 2.5, float("nan")]) == "[1,2.5,0]"

    name = _safe_identifier("Project/With Space", "Context.Table", prefix="ov")
    assert name.startswith("ov_project_with_space_context_table")
    assert len(name.encode("utf-8")) <= 63

    # PgVectorCollection re-targets OpenGaussCollection and is-a ICollection.
    assert issubclass(PgVectorCollection, ICollection)


def test_factory_creates_pgvector_adapter_without_connecting():
    adapter = create_collection_adapter(_build_config())

    assert isinstance(adapter, PgVectorCollectionAdapter)
    assert adapter.mode == "pgvector"
    assert adapter.collection_name == "context"
    assert adapter.index_name == "default"
    assert adapter.physical_table_name == "ov_default_context"


def test_from_config_reads_top_level_index_distance_dimension():
    config = VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "project": "acme",
            "name": "docs",
            "index_name": "hnsw_idx",
            "distance_metric": "l2",
            "dimension": 384,
            "pgvector": {"host": "127.0.0.1"},
        }
    )
    adapter = create_collection_adapter(config)

    assert adapter.index_name == "hnsw_idx"
    assert adapter._distance_metric == "l2"
    assert adapter._dimension == 384
    assert adapter.physical_table_name == "ov_acme_docs"


def test_pgvector_config_new_field_defaults():
    pg = _build_config().pgvector

    assert pg.url is None
    assert pg.sslmode == "prefer"
    assert pg.index_type == "hnsw"
    assert pg.index_params == {}
    assert pg.pool_size == 1
    assert pg.create_extension is True


def test_pgvector_backend_requires_url_or_host():
    # A url-only config validates (discrete host cleared).
    url_only = VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "pgvector": {"url": "postgresql://u:p@db.example:5432/app", "host": None},
        }
    )
    assert url_only.pgvector.url == "postgresql://u:p@db.example:5432/app"

    # A discrete-field config (host, no url) also validates.
    discrete = VectorDBBackendConfig.model_validate(
        {"backend": "pgvector", "pgvector": {"host": "10.0.0.1"}}
    )
    assert discrete.pgvector.host == "10.0.0.1"

    # Neither url nor host is a hard error.
    with pytest.raises(ValidationError, match="requires 'url' or 'host'"):
        VectorDBBackendConfig.model_validate(
            {"backend": "pgvector", "pgvector": {"url": None, "host": None}}
        )


def test_pgvector_backend_url_priority_and_whitespace_normalization():
    # url wins when both are set; both are stripped.
    both = VectorDBBackendConfig.model_validate(
        {
            "backend": "pgvector",
            "pgvector": {"url": "  postgresql://h/db  ", "host": "  10.0.0.2  "},
        }
    )
    assert both.pgvector.url == "postgresql://h/db"
    assert both.pgvector.host == "10.0.0.2"

    # Whitespace-only url + empty host normalizes to empty -> clear error.
    with pytest.raises(ValidationError, match="requires 'url' or 'host'"):
        VectorDBBackendConfig.model_validate(
            {"backend": "pgvector", "pgvector": {"url": "   ", "host": ""}}
        )
