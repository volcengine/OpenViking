# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Context inventory provider for product dashboard counts."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from openviking.core.namespace import canonical_agent_root, canonical_user_root
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import NotFoundError

logger = logging.getLogger(__name__)


class ContextInventoryProvider:
    """Best-effort current-state context counter with a short TTL cache."""

    def __init__(self, service: Any, *, ttl_seconds: float = 10.0) -> None:
        self._service = service
        self._ttl_seconds = max(float(ttl_seconds), 0.0)
        self._cache: dict[tuple[str, str, str], tuple[float, dict[str, int]]] = {}
        self._lock = asyncio.Lock()

    async def get_counts(self, ctx: RequestContext) -> dict[str, int]:
        """Return current context counts for the caller's tenant scope."""
        key = (ctx.account_id, ctx.user.user_id, ctx.user.agent_id)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self._ttl_seconds:
            return dict(cached[1])

        async with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self._ttl_seconds:
                return dict(cached[1])
            counts = await self._read_counts(ctx)
            self._cache[key] = (time.monotonic(), counts)
            return dict(counts)

    async def _read_counts(self, ctx: RequestContext) -> dict[str, int]:
        user_root = canonical_user_root(ctx)
        agent_root = canonical_agent_root(ctx)

        files, skills, user_memories, agent_memories = await asyncio.gather(
            self._stat_count("viking://resources", ctx=ctx),
            self._stat_count(f"{agent_root}/skills", ctx=ctx),
            self._stat_count(f"{user_root}/memories", ctx=ctx),
            self._stat_count(f"{agent_root}/memories", ctx=ctx),
        )
        memories = user_memories + agent_memories
        return {
            "files": files,
            "skills": skills,
            "memories": memories,
            "total": files + skills + memories,
        }

    async def _stat_count(self, uri: str, *, ctx: RequestContext) -> int:
        fs_service = getattr(self._service, "fs", None)
        if fs_service is None:
            return 0
        try:
            stat = await fs_service.stat(uri, ctx=ctx)
            return max(int(stat.get("count") or 0), 0)
        except (FileNotFoundError, AGFSNotFoundError, NotFoundError):
            logger.debug("Usage/Audit inventory root does not exist: %s", uri)
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Usage/Audit inventory stat count failed for uri=%s: %s",
                uri,
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            return 0
