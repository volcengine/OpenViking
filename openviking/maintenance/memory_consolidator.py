# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory Consolidator -- periodic background "dream"-style consolidation.

Sweeps a memory scope to merge semantic duplicates that escaped per-write
dedup, resolve contradictions, archive stale entries, and refresh the
scope's overview. Models Claude Code's autoDream service but adapted to
OpenViking's primitives:

    Dream                          | OpenViking equivalent
    ------------------------------ | -----------------------------------
    autoDream.ts gate chain        | MemoryConsolidationScheduler (Phase B)
    tryAcquireConsolidationLock    | LockContext(point) on scope path
    buildConsolidationPrompt 4-ph  | _orient -> _gather -> _consolidate ->
                                   |   _archive -> _reindex -> _record
    forked Sonnet agent            | MemoryDeduplicator.consolidate_cluster
    rollbackConsolidationLock      | run-record mtime drives time gate

Engine is callable from a scheduler (Phase B) or an HTTP endpoint
(Phase C) via run(scope_uri, ctx, dry_run=False).
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openviking.core.context import Context
from openviking.server.identity import RequestContext
from openviking.session.memory_archiver import ArchivalCandidate, MemoryArchiver
from openviking.session.memory_deduplicator import (
    ClusterDecision,
    ClusterDecisionType,
    MemoryDeduplicator,
)
from openviking.storage import VikingDBManager
from openviking.storage.expr import And, Eq
from openviking.storage.transaction import LockContext, get_lock_manager
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Cosine threshold for clustering existing memories. Mirrors dream's
# implicit "obviously similar" bar -- chosen empirically to catch true
# paraphrases while skipping merely-related memories.
DEFAULT_CLUSTER_THRESHOLD = 0.85

# Cap on how many similar memories to fetch per query when building
# clusters. Matches MemoryDeduplicator.MAX_PROMPT_SIMILAR_MEMORIES.
DEFAULT_TOP_K = 5

# Audit URI lives under viking://agent/<account>/maintenance/... per
# the OV alignment audit -- there is no sanctioned maintenance:// scope.
AUDIT_PATH_FRAGMENT = "maintenance/consolidation_runs"


@dataclass
class ConsolidationResult:
    """Per-run record. Persisted as JSON under the scope's audit path."""

    scope_uri: str
    dry_run: bool = False
    started_at: str = ""
    completed_at: str = ""
    phase_durations: Dict[str, float] = field(default_factory=dict)
    candidates: Dict[str, int] = field(default_factory=lambda: {"archive": 0, "merge_clusters": 0})
    ops_applied: Dict[str, int] = field(
        default_factory=lambda: {"archived": 0, "merged": 0, "deleted": 0}
    )
    errors: List[str] = field(default_factory=list)
    partial: bool = False
    applied_uris: List[str] = field(default_factory=list)
    cluster_decisions: List[Dict[str, Any]] = field(default_factory=list)
    audit_uri: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)


