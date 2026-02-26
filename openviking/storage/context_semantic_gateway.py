# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Semantic vector gateway for OpenViking business flows.

This module keeps raw filter DSL usage inside storage integration code so
business modules can call intent-based methods.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext, Role
from openviking.storage.vikingdb_interface import VikingDBInterface
from openviking_cli.utils.config import get_openviking_config


class ContextSemanticSearchGateway:
    """Semantic methods over the bound context collection."""

    def __init__(self, storage: VikingDBInterface, collection_name: str):
        self._storage = storage
        self._collection_name = collection_name

    @classmethod
    def from_storage(
        cls, storage: VikingDBInterface, collection_name: Optional[str] = None
    ) -> "ContextSemanticSearchGateway":
        if collection_name:
            bound_collection = collection_name
        else:
            try:
                bound_collection = get_openviking_config().storage.vectordb.name
            except Exception:
                # Keep simple tests and lightweight call sites usable.
                bound_collection = "context"
        return cls(storage=storage, collection_name=bound_collection)

    @property
    def collection_name(self) -> str:
        return self._collection_name

    async def collection_exists_bound(self) -> bool:
        return await self._storage.collection_exists(self._collection_name)

    async def search_in_tenant(
        self,
        ctx: RequestContext,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter_dsl: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        scope_filter = self._build_scope_filter(
            ctx=ctx,
            context_type=context_type,
            target_directories=target_directories,
            extra_filter_dsl=extra_filter_dsl,
        )
        return await self._storage.search(
            collection=self._collection_name,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=scope_filter,
            limit=limit,
            offset=offset,
        )

    async def search_global_roots_in_tenant(
        self,
        ctx: RequestContext,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter_dsl: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        if not query_vector:
            return []

        merged_filter = self._merge_filters(
            self._build_scope_filter(
                ctx=ctx,
                context_type=context_type,
                target_directories=target_directories,
                extra_filter_dsl=extra_filter_dsl,
            ),
            {"op": "must", "field": "level", "conds": [0, 1]},
        )
        return await self._storage.search(
            collection=self._collection_name,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=merged_filter,
            limit=limit,
        )

    async def search_children_in_tenant(
        self,
        ctx: RequestContext,
        parent_uri: str,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter_dsl: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        merged_filter = self._merge_filters(
            {"op": "must", "field": "parent_uri", "conds": [parent_uri]},
            self._build_scope_filter(
                ctx=ctx,
                context_type=context_type,
                target_directories=target_directories,
                extra_filter_dsl=extra_filter_dsl,
            ),
        )
        return await self._storage.search(
            collection=self._collection_name,
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=merged_filter,
            limit=limit,
        )

    async def search_similar_memories(
        self,
        account_id: str,
        owner_space: Optional[str],
        category_uri_prefix: str,
        query_vector: List[float],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        conds: List[Dict[str, Any]] = [
            {"op": "must", "field": "context_type", "conds": ["memory"]},
            {"op": "must", "field": "level", "conds": [2]},
            {"op": "must", "field": "account_id", "conds": [account_id]},
        ]
        if owner_space:
            conds.append({"op": "must", "field": "owner_space", "conds": [owner_space]})
        if category_uri_prefix:
            conds.append({"op": "must", "field": "uri", "conds": [category_uri_prefix]})

        return await self._storage.search(
            collection=self._collection_name,
            query_vector=query_vector,
            filter={"op": "and", "conds": conds},
            limit=limit,
        )

    async def get_context_by_uri(
        self,
        account_id: str,
        uri: str,
        owner_space: Optional[str] = None,
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        conds: List[Dict[str, Any]] = [
            {"op": "must", "field": "uri", "conds": [uri]},
            {"op": "must", "field": "account_id", "conds": [account_id]},
        ]
        if owner_space:
            conds.append({"op": "must", "field": "owner_space", "conds": [owner_space]})

        return await self._storage.filter(
            collection=self._collection_name,
            filter={"op": "and", "conds": conds},
            limit=limit,
        )

    async def delete_account_data(self, account_id: str) -> int:
        return await self._storage.batch_delete(
            self._collection_name,
            {"op": "must", "field": "account_id", "conds": [account_id]},
        )

    async def delete_uris(self, ctx: RequestContext, uris: List[str]) -> None:
        for uri in uris:
            conds: List[Dict[str, Any]] = [
                {"op": "must", "field": "account_id", "conds": [ctx.account_id]},
                {
                    "op": "or",
                    "conds": [
                        {"op": "must", "field": "uri", "conds": [uri]},
                        {"op": "must", "field": "uri", "conds": [f"{uri}/"]},
                    ],
                },
            ]
            if ctx.role == Role.USER and uri.startswith(("viking://user/", "viking://agent/")):
                owner_space = (
                    ctx.user.user_space_name()
                    if uri.startswith("viking://user/")
                    else ctx.user.agent_space_name()
                )
                conds.append({"op": "must", "field": "owner_space", "conds": [owner_space]})
            await self._storage.batch_delete(
                self._collection_name,
                {"op": "and", "conds": conds},
            )

    async def update_uri_mapping(
        self,
        ctx: RequestContext,
        uri: str,
        new_uri: str,
        new_parent_uri: str,
    ) -> bool:
        records = await self._storage.filter(
            collection=self._collection_name,
            filter={
                "op": "and",
                "conds": [
                    {"op": "must", "field": "uri", "conds": [uri]},
                    {"op": "must", "field": "account_id", "conds": [ctx.account_id]},
                ],
            },
            limit=1,
        )
        if not records or "id" not in records[0]:
            return False

        return await self._storage.update(
            self._collection_name,
            records[0]["id"],
            {"uri": new_uri, "parent_uri": new_parent_uri},
        )

    async def increment_active_count(self, ctx: RequestContext, uris: List[str]) -> int:
        updated = 0
        for uri in uris:
            records = await self.get_context_by_uri(account_id=ctx.account_id, uri=uri, limit=1)
            if not records:
                continue
            record = records[0]
            record_id = record.get("id")
            if not record_id:
                continue
            current = int(record.get("active_count", 0) or 0)
            if await self._storage.update(
                self._collection_name,
                record_id,
                {"active_count": current + 1},
            ):
                updated += 1
        return updated

    def _build_scope_filter(
        self,
        ctx: RequestContext,
        context_type: Optional[str],
        target_directories: Optional[List[str]],
        extra_filter_dsl: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        filters: List[Dict[str, Any]] = []
        if context_type:
            filters.append({"op": "must", "field": "context_type", "conds": [context_type]})

        tenant_filter = self._tenant_filter(ctx, context_type=context_type)
        if tenant_filter:
            filters.append(tenant_filter)

        if target_directories:
            uri_conds = [
                {"op": "must", "field": "uri", "conds": [target_dir]}
                for target_dir in target_directories
                if target_dir
            ]
            if uri_conds:
                filters.append({"op": "or", "conds": uri_conds})

        if extra_filter_dsl:
            filters.append(extra_filter_dsl)

        return self._merge_filters(*filters)

    @staticmethod
    def _tenant_filter(
        ctx: RequestContext, context_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if ctx.role == Role.ROOT:
            return None

        owner_spaces = [ctx.user.user_space_name(), ctx.user.agent_space_name()]
        if context_type == "resource":
            owner_spaces.append("")
        return {
            "op": "and",
            "conds": [
                {"op": "must", "field": "account_id", "conds": [ctx.account_id]},
                {"op": "must", "field": "owner_space", "conds": owner_spaces},
            ],
        }

    @staticmethod
    def _merge_filters(*filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        non_empty = [f for f in filters if f]
        if not non_empty:
            return None
        if len(non_empty) == 1:
            return non_empty[0]
        return {"op": "and", "conds": non_empty}
