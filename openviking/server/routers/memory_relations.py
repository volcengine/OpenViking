# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Memory relation endpoints for OpenViking HTTP Server.

Provides typed relation queries between memories (supersedes, contradicts,
related_to, derived_from). Unlike resource-level relations in relations.py,
memory relations carry a semantic type for conflict detection and retrieval.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext
from openviking.server.models import MemoryRelationListResponse, MemoryRelationResponse, Response
from openviking.storage.memory_relation_store import MemoryRelationStore, RelationType

router = APIRouter(prefix="/api/v1/memories", tags=["memory-relations"])

# Module-level store reference, set during service initialization.
_relation_store: Optional[MemoryRelationStore] = None


def set_memory_relation_store(store: MemoryRelationStore) -> None:
    """Wire the memory relation store into the router (called at startup)."""
    global _relation_store
    _relation_store = store


def _get_store() -> MemoryRelationStore:
    if _relation_store is None:
        raise RuntimeError("MemoryRelationStore not initialized")
    return _relation_store


@router.get("/{uri:path}/relations")
async def get_memory_relations(
    uri: str,
    type: Optional[str] = Query(None, description="Filter by relation type"),
    direction: str = Query("both", description="outgoing, incoming, or both"),
    _ctx: RequestContext = Depends(get_request_context),
) -> Response:
    """Get typed relations for a memory URI.

    Returns all relations where the given URI is either source or target,
    optionally filtered by relation type and direction.
    """
    store = _get_store()

    relation_type = RelationType(type) if type else None
    relations = await store.query(uri, relation_type=relation_type, direction=direction)

    items = [
        MemoryRelationResponse(
            id=r.id,
            source_uri=r.source_uri,
            target_uri=r.target_uri,
            relation_type=r.relation_type.value,
            created_at=r.created_at.isoformat(),
            metadata=r.metadata,
        )
        for r in relations
    ]

    result = MemoryRelationListResponse(relations=items, total=len(items))
    return Response(status="ok", result=result.model_dump())
