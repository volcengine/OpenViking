# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL + pgvector collection adapter.

openGauss ships a fork of the pgvector extension, so ``opengauss_adapter.py``
already emits pgvector-shaped SQL (the ``vector`` column type, the
``<=>``/``<#>``/``<->`` distance operators, ``USING hnsw``). This adapter is a
*re-target* of that module for stock PostgreSQL: it reuses the shared data plane
in :class:`~openviking.storage.vectordb_adapters.base.CollectionAdapter`, adds
``CREATE EXTENSION vector`` / DSN connect / ``ON CONFLICT`` upsert, and drops the
openGauss/Citus-only distributed-table machinery. See the line-by-line reuse map
in ``.wiki/pgvector/refs/02-opengauss-as-pgvector-reference.md``.

Built test-first: this skeleton lands the walking-skeleton import + factory
wiring (B0.3); the three ``CollectionAdapter`` hooks are filled in over B1-B4.
"""

from __future__ import annotations

from typing import Any, Dict

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb_adapters.base import CollectionAdapter


class PgVectorCollectionAdapter(CollectionAdapter):
    """CollectionAdapter for PostgreSQL with the pgvector extension."""

    mode = "pgvector"

    @classmethod
    def from_config(cls, config: Any) -> "PgVectorCollectionAdapter":
        raise NotImplementedError

    def _load_existing_collection_if_needed(self) -> None:
        raise NotImplementedError

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        raise NotImplementedError
