"""Regression checks for configured VikingDB clients and wire responses."""

from types import SimpleNamespace

import httpx
import pytest

from openviking.models.embedder.vikingdb_embedders import VikingDBDenseEmbedder
from openviking_cli.utils.config.embedding_config import EmbeddingConfig


def provider_embedder(embedder):
    while hasattr(embedder, "inner"):
        embedder = embedder.inner
    return embedder


def configured_embedder(mode="dense", **overrides):
    model = {
        "provider": "vikingdb",
        "model": "bge-m3",
        "input": "text",
        "dimension": 4,
        "ak": "test-ak",
        "sk": "test-sk",
        "session_token": "test-sts-token",
        "region": "ap-southeast-1",
        "host": "api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com",
        **overrides,
    }
    return provider_embedder(
        EmbeddingConfig.model_validate({"max_retries": 0, mode: model}).get_embedder()
    )


@pytest.mark.parametrize("mode", ["dense", "sparse", "hybrid"])
def test_configuration_builds_vikingdb_client_with_matching_sts_token(mode):
    embedder = configured_embedder(mode)
    try:
        request = embedder.client.prepare_request("POST", "/api/vikingdb/embedding", data={})
        assert request.headers["X-Security-Token"] == "test-sts-token"
        assert request.host == "api-vikingdb.vikingdb.ap-southeast-1.bytepluses.com"
        if mode != "sparse":
            assert embedder.embedding_type == "text"
    finally:
        embedder.client._session.close()


@pytest.mark.parametrize("field", ["dense", "dense_embedding"])
def test_dense_embedder_reads_provider_vector_field(monkeypatch, field):
    embedder = configured_embedder()
    vector = [1.0, 0.0, 0.0, 0.0]
    monkeypatch.setattr(
        embedder.client._session,
        "request",
        lambda **kwargs: SimpleNamespace(
            status_code=200, json=lambda: {"result": {"data": [{field: vector}]}}
        ),
    )
    try:
        assert embedder.embed("test document").dense_vector == vector
    finally:
        embedder.client._session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["dense", "dense_embedding"])
async def test_async_embedding_signs_sts_and_reads_provider_vector_field(monkeypatch, field):
    # Omit the host to exercise the resolved endpoint used by asynchronous calls.
    embedder = VikingDBDenseEmbedder(
        "bge-m3",
        ak="test-ak",
        sk="test-sk",
        region="ap-southeast-1",
        dimension=4,
        session_token="test-sts-token",
    )
    vector = [1.0, 0.0, 0.0, 0.0]

    def handler(request):
        assert request.url.host == embedder.client.host
        assert request.headers["X-Security-Token"] == "test-sts-token"
        return httpx.Response(200, json={"result": {"data": [{field: vector}]}})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monkeypatch.setattr(
                embedder, "_async_client_cache", SimpleNamespace(get=lambda factory: client)
            )
            assert (await embedder.embed_async("test document")).dense_vector == vector
    finally:
        embedder.client._session.close()


def test_credential_override_does_not_inherit_another_credentials_sts_token():
    # The second credential changes AK/SK and must not carry the first pair's token.
    embedder = configured_embedder(
        credentials=[
            {"id": "parent"},
            {"id": "other", "ak": "other-ak", "sk": "other-sk"},
        ]
    )
    candidates = [provider_embedder(candidate) for candidate in embedder._embedders]
    try:
        assert candidates[0].client.session_token == "test-sts-token"
        assert candidates[1].client.session_token == ""
    finally:
        for candidate in candidates:
            candidate.client._session.close()


@pytest.mark.parametrize("token", [None, ""])
def test_explicit_sts_token_clear_does_not_reinherit_parent(token):
    embedder = configured_embedder(credentials=[{"session_token": token}])
    try:
        assert embedder.client.session_token == ""
    finally:
        embedder.client._session.close()


def test_account_embedding_binding_does_not_keep_cluster_session_token():
    from openviking.config.vector import resolve_effective_embedding

    cluster = EmbeddingConfig.model_validate(
        {
            "dense": {
                "provider": "vikingdb",
                "model": "bge-m3",
                "dimension": 1024,
                "ak": "cluster-ak",
                "sk": "cluster-sk",
                "session_token": "cluster-token",
                "region": "ap-southeast-1",
            },
        }
    )
    effective = resolve_effective_embedding(
        cluster,
        {
            "dense": {
                "model": "bge-m3",
                "dimension": 1024,
                "credentials": [
                    {
                        "provider": "vikingdb",
                        "ak": "account-ak",
                        "sk": "account-sk",
                        "region": "ap-southeast-1",
                    }
                ],
            },
        },
    )
    assert effective.dense.session_token is None
    assert effective.dense.credentials[0].session_token is None
