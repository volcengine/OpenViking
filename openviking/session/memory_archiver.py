# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cold-storage archival for stale memories based on hotness scoring.

Moves memories with low hotness scores to an archive directory,
reducing token consumption from stale abstracts and overviews during
retrieval.  Archived memories can be restored to their original location.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

from openviking.retrieve.memory_lifecycle import hotness_score
from openviking.server.identity import RequestContext
from openviking.storage.expr import And, Eq
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)

# Directory name for archived memories within each scope.
ARCHIVE_DIR = "_archive"


@dataclass
class ArchivalCandidate:
    """A memory that qualifies for archival."""

    uri: str
    active_count: int
    updated_at: Optional[datetime]
    score: float
    context_type: str = ""
    parent_uri: str = ""


@dataclass
class ArchivalResult:
    """Summary of an archival operation."""

    scanned: int = 0
    archived: int = 0
    skipped: int = 0
    errors: int = 0
    candidates: List[ArchivalCandidate] = field(default_factory=list)


class MemoryArchiver:
    """Archives cold memories based on hotness scoring.

    Uses ``hotness_score()`` from ``memory_lifecycle`` to identify memories
    whose access frequency and recency have fallen below a threshold.
    Moves them to ``{scope}/_archive/`` using ``viking_fs.mv()`` so
    they remain recoverable but are excluded from default retrieval.
    """

    DEFAULT_THRESHOLD: float = 0.1
    DEFAULT_MIN_AGE_DAYS: int = 7
    DEFAULT_BATCH_SIZE: int = 100

    def __init__(
        self,
        viking_fs: Any,
        storage: Any,
        threshold: float = DEFAULT_THRESHOLD,
        min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    ):
        """Initialize the archiver.

        Args:
            viking_fs: VikingFS instance for filesystem operations.
            storage: VikingDBManagerProxy for vector index queries.
            threshold: Hotness score below which memories are archived.
            min_age_days: Skip memories updated within this many days.
        """
        self.viking_fs = viking_fs
        self.storage = storage
        self.threshold = threshold
        self.min_age_days = min_age_days

    @staticmethod
    def _derive_parent_uri(uri: str) -> str:
        try:
            parent = VikingURI(uri).parent
        except Exception:
            return ""
        return parent.uri if parent else ""

    async def scan(
        self,
        scope_uri: str,
        ctx: Optional[RequestContext] = None,
        now: Optional[datetime] = None,
    ) -> List[ArchivalCandidate]:
        """Scan a scope for cold memories.

        Queries the vector index for all L2 memories under *scope_uri*,
        computes their hotness score, and returns those below the threshold
        that are older than ``min_age_days``.

        Args:
            scope_uri: Root URI to scan (e.g. ``viking://memories/``).
            ctx: Request context for tenant isolation.
            now: Override current time (for deterministic tests).

        Returns:
            List of candidates eligible for archival, sorted by score
            ascending (coldest first).
        """
        if now is None:
            now = datetime.now(timezone.utc)

        candidates: List[ArchivalCandidate] = []

        # Only scan L2 content -- never archive L0 abstracts or L1 overviews.
        filter_expr = And(conds=[Eq("level", 2)])

        cursor: Optional[str] = None
        total_scanned = 0

        while True:
            records, next_cursor = await self.storage.scroll(
                filter=filter_expr,
                limit=self.DEFAULT_BATCH_SIZE,
                cursor=cursor,
                output_fields=[
                    "uri",
                    "active_count",
                    "updated_at",
                    "context_type",
                ],
            )

            if not records:
                break

            for record in records:
                uri = record.get("uri", "")

                # Skip entries already in an archive directory.
                if f"/{ARCHIVE_DIR}/" in uri:
                    continue

                # Skip entries outside the requested scope.
                if not uri.startswith(scope_uri):
                    continue

                total_scanned += 1

                active_count = int(record.get("active_count", 0) or 0)
                updated_at_raw = record.get("updated_at")
                updated_at = _parse_datetime(updated_at_raw)

                # Respect minimum age.
                if updated_at is not None:
                    age_days = (now - updated_at).total_seconds() / 86400.0
                    if age_days < self.min_age_days:
                        continue

                score = hotness_score(
                    active_count=active_count,
                    updated_at=updated_at,
                    now=now,
                )

                if score < self.threshold:
                    candidates.append(
                        ArchivalCandidate(
                            uri=uri,
                            active_count=active_count,
                            updated_at=updated_at,
                            score=score,
                            context_type=record.get("context_type", ""),
                            parent_uri=self._derive_parent_uri(uri),
                        )
                    )

            cursor = next_cursor
            if cursor is None:
                break

        # Coldest first.
        candidates.sort(key=lambda c: c.score)

        logger.info(
            f"[MemoryArchiver] Scanned {total_scanned} memories under {scope_uri}, "
            f"found {len(candidates)} archival candidates (threshold={self.threshold})"
        )
        return candidates

    async def archive(
        self,
        candidates: List[ArchivalCandidate],
        ctx: Optional[RequestContext] = None,
        dry_run: bool = False,
    ) -> ArchivalResult:
        """Archive the given candidates.

        Moves each candidate to ``{parent}/_archive/{filename}`` using
        ``viking_fs.mv()``, which atomically updates the vector index.

        Args:
            candidates: Output of ``scan()``.
            ctx: Request context for tenant isolation.
            dry_run: If True, log what would happen without moving files.

        Returns:
            Summary of the operation.
        """
        result = ArchivalResult(scanned=len(candidates), candidates=candidates)

        for candidate in candidates:
            archive_uri = _build_archive_uri(candidate.uri)

            if dry_run:
                logger.info(
                    f"[MemoryArchiver] DRY-RUN would archive {candidate.uri} "
                    f"(score={candidate.score:.4f}) -> {archive_uri}"
                )
                result.skipped += 1
                continue

            try:
                await self.viking_fs.mv(candidate.uri, archive_uri, ctx=ctx)
                result.archived += 1
                logger.info(
                    f"[MemoryArchiver] Archived {candidate.uri} "
                    f"(score={candidate.score:.4f}) -> {archive_uri}"
                )
            except Exception:
                logger.exception(f"[MemoryArchiver] Failed to archive {candidate.uri}")
                result.errors += 1

        logger.info(
            f"[MemoryArchiver] Archive complete: "
            f"{result.archived} archived, {result.skipped} skipped, "
            f"{result.errors} errors"
        )
        return result

    async def restore(
        self,
        archived_uri: str,
        ctx: Optional[RequestContext] = None,
    ) -> bool:
        """Restore an archived memory to its original location.

        The original location is derived by removing the ``_archive/``
        path segment from the URI.

        Args:
            archived_uri: URI of the archived memory.
            ctx: Request context for tenant isolation.

        Returns:
            True if the memory was restored successfully.
        """
        original_uri = _build_restore_uri(archived_uri)
        if original_uri is None:
            logger.warning(
                f"[MemoryArchiver] Cannot restore {archived_uri}: not in an archive directory"
            )
            return False

        try:
            await self.viking_fs.mv(archived_uri, original_uri, ctx=ctx)
            logger.info(f"[MemoryArchiver] Restored {archived_uri} -> {original_uri}")
            return True
        except Exception:
            logger.exception(f"[MemoryArchiver] Failed to restore {archived_uri}")
            return False

    async def scan_and_archive(
        self,
        scope_uri: str,
        ctx: Optional[RequestContext] = None,
        dry_run: bool = False,
        now: Optional[datetime] = None,
    ) -> ArchivalResult:
        """Convenience method: scan then archive in one call."""
        candidates = await self.scan(scope_uri, ctx=ctx, now=now)
        return await self.archive(candidates, ctx=ctx, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_archive_uri(uri: str) -> str:
    """Insert ``_archive/`` before the filename in a URI.

    ``viking://memories/facts/greeting.md``
    -> ``viking://memories/facts/_archive/greeting.md``
    """
    last_slash = uri.rfind("/")
    if last_slash == -1:
        return f"{ARCHIVE_DIR}/{uri}"
    parent = uri[:last_slash]
    filename = uri[last_slash + 1 :]
    return f"{parent}/{ARCHIVE_DIR}/{filename}"


def _build_restore_uri(archived_uri: str) -> Optional[str]:
    """Remove the ``_archive/`` segment to recover the original URI.

    ``viking://memories/facts/_archive/greeting.md``
    -> ``viking://memories/facts/greeting.md``

    Returns None if the URI does not contain ``_archive/``.
    """
    marker = f"/{ARCHIVE_DIR}/"
    idx = archived_uri.find(marker)
    if idx == -1:
        return None
    parent = archived_uri[:idx]
    filename = archived_uri[idx + len(marker) :]
    return f"{parent}/{filename}"


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Best-effort parse of a datetime value from the vector store."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            return parse_iso_datetime(value)
        except Exception:
            return None
    return None
