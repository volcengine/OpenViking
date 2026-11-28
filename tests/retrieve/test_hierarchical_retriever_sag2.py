# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.vectordb_adapters.sag2_adapter import IndexChanged
from openviking.storage.vikingdb_manager import VikingDBManager, VikingDBManagerProxy
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.retrieve import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


class Transport:
    def __init__(self, on_request=None):
        self.requests = []
        self.on_request = on_request

    def request(self, *, state, questions):
        self.requests.append((state, questions))
        if self.on_request:
            self.on_request()
        return (
            {
                "answers": {
                    key: {
                        "type": "noul",
                        "noul": 0.99
                        if "final answer" in value["instructions"]["candidate_passage"]
                        else 0.8,
                    }
                    for key, value in questions.items()
                },
                "usage": {},
            },
            0.01,
            200,
        )


class Client:
    provider = "jev"
    mode = "noul"
    model_name = "fake-jev"

    def __init__(self, on_request=None):
        self._transport = Transport(on_request)

    def _estimate_tokens(self, text):
        return max(1, len(str(text)) // 4)

    def update_token_usage(self, **kwargs):
        pass


def _ctx(account="acct"):
    return RequestContext(
        user=UserIdentifier(account_id=account, user_id="alice"), role=Role.USER, bypass_acl=True
    )


def _query():
    return TypedQuery(
        query="alpha",
        context_type=ContextType.RESOURCE,
        target_directories=["viking://resources"],
        intent="",
    )


def _record(record_id, text, account="acct", uri=None, level=0):
    return {
        "id": record_id,
        "account_id": account,
        "uri": uri or f"viking://resources/{record_id}",
        "context_type": "resource",
        "level": level,
        "name": record_id,
        "abstract": text,
        "content": text if level == 2 else "",
        "search_tags": [],
    }


async def _manager(tmp_path):
    config = VectorDBBackendConfig(
        backend="sag2", path=str(tmp_path), sag2={"candidate_k": 10, "max_hops": 2}
    )
    manager = VikingDBManager(config)

    async def resolve(account_id):
        return SimpleNamespace(dedicated_vectordb=False, vectordb=config)

    manager._vector_config_resolver = SimpleNamespace(resolve=resolve)
    proxy = VikingDBManagerProxy(manager, _ctx())
    assert await proxy.create_collection(
        "context", CollectionSchemas.context_collection("context", 0, requires_embedding=False)
    )
    await proxy.upsert_many(
        [
            _record("a-l0", "alpha bridge", uri="viking://resources/same"),
            _record("a-l1", "alpha context", uri="viking://resources/same", level=1),
            _record("b", "bridge gamma"),
            _record("c", "gamma final answer"),
            _record("l2", "alpha full text", level=2),
        ]
    )
    manager._shared_adapter.upsert(
        _record("secret", "alpha bridge gamma final answer", account="other")
    )
    return manager


@pytest.mark.asyncio
async def test_public_retriever_quick_without_embedder_and_uri_dedup(tmp_path):
    manager = await _manager(tmp_path)
    retriever = HierarchicalRetriever(manager, embedder=None)
    result = await retriever.retrieve(
        _query(), _ctx(), limit=10, mode=RetrieverMode.QUICK, level=[0, 1]
    )
    assert result.matched_contexts
    assert (
        len(
            [
                item
                for item in result.matched_contexts
                if item.uri.startswith("viking://resources/same/")
            ]
        )
        == 1
    )
    assert all("secret" not in item.uri for item in result.matched_contexts)
    await manager.close()


@pytest.mark.asyncio
async def test_public_retriever_h2_and_level_none_h0(tmp_path):
    manager = await _manager(tmp_path)
    retriever = HierarchicalRetriever(manager, embedder=None)
    client = Client()
    retriever._rerank_client = client
    result = await retriever.retrieve(
        _query(), _ctx(), limit=10, mode=RetrieverMode.THINKING, level=[0, 1]
    )
    assert result.matched_contexts[0].uri == "viking://resources/c/.abstract.md"
    assert result.matched_contexts[0].score == 0.99
    assert len(client._transport.requests) == 3
    payload_text = str(client._transport.requests)
    assert "secret" not in payload_text

    client._transport.requests.clear()
    await retriever.retrieve(_query(), _ctx(), limit=10, mode=RetrieverMode.THINKING, level=None)
    assert len(client._transport.requests) == 1  # L2/all-level requests never multi-hop.
    await manager.close()


@pytest.mark.asyncio
async def test_account_dedicated_backend_overrides_cluster_capabilities(tmp_path):
    shared = VectorDBBackendConfig(backend="sag2", path=str(tmp_path))
    dedicated = VectorDBBackendConfig(backend="local", path=str(tmp_path / "remote"))
    manager = VikingDBManager(shared)

    async def resolve(account_id):
        return SimpleNamespace(
            dedicated_vectordb=account_id == "dedicated",
            vectordb=dedicated if account_id == "dedicated" else shared,
        )

    manager._vector_config_resolver = SimpleNamespace(resolve=resolve)
    assert await manager.account_supports_sag2("acct")
    assert not await manager.account_requires_embedding("acct")
    assert not await manager.account_supports_sag2("dedicated")
    assert await manager.account_requires_embedding("dedicated")
    await manager.close()


@pytest.mark.asyncio
async def test_root_scope_stays_bound_to_request_account(tmp_path):
    manager = await _manager(tmp_path)
    root = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="root"), role=Role.ROOT, bypass_acl=False
    )
    result = await HierarchicalRetriever(manager, embedder=None).retrieve(
        _query(), root, limit=20, mode=RetrieverMode.QUICK, level=None
    )
    assert result.matched_contexts
    assert all("secret" not in item.uri for item in result.matched_contexts)
    await manager.close()


@pytest.mark.asyncio
async def test_non_noul_defaults_quick_but_explicit_thinking_fails(tmp_path):
    manager = await _manager(tmp_path)
    retriever = HierarchicalRetriever(manager, embedder=None)
    client = Client()
    client.mode = "choice"
    retriever._rerank_client = client
    result = await retriever.retrieve(_query(), _ctx(), limit=5, mode=None, level=[0])
    assert result.matched_contexts
    assert client._transport.requests == []
    with pytest.raises(InvalidArgumentError, match="mode='noul'"):
        await retriever.retrieve(_query(), _ctx(), limit=5, mode=RetrieverMode.THINKING, level=[0])
    await manager.close()


@pytest.mark.asyncio
async def test_public_retriever_epoch_conflict_does_not_return_mixed_version(tmp_path):
    manager = await _manager(tmp_path)
    retriever = HierarchicalRetriever(manager, embedder=None)
    retriever._rerank_client = Client(
        on_request=lambda: manager._shared_adapter.upsert(_record("new", "concurrent write"))
    )
    with pytest.raises(IndexChanged):
        await retriever.retrieve(_query(), _ctx(), limit=10, mode=RetrieverMode.THINKING, level=[0])
    await manager.close()