class MemoryConsolidator:
    """Orchestrator. No LLM calls of its own; delegates to dependencies.

    Wires together MemoryDeduplicator (LLM cluster decisions),
    MemoryArchiver (cold archival), and the existing reindex pipeline
    to deliver one atomic consolidation pass per scope.
    """

    def __init__(
        self,
        vikingdb: VikingDBManager,
        viking_fs: Any,
        dedup: MemoryDeduplicator,
        archiver: MemoryArchiver,
        service: Any = None,
        cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
        top_k: int = DEFAULT_TOP_K,
    ):
        """Initialize the consolidator.

        Args:
            vikingdb: Vector index manager for scope listing + similarity.
            viking_fs: Filesystem for reading memory bodies and writing audit.
            dedup: MemoryDeduplicator providing consolidate_cluster().
            archiver: MemoryArchiver for cold-archive phase.
            service: Optional service handle. If provided, _reindex calls
                _do_reindex_locked from openviking.server.routers.maintenance
                with this service. Without it, _reindex is a no-op.
            cluster_threshold: Minimum cosine similarity to link two
                memories into the same cluster.
            top_k: Top-K size for per-memory similarity queries.
        """
        self.vikingdb = vikingdb
        self.viking_fs = viking_fs
        self.dedup = dedup
        self.archiver = archiver
        self.service = service
        self.cluster_threshold = cluster_threshold
        self.top_k = top_k

    async def run(
        self,
        scope_uri: str,
        ctx: RequestContext,
        *,
        dry_run: bool = False,
    ) -> ConsolidationResult:
        """Execute the full consolidation pass for one scope.

        Acquires a point lock on the scope path for the entire run.
        Phases commit per-cluster transactions internally so a bad
        cluster decision does not poison the rest of the scope.

        Args:
            scope_uri: Memory scope to consolidate (e.g.
                viking://agent/<account>/memories/patterns/).
            ctx: Request context (system identity for scheduler-driven
                runs; user identity for ad-hoc HTTP runs).
            dry_run: If True, return the plan without applying any ops.

        Returns:
            ConsolidationResult with per-phase metrics and audit pointer.
        """
        result = ConsolidationResult(
            scope_uri=scope_uri,
            dry_run=dry_run,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        path = self.viking_fs._uri_to_path(scope_uri, ctx=ctx)
        async with LockContext(get_lock_manager(), [path], lock_mode="point") as lock_handle:
            try:
                overview = await self._orient(scope_uri, ctx, result)
                clusters, archive_candidates = await self._gather(scope_uri, ctx, result)

                if not dry_run:
                    await self._consolidate(
                        clusters, scope_uri, overview, ctx, result, lock_handle
                    )
                    await self._archive(archive_candidates, ctx, result)
                    if self._has_writes(result):
                        await self._reindex(scope_uri, ctx, result)
                else:
                    result.candidates["merge_clusters"] = len(clusters)
                    result.candidates["archive"] = len(archive_candidates)

                result.completed_at = datetime.now(timezone.utc).isoformat()
                await self._record(result, ctx)
            except Exception as e:
                logger.exception(f"[MemoryConsolidator] run failed for {scope_uri}")
                result.errors.append(str(e))
                result.partial = True
                result.completed_at = datetime.now(timezone.utc).isoformat()
                # Best-effort audit even on failure -- rethrow original.
                try:
                    await self._record(result, ctx)
                except Exception:
                    logger.warning("[MemoryConsolidator] audit record write failed")
                raise

        return result

    async def _orient(
        self,
        scope_uri: str,
        ctx: RequestContext,
        result: ConsolidationResult,
    ) -> str:
        """Phase 1: read the scope's existing overview if any."""
        t0 = time.perf_counter()
        overview_uri = scope_uri.rstrip("/") + "/.overview.md"
        try:
            overview = await self.viking_fs.read(overview_uri, ctx=ctx)
            if isinstance(overview, bytes):
                overview = overview.decode("utf-8", errors="replace")
        except Exception as e:
            logger.debug(f"[MemoryConsolidator] orient: no overview at {overview_uri}: {e}")
            overview = ""
        result.phase_durations["orient"] = time.perf_counter() - t0
        return overview or "(none)"

    async def _gather(
        self,
        scope_uri: str,
        ctx: RequestContext,
        result: ConsolidationResult,
    ) -> tuple[List[List[Context]], List[ArchivalCandidate]]:
        """Phase 2: cluster duplicates + identify archive candidates."""
        t0 = time.perf_counter()

        # Archive candidates: reuse MemoryArchiver.scan().
        archive_candidates = await self.archiver.scan(scope_uri, ctx=ctx)

        # Merge clusters: scroll L2 memories under the scope, query
        # similarity for each, build adjacency, extract components >= 2.
        clusters = await self._cluster_scope(scope_uri, ctx)

        result.candidates["archive"] = len(archive_candidates)
        result.candidates["merge_clusters"] = len(clusters)
        result.phase_durations["gather"] = time.perf_counter() - t0
        return clusters, archive_candidates

    async def _cluster_scope(
        self,
        scope_uri: str,
        ctx: RequestContext,
    ) -> List[List[Context]]:
        """Build clusters of similar existing memories under the scope.

        Strategy:
        1. Scroll L2 entries under the scope to get the candidate set.
        2. For each entry, query the vector index for its top-K similar
           neighbors (via the embedder applied to the entry's abstract).
        3. Build adjacency: edge between A and B iff B appears in A's
           top-K with cosine >= threshold OR vice versa.
        4. Connected components of size >= 2 are merge clusters.
        """
        members: Dict[str, Context] = {}
        filter_expr = And(conds=[Eq("level", 2)])

        cursor: Optional[str] = None
        while True:
            try:
                records, next_cursor = await self.vikingdb.scroll(
                    filter=filter_expr,
                    limit=100,
                    cursor=cursor,
                    output_fields=[
                        "uri",
                        "abstract",
                        "active_count",
                        "updated_at",
                    ],
                )
            except Exception as e:
                logger.warning(f"[MemoryConsolidator] scroll failed under {scope_uri}: {e}")
                return []

            if not records:
                break

            for record in records:
                uri = record.get("uri", "")
                if not uri.startswith(scope_uri):
                    continue
                if "/_archive/" in uri:
                    continue
                members[uri] = Context.from_dict(record)

            cursor = next_cursor
            if cursor is None:
                break

        if len(members) < 2:
            return []

        # Build adjacency via top-K query per member.
        adjacency: Dict[str, set[str]] = {uri: set() for uri in members}
        embedder = getattr(self.dedup, "embedder", None)
        if embedder is None:
            logger.info(
                "[MemoryConsolidator] no embedder configured; skipping cluster build "
                f"under {scope_uri}"
            )
            return []

        try:
            from openviking.models.embedder.base import embed_compat
        except Exception as e:
            logger.warning(f"[MemoryConsolidator] cannot import embedder: {e}")
            return []

        for uri, mem in members.items():
            query_text = (mem.abstract or "")[:512]
            if not query_text:
                # Fallback: read file body. Memories without an abstract
                # haven't been L0-summarized yet but the file body is
                # still a usable embedding source.
                try:
                    body = await self.viking_fs.read(uri, ctx=ctx)
                    if isinstance(body, bytes):
                        body = body.decode("utf-8", errors="replace")
                    query_text = (body or "")[:512]
                except Exception as e:
                    logger.debug(f"[MemoryConsolidator] body read fallback failed for {uri}: {e}")
                if not query_text:
                    continue
            try:
                embed_result = await embed_compat(embedder, query_text, is_query=True)
                query_vector = embed_result.dense_vector
            except Exception as e:
                logger.debug(f"[MemoryConsolidator] embed failed for {uri}: {e}")
                continue

            try:
                hits = await self.vikingdb.search_similar_memories(
                    owner_space=None,
                    category_uri_prefix=scope_uri,
                    query_vector=query_vector,
                    limit=self.top_k,
                )
            except Exception as e:
                logger.debug(f"[MemoryConsolidator] similarity query failed for {uri}: {e}")
                continue

            for hit in hits:
                hit_uri = hit.get("uri", "")
                if not hit_uri or hit_uri == uri or hit_uri not in members:
                    continue
                score = float(hit.get("_score", hit.get("score", 0)) or 0)
                if score >= self.cluster_threshold:
                    adjacency[uri].add(hit_uri)
                    adjacency[hit_uri].add(uri)

        parent: Dict[str, str] = {uri: uri for uri in members}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for uri, neighbors in adjacency.items():
            for n in neighbors:
                union(uri, n)

        groups: Dict[str, List[Context]] = {}
        for uri in members:
            root = find(uri)
            groups.setdefault(root, []).append(members[uri])

        return [g for g in groups.values() if len(g) >= 2]

    async def _consolidate(
        self,
        clusters: List[List[Context]],
        scope_uri: str,
        overview: str,
        ctx: RequestContext,
        result: ConsolidationResult,
        lock_handle=None,
    ) -> None:
        """Phase 3: per-cluster LLM decision and apply ops.

        lock_handle is the scope-level handle from LockContext; passed
        through to viking_fs.rm so per-file deletions reuse the held
        lock instead of timing out trying to re-acquire it (the scope
        lock covers all child paths, and LockContext is not reentrant).
        """
        t0 = time.perf_counter()

        for cluster in clusters:
            try:
                contents = await self._fetch_cluster_contents(cluster, ctx)
                decision = await self.dedup.consolidate_cluster(
                    cluster=cluster,
                    scope_uri=scope_uri,
                    scope_overview=overview,
                    cluster_contents=contents,
                )
                result.cluster_decisions.append(self._summarize_decision(decision))
                await self._apply_decision(decision, ctx, result, lock_handle)
            except Exception as e:
                logger.exception(f"[MemoryConsolidator] cluster failed under {scope_uri}")
                result.errors.append(f"cluster_failed: {e}")
                result.partial = True

        result.phase_durations["consolidate"] = time.perf_counter() - t0

    async def _apply_decision(
        self,
        decision: ClusterDecision,
        ctx: RequestContext,
        result: ConsolidationResult,
        lock_handle=None,
    ) -> None:
        """Apply ops from one ClusterDecision. Tracks applied URIs.

        Skips URIs already in result.applied_uris so retries from a
        failed prior phase do not double-apply (vector index update is
        not idempotent per the audit).
        """
        if decision.decision == ClusterDecisionType.KEEP_ALL:
            return

        applied: set[str] = set(result.applied_uris)

        # Refuse to delete sources when merged_content is empty -- that
        # would leave the keeper with its stale pre-merge body and lose
        # the source content entirely.
        if decision.decision == ClusterDecisionType.KEEP_AND_MERGE and decision.keeper_uri:
            if not decision.merged_content:
                logger.warning(
                    f"[MemoryConsolidator] KEEP_AND_MERGE without merged_content "
                    f"for keeper {decision.keeper_uri}; "
                    f"skipping merge to avoid losing sources {decision.merge_into}"
                )
                result.errors.append(
                    f"merge_skipped_empty_content: keeper={decision.keeper_uri}"
                )
                result.partial = True
                result.applied_uris = sorted(applied)
                return

            if decision.keeper_uri not in applied:
                try:
                    await self.viking_fs.write(
                        decision.keeper_uri,
                        decision.merged_content,
                        ctx=ctx,
                    )
                    applied.add(decision.keeper_uri)
                except Exception as e:
                    logger.warning(f"[MemoryConsolidator] write keeper failed: {e}")
                    result.errors.append(f"write_keeper_failed: {e}")
                    result.partial = True
                    result.applied_uris = sorted(applied)
                    return

            await self._delete_uris(
                decision.merge_into,
                applied,
                op_key="merged",
                error_label="merge_delete_failed",
                keeper_uri=decision.keeper_uri,
                ctx=ctx,
                result=result,
                lock_handle=lock_handle,
            )

        # Delete: drop fully-invalidated members.
        if decision.decision == ClusterDecisionType.KEEP_AND_DELETE:
            await self._delete_uris(
                decision.delete,
                applied,
                op_key="deleted",
                error_label="delete_failed",
                keeper_uri=decision.keeper_uri,
                ctx=ctx,
                result=result,
                lock_handle=lock_handle,
            )

        result.applied_uris = sorted(applied)

    async def _delete_uris(
        self,
        uris: List[str],
        applied: set,
        *,
        op_key: str,
        error_label: str,
        keeper_uri: str,
        ctx: RequestContext,
        result: ConsolidationResult,
        lock_handle=None,
    ) -> None:
        """Delete a set of URIs, updating applied/ops_applied/errors in place."""
        for uri in uris:
            if uri in applied or uri == keeper_uri:
                continue
            try:
                await self.viking_fs.rm(uri, ctx=ctx, lock_handle=lock_handle)
                applied.add(uri)
                result.ops_applied[op_key] += 1
            except Exception as e:
                logger.warning(f"[MemoryConsolidator] {error_label}: {e}")
                result.errors.append(f"{error_label}: {e}")
                result.partial = True

    async def _archive(
        self,
        candidates: List[ArchivalCandidate],
        ctx: RequestContext,
        result: ConsolidationResult,
    ) -> None:
        """Phase 4: cold archive via MemoryArchiver."""
        t0 = time.perf_counter()
        if candidates:
            archive_result = await self.archiver.archive(candidates, ctx=ctx, dry_run=False)
            result.ops_applied["archived"] = archive_result.archived
            if archive_result.errors > 0:
                result.partial = True
                result.errors.append(f"archive_errors: {archive_result.errors}")
        result.phase_durations["archive"] = time.perf_counter() - t0

    async def _reindex(
        self,
        scope_uri: str,
        ctx: RequestContext,
        result: ConsolidationResult,
    ) -> None:
        """Phase 5: rebuild scope overview/abstract under the existing lock."""
        t0 = time.perf_counter()
        if self.service is None:
            logger.debug("[MemoryConsolidator] no service handle; skipping reindex")
            result.phase_durations["reindex"] = 0.0
            return
        try:
            from openviking.server.routers.maintenance import _do_reindex_locked

            await _do_reindex_locked(self.service, scope_uri, regenerate=True, ctx=ctx)
        except Exception as e:
            logger.warning(f"[MemoryConsolidator] reindex failed: {e}")
            result.errors.append(f"reindex_failed: {e}")
            # Reindex failure does not abort the run; next pass retries.
        result.phase_durations["reindex"] = time.perf_counter() - t0

    async def _record(
        self,
        result: ConsolidationResult,
        ctx: RequestContext,
    ) -> None:
        """Phase 6: write audit record to viking://agent/<acct>/maintenance/..."""
        t0 = time.perf_counter()
        scope_hash = self._scope_hash(result.scope_uri)
        # Strip ":" and ".+0000" timezone tail for filesystem-safe filename.
        ts = result.completed_at.split(".")[0].replace(":", "").replace("-", "")
        audit_uri = self._build_audit_uri(ctx, scope_hash, ts)
        result.audit_uri = audit_uri
        parent_uri = audit_uri.rsplit("/", 1)[0]
        try:
            await self.viking_fs.mkdir(parent_uri, ctx=ctx, exist_ok=True)
        except Exception as e:
            logger.debug(f"[MemoryConsolidator] mkdir parent failed: {e}")
        try:
            await self.viking_fs.write(audit_uri, result.to_json(), ctx=ctx)
        except Exception as e:
            logger.warning(f"[MemoryConsolidator] audit write failed at {audit_uri}: {e}")
        result.phase_durations["record"] = time.perf_counter() - t0

    @staticmethod
    def _build_audit_uri(ctx: RequestContext, scope_hash: str, timestamp: str) -> str:
        """Build account-scoped audit URI per the OV alignment audit."""
        account = getattr(ctx, "account_id", None) or "default"
        return (
            f"viking://agent/{account}/{AUDIT_PATH_FRAGMENT}/{scope_hash}/{timestamp}.json"
        )

    @staticmethod
    def _scope_hash(scope_uri: str) -> str:
        import hashlib

        return hashlib.sha1(scope_uri.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _summarize_decision(decision: ClusterDecision) -> Dict[str, Any]:
        return {
            "decision": decision.decision.value,
            "keeper_uri": decision.keeper_uri,
            "merge_into": decision.merge_into,
            "delete": decision.delete,
            "archive": decision.archive,
            "reason": decision.reason,
            "cluster_size": len(decision.cluster),
        }

    @staticmethod
    def _has_writes(result: ConsolidationResult) -> bool:
        ops = result.ops_applied
        return any(ops.get(k, 0) > 0 for k in ("archived", "merged", "deleted"))

    async def _fetch_cluster_contents(
        self,
        cluster: List[Context],
        ctx: RequestContext,
    ) -> Dict[str, str]:
        contents: Dict[str, str] = {}
        for mem in cluster:
            try:
                body = await self.viking_fs.read(mem.uri, ctx=ctx)
                if isinstance(body, bytes):
                    body = body.decode("utf-8", errors="replace")
                contents[mem.uri] = body or ""
            except Exception as e:
                logger.debug(f"[MemoryConsolidator] read failed for {mem.uri}: {e}")
        return contents

