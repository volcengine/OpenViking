# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

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


def test_pgvector_config_new_field_defaults():
    pg = _build_config().pgvector

    assert pg.url is None
    assert pg.sslmode == "prefer"
    assert pg.index_type == "hnsw"
    assert pg.index_params == {}
    assert pg.pool_size == 1
    assert pg.create_extension is True
