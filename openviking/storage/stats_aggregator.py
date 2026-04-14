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
from openviking.storage.expr import Eq
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

        by_category: Dict[str, int] = {}
        hotness_dist = {"cold": 0, "warm": 0, "hot": 0}
        staleness = {
            "not_accessed_7d": 0,
            "not_accessed_30d": 0,
            "oldest_memory_age_days": 0,
        }

        # Fetch vector-indexed memories first, then supplement from the
        # filesystem so stats still work when extracted memory leaf files have
        # not been individually indexed into VikingDB.
        all_records = await self._get_memory_records(ctx)
        grouped: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in categories}
        for record in all_records:
            cat = _category_from_uri(record.get("uri", ""))
            if cat in grouped:
                grouped[cat].append(record)

        for cat in categories:
            records = grouped[cat]
            by_category[cat] = len(records)

            for record in records:
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

        total_memories = sum(by_category.values())

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
        """Query all memory records in a single DB round-trip.

        Uses the context_type="memory" filter. Callers group by category
        in Python to avoid N+1 queries.
        """
        try:
            return await self._vikingdb.query(
                filter=Eq("context_type", "memory"),
                limit=10000,
                output_fields=[
                    "uri",
                    "active_count",
                    "updated_at",
                    "created_at",
                    "context_type",
                ],
                ctx=ctx,
            )
        except Exception as e:
            logger.error("Error querying memories: %s", e)
            return []

    async def _get_memory_records(
        self,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        """Return memory records from VikingDB, supplemented by filesystem scan."""
        records_by_uri: Dict[str, Dict[str, Any]] = {}
        for record in await self._query_all_memories(ctx):
            uri = record.get("uri", "")
            if isinstance(uri, str) and uri:
                records_by_uri[uri] = record

        if not records_by_uri:
            for record in await self._scan_memory_filesystem(ctx):
                uri = record.get("uri", "")
                if isinstance(uri, str) and uri and uri not in records_by_uri:
                    records_by_uri[uri] = record

        return list(records_by_uri.values())

    async def _scan_memory_filesystem(
        self,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        """Scan memory roots directly from VikingFS as a stats fallback."""
        viking_fs = get_viking_fs()
        if viking_fs is None:
            return []

        memory_roots = [
            f"viking://user/{ctx.user.user_space_name()}/memories",
            f"viking://agent/{ctx.user.agent_space_name()}/memories",
        ]
        seen_dirs = set()
        scanned: Dict[str, Dict[str, Any]] = {}

        async def walk(dir_uri: str) -> None:
            if dir_uri in seen_dirs:
                return
            seen_dirs.add(dir_uri)
            try:
                entries = await viking_fs.ls(dir_uri, show_all_hidden=True, ctx=ctx)
            except Exception:
                return

            for entry in entries:
                name = entry.get("name", "")
                if not name or name in {".", ".."}:
                    continue
                if name.startswith(".") or name in {"_archive", ".relations.json"}:
                    continue

                uri = entry.get("uri") or f"{dir_uri.rstrip('/')}/{name}"
                if entry.get("isDir", False):
                    await walk(uri)
                    continue

                if not uri.endswith(".md"):
                    continue
                if name in {".overview.md", ".abstract.md"}:
                    continue
                if _category_from_uri(uri) is None:
                    continue

                scanned[uri] = await self._build_filesystem_record(viking_fs, uri, ctx)

        for root in memory_roots:
            await walk(root)

        return list(scanned.values())

    async def _build_filesystem_record(
        self,
        viking_fs,
        uri: str,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Build a lightweight stats record from a memory file on VikingFS."""
        created_at = None
        updated_at = None
        active_count = 0

        try:
            from openviking.session.memory.utils.content import deserialize_metadata

            raw = await viking_fs.read_file(uri, ctx=ctx)
            metadata = deserialize_metadata(raw) or {}
            created_at = metadata.get("created_at")
            updated_at = metadata.get("updated_at")
            active_count = int(metadata.get("active_count", 0) or 0)
        except Exception:
            metadata = {}

        if created_at is None or updated_at is None:
            try:
                stat = await viking_fs.stat(uri, ctx=ctx)
                mod_time = stat.get("modTime")
                if created_at is None:
                    created_at = mod_time
                if updated_at is None:
                    updated_at = mod_time
            except Exception:
                pass

        return {
            "uri": uri,
            "active_count": active_count,
            "updated_at": updated_at,
            "created_at": created_at,
            "context_type": "memory",
        }


def _category_from_uri(uri: str) -> Optional[str]:
    """Infer memory category from a memory URI."""
    if not isinstance(uri, str) or not uri:
        return None
    if uri.endswith("/memories/profile.md"):
        return "profile"
    for cat in MEMORY_CATEGORIES:
        if cat == "profile":
            continue
        if f"/{cat}/" in uri:
            return cat
    return None


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
