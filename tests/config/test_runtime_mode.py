# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime modes keep tenant model identity separate from startup defaults."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.config.account_config import AccountConfig
from openviking.config.binding import manager_over_source
from openviking.config.embedding import AccountEmbeddingProvider
from openviking.config.scope import ConfigScope
from openviking.config.source import MemoryConfigSource
from openviking.config.validate import ConfigPatchError
from openviking.config.vector import AccountVectorConfigResolver, resolve_account_vector_settings
from openviking.config.vlm import AccountVLMProvider
from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)


def account_settings(dimension=768, name="a"):
    return {
        "vlm": {
            "model": f"chat-{name}",
            "credentials": [{"provider": "openai", "api_key": f"vlm-{name}"}],
        },
        "embedding": {
            "dense": {
                "model": f"embed-{name}",
                "dimension": dimension,
                "credentials": [{"provider": "openai", "api_key": f"embedding-{name}"}],
            }
        },
        "vectordb": {
            "backend": "http",
            "url": f"http://vector-{name}:8080",
            "name": f"collection-{name}",
            "index_name": "index",
            "dimension": dimension,
        },
    }


@pytest.fixture
def serverless_config(tmp_path, monkeypatch):
    config = OpenVikingConfig.from_dict(
        {"runtime_mode": "serverless", "storage": {"workspace": str(tmp_path)}}
    )
    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", config)
    return config


@pytest.mark.parametrize("mode", [None, "dedicated", "serverless"])
def test_mode_defaults_and_round_trip(tmp_path, mode):
    values = {"storage": {"workspace": str(tmp_path)}}
    if mode is not None:
        values["runtime_mode"] = mode
    config = OpenVikingConfig.from_dict(values)
    restored = OpenVikingConfig.from_dict(config.model_dump(by_alias=True))
    assert restored.runtime_mode == (mode or "dedicated")
    if mode == "serverless":
        assert restored.storage.vectordb is None
        assert restored.embedding.dense is None
        assert restored.embedding.sparse is None
        assert restored.embedding.hybrid is None
    else:
        assert restored.storage.vectordb.backend == "local"
        assert restored.embedding.dense.provider == "local"


def test_unknown_mode_rejected(tmp_path):
    with pytest.raises(ValueError, match="runtime_mode"):
        OpenVikingConfig.from_dict(
            {"runtime_mode": "typo", "storage": {"workspace": str(tmp_path)}}
        )


@pytest.mark.parametrize("missing", ["vlm", "embedding", "vectordb", "embedding_model"])
def test_incomplete_account_rejected_before_persistence(serverless_config, missing):
    source = MemoryConfigSource()
    manager = manager_over_source(source, base_config=serverless_config)
    settings = account_settings()
    if missing == "embedding_model":
        settings["embedding"] = {"max_concurrent": 3}
    else:
        settings.pop(missing)
    with pytest.raises(ValueError, match="Serverless account requires"):
        manager.validate_initial_settings("a", settings)
    assert source._store == {}


async def test_account_models_dimensions_and_restart_are_independent(serverless_config):
    serverless_config.embedding = EmbeddingConfig(
        dense={"provider": "openai", "model": "cluster", "api_key": "cluster-key", "dimension": 512}
    )
    source = MemoryConfigSource()
    manager = manager_over_source(source, base_config=serverless_config)
    await manager.initialize()
    for name, dimension in (("a", 1024), ("b", 768)):
        await manager.patch_account(name, account_settings(dimension, name), creating=True)

    recovered = manager_over_source(source, base_config=serverless_config)
    await recovered.initialize()
    resolver = AccountVectorConfigResolver(recovered)
    vlm = AccountVLMProvider(recovered)
    try:
        for name, dimension in (("a", 1024), ("b", 768)):
            settings = await resolver.resolve(name)
            assert settings.embedding.dimension == dimension
            assert settings.embedding.dense.model == f"embed-{name}"
            assert settings.vectordb.name == f"collection-{name}"
            assert settings.vectordb.dimension == dimension
            assert settings.dedicated_vectordb
            model = await vlm.effective_vlm_config(name)
            planner = await vlm.get_query_planner(name)
            assert model.model == planner.model == f"chat-{name}"
            assert model.credentials[0].api_key == f"vlm-{name}"
        await recovered.patch_account("a", {"memory": {"extraction_enabled": False}})
        with pytest.raises(ConfigPatchError):
            await recovered.patch_account("a", {"embedding": {"dense": {"dimension": 768}}})
        with pytest.raises(ValueError, match="Serverless account requires"):
            await recovered.patch_account("a", {"vlm": None})
        assert (await recovered.get_settings(ConfigScope.account("a")))["vlm"]["model"] == "chat-a"
    finally:
        await vlm.close()


