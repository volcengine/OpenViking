# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Qdrant participates in the native Account vector configuration contract."""

import pytest

from openviking.config.account_config import AccountConfig
from openviking.config.account_vector import AccountVectorDBConfig
from openviking.config.binding import manager_over_source
from openviking.config.scope import ConfigScope
from openviking.config.source.memory_source import MemoryConfigSource
from openviking.config.validate import ConfigPatchError, validate_patch
from openviking.config.vector import AccountVectorConfigResolver, resolve_effective_vectordb
from openviking_cli.utils.config import set_openviking_config
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def account_vectordb(**overrides):
    return {
        "backend": "qdrant",
        "name": "context",
        "index_name": "default",
        "dimension": 2,
        "qdrant": {"url": "http://account.invalid:6333"},
        **overrides,
    }


@pytest.mark.parametrize("connection", [{}, {"qdrant": None}, {"qdrant": {}}])
def test_qdrant_account_requires_its_own_endpoint(connection):
    values = account_vectordb()
    values.pop("qdrant")
    with pytest.raises(ValueError, match="requires qdrant.url or url"):
        AccountVectorDBConfig.model_validate({**values, **connection})


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": 0},
        {"dense_vector_name": ""},
        {"sparse_vector_name": ""},
        {"data_collection_name": " "},
        {"metadata_collection_name": " "},
        {"api_ky": "typo"},
    ],
)
def test_qdrant_account_reuses_connection_validation(options):
    with pytest.raises(ValueError):
        AccountVectorDBConfig.model_validate(
            account_vectordb(qdrant={"url": "http://account.invalid:6333", **options})
        )


def test_qdrant_account_sparse_weight_matches_adapter_contract():
    with pytest.raises(ValueError, match="sparse_weight"):
        AccountVectorDBConfig.model_validate(account_vectordb(sparse_weight=1.1))


@pytest.mark.parametrize(
    "account",
    [
        account_vectordb(),
        account_vectordb(qdrant=None, url="http://account.invalid:6333"),
        account_vectordb(backend="http", qdrant=None, url="http://account.invalid"),
    ],
)
def test_account_connection_replaces_cluster_secrets_and_physical_names(account):
    cluster = VectorDBBackendConfig(
        backend="qdrant",
        qdrant={
            "url": "http://cluster.invalid:6333",
            "api_key": "test-cluster-key",
            "data_collection_name": "cluster-data",
            "metadata_collection_name": "cluster-meta",
            "dense_vector_name": "cluster-vector",
        },
        opengauss={"password": "test-cluster-password"},
        custom_params={"api_key": "test-custom-key", "data_collection_name": "custom-data"},
    )
    assert resolve_effective_vectordb(cluster, None) == cluster
    resolved = resolve_effective_vectordb(cluster, AccountVectorDBConfig.model_validate(account))
    assert resolved.qdrant.api_key is None
    assert resolved.qdrant.data_collection_name is None
    assert resolved.qdrant.metadata_collection_name is None
    assert resolved.qdrant.dense_vector_name == "vector"
    assert resolved.opengauss.password == ""
    assert resolved.custom_params == {}
    assert cluster.qdrant.api_key == "test-cluster-key"


async def test_qdrant_account_creation_reload_and_immutable_identity():
    cluster = OpenVikingConfig.from_dict(
        {
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "cluster",
                    "dimension": 2,
                    "api_key": "test-cluster-key",
                }
            },
            "storage": {
                "vectordb": {
                    "backend": "qdrant",
                    "project": "cluster",
                    "qdrant": {"url": "http://cluster.invalid:6333"},
                }
            },
        }
    )
    source = MemoryConfigSource()
    settings = {
        "embedding": {
            "dense": {
                "model": "account",
                "dimension": 4,
                "credentials": [{"provider": "openai", "api_key": "test-account-key"}],
            }
        },
        "vectordb": account_vectordb(
            project="account",
            dimension=4,
            qdrant={
                "url": "http://account.invalid:6333",
                "api_key": "test-account-key",
                "timeout_seconds": 20,
                "dense_vector_name": "dense",
                "sparse_vector_name": "sparse",
                "data_collection_name": "account-data",
                "metadata_collection_name": "account-meta",
            },
        ),
    }
    set_openviking_config(cluster)
    try:
        manager = manager_over_source(source, base_config=cluster)
        await manager.initialize()
        with pytest.raises(ValueError, match="dimension"):
            await manager.patch_account(
                "a", {**settings, "vectordb": account_vectordb()}, creating=True
            )
        assert await source.load(ConfigScope.account("a")) is None
        await manager.patch_account("a", settings, creating=True)
        # Reload from persisted settings, exercising RuntimeField filtering, not just Pydantic.
        reloaded = manager_over_source(source, base_config=cluster)
        await reloaded.initialize()
        resolver = AccountVectorConfigResolver(reloaded)
        account, fallback = await resolver.resolve("a"), await resolver.resolve("b")
        assert account.dedicated_vectordb and account.vectordb.dimension == 4
        assert account.vectordb.project_name == "account"
        assert account.vectordb.qdrant.model_dump() == settings["vectordb"]["qdrant"]
        assert account.embedding.dense.credentials[0].api_key == "test-account-key"
        assert account.embedding.dense.api_key is None
        assert not fallback.dedicated_vectordb and fallback.vectordb.dimension == 2
        assert fallback.vectordb.qdrant.url == "http://cluster.invalid:6333"
        for field, value in settings["vectordb"]["qdrant"].items():
            with pytest.raises(ConfigPatchError, match="create-only"):
                await reloaded.patch_account("a", {"vectordb": {"qdrant": {field: value}}})
        with pytest.raises(ConfigPatchError, match="create-only"):
            await reloaded.patch_account("a", {"vectordb": None})
        assert await source.load(ConfigScope.account("a")) == settings
        with pytest.raises(ConfigPatchError, match="api_ky"):
            validate_patch(
                AccountConfig, {"vectordb": {"qdrant": {"api_ky": "typo"}}}, creating=True
            )
    finally:
        OpenVikingConfigSingleton.reset_instance()
