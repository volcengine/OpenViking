# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Agent Evolution product queries."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional

from openviking.core.namespace import canonicalize_uri
from openviking.server.identity import RequestContext
from openviking.session.memory.experience_lineage import (
    canonical_experience_uri,
    experience_source_tag,
)
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, NotInitializedError

if TYPE_CHECKING:
    from openviking.storage.vikingdb_manager import VikingDBManager

DEFAULT_TRAJECTORY_PAGE_LIMIT = 50
MAX_TRAJECTORY_PAGE_LIMIT = 1000

_TRAJECTORY_OUTPUT_FIELDS = [
    "uri",
    "name",
    "description",
    "created_at",
    "updated_at",
]


class AgentEvolutionService:
    """Serve exact, non-semantic Agent Evolution lineage queries."""

    def __init__(
        self,
        viking_fs: Optional[VikingFS] = None,
        vikingdb: Optional[VikingDBManager] = None,
    ):
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb

    def set_dependencies(self, *, viking_fs: VikingFS, vikingdb: VikingDBManager) -> None:
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb

    def _ensure_initialized(self) -> VikingFS:
        if self._viking_fs is None:
            raise NotInitializedError("VikingFS")
        return self._viking_fs

    async def list_trajectories_by_experience(
        self,
        *,
        experience_uri: str,
        ctx: RequestContext,
        limit: int = DEFAULT_TRAJECTORY_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List trajectories produced by commits that read an Experience."""
        if limit < 1 or limit > MAX_TRAJECTORY_PAGE_LIMIT:
            raise InvalidArgumentError(f"limit must be between 1 and {MAX_TRAJECTORY_PAGE_LIMIT}")
        if offset < 0:
            raise InvalidArgumentError("offset must be greater than or equal to 0")

        canonical_uri = canonical_experience_uri(experience_uri, ctx)
        if canonical_uri is None:
            raise InvalidArgumentError(
                "experience_uri must identify an Experience owned by the current user"
            )

        viking_fs = self._ensure_initialized()
        stat = await viking_fs.stat(canonical_uri, ctx=ctx, skip_count=True)
        if stat.get("isDir", False):
            raise InvalidArgumentError("experience_uri must identify an Experience file")

        if self._vikingdb is None:
            raise NotInitializedError("VikingDB")

        trajectory_root = canonicalize_uri(
            f"viking://user/{ctx.user.user_id}/memories/trajectories",
            ctx,
        )
        lineage_filter = And(
            [
                PathScope("uri", trajectory_root, depth=1),
                Eq("context_type", "memory"),
                Eq("level", 2),
                Eq("search_tags", experience_source_tag(canonical_uri)),
            ]
        )
        records, total = await asyncio.gather(
            self._vikingdb.filter(
                filter=lineage_filter,
                limit=limit,
                offset=offset,
                output_fields=_TRAJECTORY_OUTPUT_FIELDS,
                order_by="updated_at",
                order_desc=True,
                ctx=ctx,
            ),
            self._vikingdb.count(filter=lineage_filter, ctx=ctx),
        )
        items = [
            {field: record.get(field) for field in _TRAJECTORY_OUTPUT_FIELDS if field in record}
            for record in records
        ]
        return {
            "experience_uri": canonical_uri,
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }
