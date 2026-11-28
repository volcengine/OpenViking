# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import contextvars
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.search_service import SearchService
from openviking.storage.acl import AclAction, AclMode, acl_grant_tokens
from openviking.storage.collection_schemas import CollectionSchemas, TextEmbeddingHandler
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.viking_fs import VikingFS
from openviking.storage.vikingdb_manager import VikingDBManager, VikingDBManagerProxy
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _ctx(user_id="alice"):
    return RequestContext(user=UserIdentifier("acct", user_id), role=Role.USER)


def _message(record_id, *, mode, principals=(), text="alpha evidence"):
    grants = acl_grant_tokens(principals, AclAction.READ) if principals else []
    return EmbeddingMsg(
        message=text,
        context_data={
            "id": record_id,
            "uri": f"viking://resources/{record_id}",
            "account_id": "acct",
            "context_type": "resource",
            "level": 0,
            "name": record_id,
            "abstract": text,
            "search_tags": [],
            "acl_mode": mode,
            "acl_direct_grants": grants,
            "acl_inherited_grants": [],
        },
    )


async def _service(tmp_path):
    config = VectorDBBackendConfig(backend="sag2", path=str(tmp_path))
    manager = VikingDBManager(config)

    async def resolve(account_id):
        return SimpleNamespace(dedicated_vectordb=False, vectordb=config)

    manager._vector_config_resolver = SimpleNamespace(resolve=resolve)

    def materialize(records, ctx, update=None):
        for record in records:
            uri = record["uri"]
            if uri.endswith("/public"):
                record.update(
                    acl_mode=AclMode.NONE.value,
                    acl_direct_grants=["none"],
                    acl_inherited_grants=["none"],
                )
            else:
                principal = "user:alice" if uri.endswith("/allowed") else "user:bob"
                record.update(
                    acl_mode=AclMode.RESTRICTED.value,
                    acl_direct_grants=acl_grant_tokens([principal], AclAction.READ),
                    acl_inherited_grants=["none"],
                )
        return records

    manager.acl_manager = SimpleNamespace(
        is_enabled=AsyncMock(return_value=True),
        materialize_context_records=AsyncMock(side_effect=materialize),
    )
    proxy = VikingDBManagerProxy(manager, _ctx())
    assert await proxy.create_collection(
        "context", CollectionSchemas.context_collection("context", 0, requires_embedding=False)
    )

    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = None
    fs.rerank_config = None
    fs.retrieval_config = None
    fs.vector_store = manager
    fs.acl_manager = None
    fs._bound_ctx = contextvars.ContextVar("sag2_e2e_ctx", default=None)
    fs._fulltext_available = {}
    fs._get_vector_store = MagicMock(return_value=manager)
    fs._get_embedder = MagicMock(return_value=None)
    fs._ensure_access = AsyncMock()
    fs._ensure_retrieval_scope = AsyncMock()
    fs.authorize_read = MagicMock(side_effect=lambda ctx, uris: ctx)
    return manager, TextEmbeddingHandler(manager), SearchService(fs)


@pytest.mark.asyncio
async def test_queue_to_public_find_search_preserves_acl(tmp_path):
    manager, handler, service = await _service(tmp_path)
    for message in (
        _message("public", mode=AclMode.NONE.value),
        _message("allowed", mode=AclMode.RESTRICTED.value, principals=["user:alice"]),
        _message("denied", mode=AclMode.RESTRICTED.value, principals=["user:bob"]),
    ):
        result = await handler.on_dequeue({"data": message.to_json()})
        assert result.outcome is ProcessOutcome.SUCCESS

    found = await service.find(
        "alpha", _ctx(), target_uri="viking://resources", level=[0], limit=10
    )
    assert {item.uri for item in found.resources} == {
        "viking://resources/public/.abstract.md",
        "viking://resources/allowed/.abstract.md",
    }

    searched = await service.search(
        "alpha",
        _ctx(),
        target_uri="viking://resources",
        level=[0],
        limit=10,
        search_type="keywords",
    )
    assert {item.uri for item in searched.resources} == {
        "viking://resources/public/.abstract.md",
        "viking://resources/allowed/.abstract.md",
    }

    bob = await service.find(
        "alpha", _ctx("bob"), target_uri="viking://resources", level=[0], limit=10
    )
    assert {item.uri for item in bob.resources} == {
        "viking://resources/public/.abstract.md",
        "viking://resources/denied/.abstract.md",
    }
    await manager.close()


@pytest.mark.asyncio
async def test_queue_rejects_oversized_sag2_content_without_indexing(tmp_path):
    manager, handler, service = await _service(tmp_path)
    message = _message("oversized", mode=AclMode.NONE.value, text="x" * (1024 * 1024 + 1))
    result = await handler.on_dequeue({"data": message.to_json()})
    assert result.outcome is ProcessOutcome.FAILED
    assert await VikingDBManagerProxy(manager, _ctx()).count() == 0
    found = await service.find(
        "alpha", _ctx(), target_uri="viking://resources", level=[0], limit=10
    )
    assert found.total == 0
    await manager.close()