async def test_recovered_embedding_calls_use_account_credentials(serverless_config, monkeypatch):
    source = MemoryConfigSource()
    manager = manager_over_source(source, base_config=serverless_config)
    await manager.initialize()
    for name, dimension in (("a", 1024), ("b", 768)):
        await manager.patch_account(name, account_settings(dimension, name), creating=True)
    recovered = manager_over_source(source, base_config=serverless_config)
    await recovered.initialize()
    used = []

    class Embedder(DenseEmbedderBase):
        def __init__(self, config):
            super().__init__(model_name=config.dense.model)
            self.dimension = config.dimension
            used.append((config.dense.model, config.dense.credentials[0].api_key))

        def embed(self, text, is_query=False):
            return EmbedResult(dense_vector=[0.1] * self.dimension)

        def get_dimension(self):
            return self.dimension

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda config: Embedder(config))
    provider = AccountEmbeddingProvider(AccountVectorConfigResolver(recovered), recovered)
    try:
        a = await provider.embed("a", "restored task", is_query=False)
        b = await provider.embed("b", "restored task", is_query=False)
        assert len(a.dense_vector) == 1024
        assert len(b.dense_vector) == 768
        assert used == [("embed-a", "embedding-a"), ("embed-b", "embedding-b")]
    finally:
        await provider.close()


async def test_no_cluster_model_fallback_even_when_configured(serverless_config):
    serverless_config.vlm.model = "cluster-chat"
    serverless_config.vlm.provider = "openai"
    serverless_config.vlm.api_key = "cluster-key"
    serverless_config.query_planner = type(serverless_config.vlm).model_validate(
        serverless_config.vlm.model_dump()
    )
    manager = manager_over_source(MemoryConfigSource(), base_config=serverless_config)
    await manager.initialize()
    provider = AccountVLMProvider(manager)
    try:
        with pytest.raises(ValueError, match="requires its own vlm"):
            await provider.get_query_planner("internal")
        with pytest.raises(ValueError, match="requires its own embedding"):
            await AccountVectorConfigResolver(manager).resolve("internal")
    finally:
        await provider.close()


def test_dimension_conflict_rejected_before_persistence(serverless_config):
    manager = manager_over_source(MemoryConfigSource(), base_config=serverless_config)
    settings = account_settings()
    settings["vectordb"]["dimension"] = 1024
    with pytest.raises(ValueError, match="embedding dimension 768 differs"):
        manager.validate_initial_settings("a", settings)


async def test_persisted_dynamic_mode_rejected(serverless_config):
    source = MemoryConfigSource()
    await source.update(ConfigScope.cluster(), lambda _: {"runtime_mode": "dedicated"})
    manager = manager_over_source(source, base_config=serverless_config)
    with pytest.raises(ValueError, match="runtime_mode is startup-only"):
        await manager.initialize()


async def test_runtime_mode_cannot_be_patched(serverless_config):
    manager = manager_over_source(MemoryConfigSource(), base_config=serverless_config)
    await manager.initialize()
    for patch in (manager.patch_cluster, lambda body: manager.patch_account("a", body)):
        with pytest.raises(ConfigPatchError):
            await patch({"runtime_mode": "dedicated"})
    await manager.patch_cluster({"agent_evolution": {"enabled": False}})
    from openviking_cli.utils.config import get_openviking_config

    assert get_openviking_config().runtime_mode == "serverless"


async def test_vector_facade_creates_only_requested_account_backends(serverless_config, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Cluster adapter must not be constructed")

    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.create_collection_adapter", forbidden
    )
    backend = VikingVectorIndexBackend(None, runtime_mode="serverless")
    resolver = SimpleNamespace(
        resolve=AsyncMock(
            side_effect=lambda name: resolve_account_vector_settings(
                AccountConfig.model_validate(account_settings(1024 if name == "a" else 768, name)),
                serverless_config,
            )
        )
    )
    backend.set_vector_config_resolver(resolver)
    created = []

    def create(config, bound_account_id=None):
        result = Mock(
            account_id=bound_account_id, config=config, _adapter=object(), close=AsyncMock()
        )
        created.append(result)
        return result

    monkeypatch.setattr("openviking.storage.viking_vector_index_backend._SingleAccountBackend", create)
    assert backend._shared_adapter is None
    with pytest.raises(ValueError, match="require an account_id"):
        await backend.collection_exists()
    a, b = await backend.get_account_backend("a"), await backend.get_account_backend("b")
    assert await backend.get_account_backend("a") is a
    assert a.config.dimension == 1024 and b.config.dimension == 768
    assert a.config.name != b.config.name
    assert len(created) == 2
    await backend.release_account("a")
    await backend.release_account("b")
    await backend.close()
