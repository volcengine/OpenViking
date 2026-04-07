# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory health statistics aggregator.

Queries VikingDB indexes and the hotness_score function to produce
aggregate memory health metrics without introducing new storage.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openviking.retrieve.memory_lifecycle import hotness_score
from openviking.server.identity import RequestContext
from openviking.storage.expr import And, Eq
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Memory categories from MemoryCategory enum
MEMORY_CATEGORIES = [
    "profile",
    "preferences",
    "entities",
    "events",
    "cases",
    "patterns",
    "tools",
    "skills",
]

# Categories that are directories (contain individual memory files)
_DIRECTORY_CATEGORIES = [
    "preferences",
    "entities",
    "events",
    "cases",
    "patterns",
    "tools",
    "skills",
]

# Categories that are single files at the memories root
_FILE_CATEGORIES = ["profile"]

# Hotness buckets
COLD_THRESHOLD = 0.2
HOT_THRESHOLD = 0.6


class StatsAggregator:
    """Aggregates memory health statistics from VikingDB.

    Reads from existing indexes and the hotness_score function.
    No new storage required.
    """

    def __init__(self, vikingdb_manager) -> None:
        self._vikingdb = vikingdb_manager

    async def _count_memories_on_fs(
        self,
        memory_base: str,
        ctx: RequestContext,
    ) -> Dict[str, int]:
        """Count memory files directly from the filesystem.

        This is the authoritative count — it reflects what actually
        exists on disk, regardless of whether individual files have
        been vectorized.  The semantic_processor only vectorizes
        directory-level .abstract.md / .overview.md files, not
        individual memory .md files, so the vector index is always
        an undercount.

        Returns a dict mapping category name → file count.
        """
        viking_fs = get_viking_fs()
        counts: Dict[str, int] = {cat: 0 for cat in MEMORY_CATEGORIES}

        # Count profile.md (single file, not a directory)
        try:
            if await viking_fs.exists(f"{memory_base}/profile.md", ctx=ctx):
                counts["profile"] = 1
        except Exception as e:
            logger.debug("Error checking profile.md existence: %s", e)

        # Count files in each directory category
        for cat in _DIRECTORY_CATEGORIES:
            dir_uri = f"{memory_base}/{cat}"
            try:
                entries = await viking_fs.ls(dir_uri, ctx=ctx)
            except Exception:
                # Directory doesn't exist — count stays 0
                continue

            for entry in entries:
                name = entry.get("name", "")
                is_dir = entry.get("isDir", False)
                # Skip dotfiles (.abstract.md, .overview.md), dotdirs (.)
                if name.startswith(".") or not name or is_dir:
                    continue
                # Only count .md files (memory files)
                if name.endswith(".md"):
                    counts[cat] += 1

        return counts

    async def get_memory_stats(
        self,
        ctx: RequestContext,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get aggregate memory statistics.

        Args:
            ctx: Request context for tenant scoping.
            category: Optional category filter (e.g. "cases").

        Returns:
            Dictionary with total counts, category breakdown,
            hotness distribution, and staleness metrics.
        """
        now = datetime.now(timezone.utc)

        # Build category list to query
        categories = [category] if category else MEMORY_CATEGORIES

        by_category: Dict[str, int] = {cat: 0 for cat in categories}
        hotness_dist = {"cold": 0, "warm": 0, "hot": 0}
        staleness = {
            "not_accessed_7d": 0,
            "not_accessed_30d": 0,
            "oldest_memory_age_days": 0,
        }

        # Primary count: use the filesystem (source of truth).
        # The vector index is incomplete because the semantic_processor
        # only vectorizes directory-level abstract/overview files, not
        # individual memory .md files created during session commit.
        user_id = ctx.user.user_id
        memory_base = f"viking://user/{user_id}/memories"

        fs_counts = await self._count_memories_on_fs(memory_base, ctx)
        for cat in categories:
            by_category[cat] = fs_counts.get(cat, 0)

        total_memories = sum(by_category.values())

        # Fetch individual records for hotness/staleness metrics (best-effort).
        # On the local HNSW backend, query() may return incomplete results
        # when no query vector is provided; category counts above are
        # authoritative regardless.
        all_records = await self._query_all_memories(ctx)
        for record in all_records:
            active_count = record.get("active_count", 0)
            updated_at_raw = record.get("updated_at")
            updated_at = _parse_datetime(updated_at_raw)
            created_at_raw = record.get("created_at")
            created_at = _parse_datetime(created_at_raw)

            # Hotness distribution
            score = hotness_score(active_count, updated_at, now=now)
            if score < COLD_THRESHOLD:
                hotness_dist["cold"] += 1
            elif score > HOT_THRESHOLD:
                hotness_dist["hot"] += 1
            else:
                hotness_dist["warm"] += 1

            # Staleness: use updated_at for access tracking
            if updated_at:
                age_days = (now - updated_at).total_seconds() / 86400.0
                if age_days > 7:
                    staleness["not_accessed_7d"] += 1
                if age_days > 30:
                    staleness["not_accessed_30d"] += 1

            # Track oldest memory by created_at
            if created_at:
                age = (now - created_at).total_seconds() / 86400.0
                if age > staleness["oldest_memory_age_days"]:
                    staleness["oldest_memory_age_days"] = round(age, 1)

        return {
            "total_memories": total_memories,
            "by_category": by_category,
            "hotness_distribution": hotness_dist,
            "staleness": staleness,
        }

    async def get_session_extraction_stats(
        self,
        session_id: str,
        service,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Get extraction stats for a specific session.

        Args:
            session_id: The session to query.
            service: OpenVikingService instance.
            ctx: Request context for tenant scoping.

        Returns:
            Dictionary with session extraction statistics.
        """
        session = service.sessions.session(ctx, session_id)
        await session.load()

        stats = session.stats
        return {
            "session_id": session_id,
            "total_turns": stats.total_turns,
            "memories_extracted": stats.memories_extracted,
            "contexts_used": stats.contexts_used,
            "skills_used": stats.skills_used,
        }

    async def _query_all_memories(
        self,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        """Query all memory records for hotness/staleness metrics.

        Note: This uses query() which relies on vector search internally.
        On the local HNSW backend it may return incomplete results when no
        query vector is provided. Category counts use filesystem counting instead.
        """
        try:
            return await self._vikingdb.query(
                filter=And([Eq("context_type", "memory"), Eq("level", 2)]),
                limit=10000,
                output_fields=[
                    "uri",
                    "active_count",
                    "updated_at",
                    "created_at",
                    "context_type",
                ],
                order_by="created_at",
                ctx=ctx,
            )
        except Exception as e:
            logger.error("Error querying memories: %s", e)
            return []


def _parse_datetime(value) -> Optional[datetime]:
    """Parse a datetime value from a VikingDB record."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None
    return None
