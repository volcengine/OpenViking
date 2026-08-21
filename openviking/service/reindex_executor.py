# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Admin reindex executor."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from openviking.core.context import (
    Context,
    ContextLevel,
    ContextType,
    ResourceContentType,
    Vectorize,
)
from openviking.core.namespace import (
    classify_uri,
    content_owner_context_for_uri,
    context_type_for_uri,
    is_session_uri,
    owner_fields_for_uri,
    owner_space_for_uri,
)
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_work_index import bind_task_context
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.expr import And, Eq, Or, PathScope
from openviking.storage.index_audit import (
    REPAIR_PLAN_VERSION,
    index_records_fingerprint,
)
from openviking.storage.index_digest import canonical_digest
from openviking.storage.index_source import ABSTRACT_NOT_READY_SUFFIX as _ABSTRACT_NOT_READY_SUFFIX
from openviking.storage.index_source import OVERVIEW_NOT_READY_SUFFIX as _OVERVIEW_NOT_READY_SUFFIX
from openviking.storage.index_source import (
    IndexSourceFact,
    SourceState,
    build_index_sources,
    directory_source,
    file_summary,
    parse_overview,
    select_resource_file_vector_text,
    summary_source_selected,
)
from openviking.storage.index_source import is_not_ready_sentinel as _is_not_ready_sentinel
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.semantic_sidecar import body_for_preview, embedding_text_for_body
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.embedding_utils import (
    _apply_ingest_options,
    _truncate_abstract_bytes,
    get_resource_content_type,
)
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    OpenVikingError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

REINDEX_TASK_TYPE = "admin_reindex"
INDEX_REPAIR_TASK_TYPE = "index_repair"
PRUNE_ORPHAN_CANDIDATE_LIMIT = 100000
PRUNE_OUTPUT_FIELDS = ["id", "uri", "level", "context_type", "account_id", "owner_user_id"]
_MAX_FILE_VECTORIZATION_CONCURRENCY = 64


_reindex_executor: "ReindexExecutor | None" = None


def get_reindex_executor() -> "ReindexExecutor":
    global _reindex_executor
    if _reindex_executor is None:
        _reindex_executor = ReindexExecutor()
    return _reindex_executor


@dataclass
class _ReindexCounters:
    scanned_records: int = 0
    rebuilt_records: int = 0
    deleted_records: int = 0
    would_delete_records: int = 0
    unsupported_records: int = 0
    failed_records: int = 0
    warnings: list[str] = field(default_factory=list)

    def merge_from(self, other: "_ReindexCounters") -> None:
        self.scanned_records += other.scanned_records
        self.rebuilt_records += other.rebuilt_records
        self.deleted_records += other.deleted_records
        self.would_delete_records += other.would_delete_records
        self.unsupported_records += other.unsupported_records
        self.failed_records += other.failed_records
        self.warnings.extend(other.warnings)


@dataclass
class _ReindexRunContext:
    ctx: RequestContext
    counters: _ReindexCounters
    lock: dict | None = None
    ingest_options: IngestOptions | None = None


@dataclass
class _PruneSourceRead:
    exists: bool
    text: str = ""
    error: Exception | None = None


class ReindexExecutor:
    """Non-destructive reindex orchestration for admin maintenance flows."""

    SUPPORTED_MODES_BY_TYPE = {
        "global_namespace": {"vectors_only", "semantic_and_vectors", "prune_orphans"},
        "user_namespace": {"vectors_only", "semantic_and_vectors", "prune_orphans"},
        "skill_namespace": {"vectors_only", "semantic_and_vectors", "prune_orphans"},
        "resource": {"vectors_only", "semantic_and_vectors", "prune_orphans"},
        "skill": {"vectors_only", "semantic_and_vectors", "prune_orphans"},
        "memory": {"vectors_only", "semantic_and_vectors", "prune_orphans"},
    }

    @staticmethod
    def _effective_file_vectorization_concurrency() -> int:
        config = get_openviking_config().reindex
        return max(
            1,
            min(
                int(config.file_vectorization_concurrency),
                _MAX_FILE_VECTORIZATION_CONCURRENCY,
            ),
        )

    async def _run_ordered_counter_batches(
        self,
        items: list[str],
        *,
        concurrency: int,
        processor: Any,
        counters: _ReindexCounters,
    ) -> None:
        for start in range(0, len(items), concurrency):
            batch = items[start : start + concurrency]
            for item_counters in await asyncio.gather(*(processor(item) for item in batch)):
                counters.merge_from(item_counters)

    @staticmethod
    def _content_owner_ctx(uri: str, ctx: RequestContext) -> RequestContext:
        """Return the content-owner context for user-scoped reindex writes.

        Reindex authorization and task ownership use the actor ctx, but semantic
        and vector records should retain ownership from the target URI.
        """
        return content_owner_context_for_uri(uri, ctx)

    async def execute(
        self,
        *,
        uri: str,
        mode: str,
        wait: bool,
        dry_run: bool = False,
        tags: list[str] | None = None,
        tag_mode: str = "replace",
        ctx: RequestContext,
    ) -> dict[str, Any]:
        object_type = self._infer_target_type(uri)
        self._validate_mode(object_type, mode)
        if dry_run and mode != "prune_orphans":
            raise InvalidArgumentError("dry_run is only supported for prune_orphans reindex mode.")
        ingest_options = self._resolve_ingest_options(
            mode=mode,
            tags=tags,
            tag_mode=tag_mode,
        )

        tracker = get_task_tracker()
        if wait:
            if await tracker.has_running(
                REINDEX_TASK_TYPE,
                uri,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            ):
                raise OpenVikingError(
                    f"URI {uri} already has a reindex in progress",
                    code="CONFLICT",
                    details={"uri": uri},
                )
            return await self._run(
                uri=uri,
                object_type=object_type,
                mode=mode,
                dry_run=dry_run,
                ingest_options=ingest_options,
                ctx=ctx,
            )

        task = await tracker.create_if_no_running(
            REINDEX_TASK_TYPE,
            uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        if task is None:
            raise OpenVikingError(
                f"URI {uri} already has a reindex in progress",
                code="CONFLICT",
                details={"uri": uri},
            )

        asyncio.create_task(
            self._run_tracked(
                task.task_id,
                uri=uri,
                object_type=object_type,
                mode=mode,
                dry_run=dry_run,
                ingest_options=ingest_options,
                ctx=ctx,
            )
        )
        return {
            "task_id": task.task_id,
            "status": "accepted",
            "uri": uri,
            "object_type": object_type,
            "mode": mode,
        }

    async def apply_repair_plan(
        self,
        *,
        plan: dict[str, Any],
        wait: bool,
        dry_run: bool,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        """Validate and apply a preconditioned resource index repair plan."""
        root_uri = self._validate_repair_plan_envelope(plan, ctx)
        if wait:
            return await self._apply_repair_plan_locked(
                plan=plan,
                dry_run=dry_run,
                ctx=ctx,
            )

        tracker = get_task_tracker()
        task = await tracker.create_if_no_running(
            INDEX_REPAIR_TASK_TYPE,
            root_uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        if task is None:
            raise OpenVikingError(
                f"URI {root_uri} already has an index repair in progress",
                code="CONFLICT",
                details={"uri": root_uri},
            )
        asyncio.create_task(
            self._run_repair_tracked(
                task.task_id,
                plan=plan,
                dry_run=dry_run,
                ctx=ctx,
            )
        )
        return {
            "task_id": task.task_id,
            "status": "accepted",
            "uri": root_uri,
            "mode": "repair_plan",
        }

    @staticmethod
    def _validate_repair_plan_envelope(plan: dict[str, Any], ctx: RequestContext) -> str:
        if not isinstance(plan, dict):
            raise InvalidArgumentError("repair plan must be a JSON object")
        allowed = {
            "plan_version",
            "account_id",
            "root_uri",
            "collection",
            "root_fingerprint",
            "actions",
            "plan_digest",
        }
        if set(plan) != allowed:
            raise InvalidArgumentError("repair plan contains missing or unknown fields")
        if plan.get("plan_version") != REPAIR_PLAN_VERSION:
            raise InvalidArgumentError("unsupported repair plan version")
        supplied_digest = plan.get("plan_digest")
        unsigned = {key: value for key, value in plan.items() if key != "plan_digest"}
        if supplied_digest != canonical_digest(unsigned):
            raise InvalidArgumentError("repair plan digest mismatch")
        if plan.get("account_id") != ctx.account_id:
            raise InvalidArgumentError("repair plan account does not match request context")
        root_uri = plan.get("root_uri")
        if not isinstance(root_uri, str) or not (
            root_uri == "viking://resources" or root_uri.startswith("viking://resources/")
        ):
            raise InvalidArgumentError("repair plan root must be a resource subtree")
        if not isinstance(plan.get("actions"), list):
            raise InvalidArgumentError("repair plan actions must be a list")
        return root_uri

    async def _run_repair_tracked(
        self,
        task_id: str,
        *,
        plan: dict[str, Any],
        dry_run: bool,
        ctx: RequestContext,
    ) -> None:
        tracker = get_task_tracker()
        tracker.register_running_task(task_id)
        try:
            await tracker.start(task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
            with bind_task_context(task_id, ctx.account_id, ctx.user.user_id):
                result = await self._apply_repair_plan_locked(
                    plan=plan,
                    dry_run=dry_run,
                    ctx=ctx,
                )
            await tracker.complete(
                task_id,
                result,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await tracker.fail(
                task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        finally:
            await tracker.unregister_running_task(task_id)

    async def _apply_repair_plan_locked(
        self,
        *,
        plan: dict[str, Any],
        dry_run: bool,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        root_uri = self._validate_repair_plan_envelope(plan, ctx)
        service = get_service()
        if service.viking_fs is None or service.vikingdb_manager is None:
            raise RuntimeError("OpenVikingService not initialized")
        viking_fs = service.viking_fs
        path = viking_fs._uri_to_path(root_uri, ctx=ctx)
        lease = await viking_fs._async_agfs.pathlock_acquire_tree(path)
        started_at = time.perf_counter()
        telemetry_id = get_current_telemetry().telemetry_id
        wait_tracker = get_request_wait_tracker()
        if telemetry_id:
            wait_tracker.register_request(telemetry_id)
        try:
            actions, facts = await self._prevalidate_repair_plan(plan, ctx=ctx)
            results: list[dict[str, Any]] = []
            rebuilt = 0
            deleted = 0
            for action, state in actions:
                if state == "already_converged":
                    results.append(self._repair_action_result(action, state))
                    continue
                if dry_run:
                    results.append(self._repair_action_result(action, "would_apply"))
                    continue
                if action["action"] in {"delete", "delete_reindex"}:
                    deleted += await self.delete_uri_level(
                        uri=action["uri"], level=int(action["level"]), ctx=ctx
                    )
                if action["action"] in {"reindex", "delete_reindex"}:
                    fact = facts.get((action["uri"], int(action["level"])))
                    if fact is None:
                        raise FailedPreconditionError(
                            "stale repair plan",
                            details={"reason": "stale_plan"},
                        )
                    await self._repair_reindex_fact(fact, ctx=ctx)
                    rebuilt += 1
                results.append(self._repair_action_result(action, "applied"))

            if telemetry_id and rebuilt:
                await wait_tracker.wait_for_request(telemetry_id)
            status = "dry_run" if dry_run else "completed"
            if all(item["status"] == "already_converged" for item in results):
                status = "already_converged"
            return {
                "status": status,
                "uri": root_uri,
                "mode": "repair_plan",
                "action_count": len(results),
                "rebuilt_records": rebuilt,
                "deleted_records": deleted,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "actions": results,
            }
        finally:
            await viking_fs._async_agfs.pathlock_release(lease)
            if telemetry_id:
                wait_tracker.cleanup(telemetry_id)

    async def _prevalidate_repair_plan(
        self,
        plan: dict[str, Any],
        *,
        ctx: RequestContext,
    ) -> tuple[list[tuple[dict[str, Any], str]], dict[tuple[str, int], IndexSourceFact]]:
        service = get_service()
        assert service.viking_fs is not None
        assert service.vikingdb_manager is not None
        root_uri = str(plan["root_uri"])
        collection = plan.get("collection")
        if not isinstance(collection, dict) or set(collection) != {"name", "schema_fingerprint"}:
            raise InvalidArgumentError("invalid repair plan collection descriptor")
        if collection.get("name") != service.vikingdb_manager.collection_name:
            raise FailedPreconditionError(
                "stale repair plan", details={"reason": "active_collection_changed"}
            )
        meta = await service.vikingdb_manager.get_collection_meta(ctx=ctx)
        if canonical_digest(meta or {}) != collection.get("schema_fingerprint"):
            raise FailedPreconditionError(
                "stale repair plan", details={"reason": "collection_schema_changed"}
            )
        stat = await service.viking_fs.stat(root_uri, ctx=ctx, skip_count=True)
        if not stat.get("isDir", stat.get("is_dir", False)):
            raise FailedPreconditionError("stale repair plan", details={"reason": "root_changed"})
        entries = await self._tree_all(service.viking_fs, root_uri, show_all_hidden=True, ctx=ctx)
        source_facts, unresolved = await build_index_sources(
            service.viking_fs, root_uri, entries, ctx
        )
        if unresolved:
            raise FailedPreconditionError(
                "stale repair plan", details={"reason": "source_unverifiable"}
            )
        facts = {fact.key: fact for fact in source_facts}
        root_fingerprint = canonical_digest(
            [
                {"uri": fact.uri, "level": fact.level, "source_digest": fact.digest}
                for fact in source_facts
            ]
        )
        if root_fingerprint != plan.get("root_fingerprint"):
            raise FailedPreconditionError("stale repair plan", details={"reason": "root_changed"})

        validated: list[tuple[dict[str, Any], str]] = []
        for raw_action in plan["actions"]:
            action = self._validate_repair_action(raw_action, root_uri)
            level = int(action["level"])
            fact = facts.get((action["uri"], level))
            records = await service.vikingdb_manager.get_context_by_uri(
                uri=action["uri"],
                level=level,
                limit=100,
                ctx=ctx,
            )
            if self._repair_action_converged(action, fact, records, ctx):
                validated.append((action, "already_converged"))
                continue
            if action.get("expected_source_digest") != (fact.digest if fact else None):
                raise FailedPreconditionError(
                    "stale repair plan", details={"reason": "source_changed"}
                )
            if action.get("expected_index_fingerprint") != index_records_fingerprint(records):
                raise FailedPreconditionError(
                    "stale repair plan", details={"reason": "index_changed"}
                )
            validated.append((action, "pending"))
        return validated, facts

    @staticmethod
    def _validate_repair_action(action: Any, root_uri: str) -> dict[str, Any]:
        allowed = {
            "action",
            "uri",
            "level",
            "reason",
            "expected_source_digest",
            "expected_index_fingerprint",
        }
        if not isinstance(action, dict) or set(action) != allowed:
            raise InvalidArgumentError("invalid repair plan action")
        if action.get("action") not in {"reindex", "delete", "delete_reindex"}:
            raise InvalidArgumentError("unsupported repair plan action")
        uri = action.get("uri")
        if not isinstance(uri, str) or not (uri == root_uri or uri.startswith(root_uri + "/")):
            raise InvalidArgumentError("repair action is outside the plan root")
        level = action.get("level")
        if not isinstance(level, int) or isinstance(level, bool):
            raise InvalidArgumentError("repair action has an invalid level")
        if action.get("action") != "delete" and level not in {0, 1, 2}:
            raise InvalidArgumentError("repair reindex action has an invalid level")
        return action

    @staticmethod
    def _repair_action_converged(
        action: dict[str, Any],
        fact: IndexSourceFact | None,
        records: list[dict[str, Any]],
        ctx: RequestContext,
    ) -> bool:
        if action["action"] == "delete":
            return fact is None and not records
        if fact is None or len(records) != 1:
            return False
        record = records[0]
        expected_owner = owner_fields_for_uri(fact.uri, ctx=ctx).get("owner_user_id")
        return (
            record.get("uri") == fact.uri
            and record.get("level") == fact.level
            and record.get("context_type") == ContextType.RESOURCE.value
            and record.get("account_id") == ctx.account_id
            and record.get("owner_user_id") == expected_owner
            and record.get("source_digest") == fact.digest
        )

    async def _repair_reindex_fact(self, fact: IndexSourceFact, *, ctx: RequestContext) -> None:
        if fact.level in {int(ContextLevel.ABSTRACT), int(ContextLevel.OVERVIEW)}:
            abstract = fact.vector_text
            if fact.level == int(ContextLevel.OVERVIEW):
                abstract = _truncate_abstract_bytes(fact.vector_text)
            await self._upsert_context(
                uri=fact.uri,
                parent_uri=VikingURI(fact.uri).parent.uri,
                abstract=abstract,
                vector_text=fact.vector_text,
                is_leaf=False,
                context_type=ContextType.RESOURCE.value,
                level=ContextLevel(fact.level),
                ctx=ctx,
            )
            return
        service = get_service()
        assert service.viking_fs is not None
        resolved_summary = await file_summary(service.viking_fs, fact.uri, ctx)
        summary = resolved_summary.text if resolved_summary.state == SourceState.FOUND else ""
        await self._upsert_context(
            uri=fact.uri,
            parent_uri=VikingURI(fact.uri).parent.uri,
            abstract=self._prefer_non_empty(summary, fact.vector_text),
            vector_text=fact.vector_text,
            is_leaf=True,
            context_type=ContextType.RESOURCE.value,
            level=ContextLevel.DETAIL,
            ctx=ctx,
        )

    @staticmethod
    def _repair_action_result(action: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "action": action["action"],
            "uri": action["uri"],
            "level": action["level"],
            "reason": action["reason"],
            "status": status,
        }

    @staticmethod
    def _resolve_ingest_options(
        *,
        mode: str,
        tags: list[str] | None,
        tag_mode: str,
    ) -> IngestOptions | None:
        if mode == "prune_orphans" or tags is None:
            return None
        if tag_mode not in {"replace", "append"}:
            raise InvalidArgumentError(f"unsupported tag mode: {tag_mode}")
        return IngestOptions.from_search_tags(tags, mode=tag_mode)

    @staticmethod
    def _with_ingest_options(
        kwargs: dict[str, Any],
        ingest_options: IngestOptions | None,
    ) -> dict[str, Any]:
        if ingest_options is not None:
            kwargs["ingest_options"] = ingest_options
        return kwargs

    def _infer_target_type(self, uri: str) -> str:
        if not uri.startswith("viking://"):
            raise OpenVikingError(
                f"Unsupported reindex URI: {uri}",
                code="UNSUPPORTED_URI",
                details={"uri": uri},
            )
        classification = classify_uri(uri)
        parts = classification.parts
        if not parts:
            return "global_namespace"
        if is_session_uri(uri):
            raise OpenVikingError(
                f"Unsupported reindex URI: {uri}",
                code="UNSUPPORTED_URI",
                details={"uri": uri},
            )
        if parts == ("user",):
            return "user_namespace"
        if classification.is_user_namespace_root:
            return "user_namespace"
        if parts[0] == "agent":
            if len(parts) >= 2 and parts[1] in {"skills", "endpoints", "tools", "payments"}:
                if classification.is_skill_namespace:
                    return "skill_namespace"
                if classification.is_skill_root:
                    return "skill"
                if classification.is_skill:
                    raise OpenVikingError(
                        f"Unsupported reindex URI: {uri}",
                        code="UNSUPPORTED_URI",
                        details={"uri": uri},
                    )
                return "resource"
            raise OpenVikingError(
                "viking://agent/{agent_id}/... is no longer supported; "
                "use viking://agent/skills/... or viking://user/... instead.",
                code="UNSUPPORTED_URI",
                details={"uri": uri},
            )
        if classification.is_memory:
            return "memory"
        if classification.is_skill_namespace:
            return "skill_namespace"
        if classification.is_skill_root:
            return "skill"
        if classification.is_skill:
            raise OpenVikingError(
                f"Unsupported reindex URI: {uri}",
                code="UNSUPPORTED_URI",
                details={"uri": uri},
            )
        if parts[0] in {"resources", "user"}:
            return "resource"
        raise OpenVikingError(
            f"Unsupported reindex URI: {uri}",
            code="UNSUPPORTED_URI",
            details={"uri": uri},
        )

    async def _tree_all(
        self,
        viking_fs: Any,
        uri: str,
        *,
        show_all_hidden: bool,
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        return await viking_fs.tree(
            uri,
            output="original",
            show_all_hidden=show_all_hidden,
            node_limit=None,
            level_limit=None,
            ctx=ctx,
        )

    async def _refresh_namespace_resource_semantics(
        self,
        *,
        target_root: str,
        directories: list[str],
        files: list[str],
        run: _ReindexRunContext,
    ) -> tuple[list[str], list[str]]:
        counters = run.counters
        ctx = run.ctx
        prefix = self._child_prefix(target_root)
        semantic_roots = sorted(
            {
                directory_uri
                for directory_uri in directories
                if directory_uri.startswith(prefix)
                and "/" not in directory_uri[len(prefix) :]
                and directory_uri[len(prefix) :]
            }
        )
        filtered_directories = [
            directory_uri
            for directory_uri in directories
            if any(
                directory_uri == root or directory_uri.startswith(root + "/")
                for root in semantic_roots
            )
        ]
        filtered_files = [
            file_uri
            for file_uri in files
            if any(file_uri.startswith(root + "/") for root in semantic_roots)
        ]
        filtered_file_set = set(filtered_files)
        for file_uri in files:
            if file_uri in filtered_file_set:
                continue
            counters.unsupported_records += 1
            counters.warnings.append(
                f"Skipped {file_uri}: namespace semantic_and_vectors only refreshes resource directories"
            )
        for semantic_root in semantic_roots:
            await self._run_semantic_processor(
                uri=semantic_root,
                context_type="resource",
                ctx=ctx,
                lock=run.lock,
            )
        return filtered_directories, filtered_files

    @staticmethod
    def _child_prefix(root: str) -> str:
        if root.rstrip("/") == "viking:":
            return "viking://"
        return root.rstrip("/") + "/"

    @staticmethod
    def _apply_embedding_wait_status(
        counters: _ReindexCounters,
        queue_status: dict[str, Any],
    ) -> None:
        embedding_status = queue_status.get("Embedding") or {}
        error_count = int(embedding_status.get("error_count", 0) or 0)
        if error_count <= 0:
            return
        counters.failed_records += error_count
        counters.rebuilt_records = max(0, counters.rebuilt_records - error_count)
        for error in embedding_status.get("errors", []) or []:
            message = error.get("message") if isinstance(error, dict) else str(error)
            if message:
                counters.warnings.append(f"Embedding queue failed during reindex: {message}")

    def _is_resource_entry_for_namespace(self, uri: str, target_root: str) -> bool:
        if not uri.startswith(self._child_prefix(target_root)):
            return False
        classification = classify_uri(uri)
        if classification.is_memory or classification.is_skill:
            return False
        return True

    def _is_global_resource_entry(self, uri: str) -> bool:
        return uri == "viking://resources" or uri.startswith("viking://resources/")

    async def _reindex_skill_namespace(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
    ) -> None:
        counters = run.counters
        ctx = run.ctx
        viking_fs = get_viking_fs()
        try:
            entries = await self._tree_all(viking_fs, uri, show_all_hidden=True, ctx=ctx)
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        skill_roots = []
        for entry in entries:
            entry_uri = entry.get("uri")
            if entry_uri and entry.get("isDir") and classify_uri(entry_uri).is_skill_root:
                skill_roots.append(entry_uri)

        for skill_root in sorted(set(skill_roots)):
            await self._reindex_skill(
                uri=skill_root,
                mode=mode,
                run=run,
            )

        if not skill_roots:
            counters.unsupported_records += 1
            counters.warnings.append(f"No skill roots found under {uri}")

    def _validate_mode(self, object_type: str, mode: str) -> None:
        supported_modes = self.SUPPORTED_MODES_BY_TYPE[object_type]
        if mode not in supported_modes:
            raise OpenVikingError(
                f"Mode {mode} is not supported for {object_type}",
                code="UNSUPPORTED_MODE",
                details={
                    "mode": mode,
                    "object_type": object_type,
                    "supported_modes": sorted(supported_modes),
                },
            )

    async def _run(
        self,
        *,
        uri: str,
        object_type: str,
        mode: str,
        dry_run: bool = False,
        ingest_options: IngestOptions | None = None,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        service = get_service()
        if service.viking_fs is None or service.vikingdb_manager is None:
            raise RuntimeError("OpenVikingService not initialized")
        if mode != "prune_orphans" and not service.vikingdb_manager.has_queue_manager:
            raise OpenVikingError(
                "Reindex requires embedding queue",
                code="FAILED_PRECONDITION",
                details={"uri": uri},
            )

        path = service.viking_fs._uri_to_path(uri, ctx=ctx)
        started_at = time.perf_counter()
        counters = _ReindexCounters()
        telemetry_id = get_current_telemetry().telemetry_id
        wait_tracker = get_request_wait_tracker()
        if telemetry_id:
            wait_tracker.register_request(telemetry_id)

        acquire_lock = service.viking_fs._async_agfs.pathlock_acquire_tree
        if mode != "prune_orphans" or await service.viking_fs.exists(uri, ctx=ctx):
            stat = await service.viking_fs.stat(uri, ctx=ctx)
            if not stat.get("isDir", stat.get("is_dir")):
                acquire_lock = service.viking_fs._async_agfs.pathlock_acquire_exact
        lease = await acquire_lock(path)
        try:
            borrowed = await service.viking_fs._async_agfs.pathlock_as_borrowed(lease)
            run = _ReindexRunContext(
                ctx=ctx,
                counters=counters,
                lock=borrowed,
                ingest_options=ingest_options,
            )
            if mode == "prune_orphans":
                await self._prune_orphan_vectors(
                    uri=uri,
                    object_type=object_type,
                    dry_run=dry_run,
                    counters=counters,
                    ctx=ctx,
                )
            elif object_type == "global_namespace":
                await self._reindex_global_namespace(
                    uri=uri,
                    mode=mode,
                    run=run,
                )
            elif object_type == "user_namespace":
                await self._reindex_user_namespace(
                    uri=uri,
                    mode=mode,
                    run=run,
                )
            elif object_type == "skill_namespace":
                await self._reindex_skill_namespace(
                    uri=uri,
                    mode=mode,
                    run=run,
                )
            elif object_type == "resource":
                await self._reindex_resource(
                    uri=uri,
                    mode=mode,
                    run=run,
                )
            elif object_type == "skill":
                await self._reindex_skill(
                    uri=uri,
                    mode=mode,
                    run=run,
                )
            elif object_type == "memory":
                await self._reindex_memory(
                    uri=uri,
                    mode=mode,
                    run=run,
                )
            else:
                raise OpenVikingError(
                    f"Unsupported reindex type: {object_type}",
                    code="UNSUPPORTED_URI",
                    details={"uri": uri},
                )

            if telemetry_id and mode != "prune_orphans":
                await wait_tracker.wait_for_request(telemetry_id)
                self._apply_embedding_wait_status(
                    counters,
                    wait_tracker.build_queue_status(telemetry_id),
                )
        finally:
            await service.viking_fs._async_agfs.pathlock_release(lease)
            if telemetry_id:
                wait_tracker.cleanup(telemetry_id)

        return {
            "status": "completed",
            "uri": uri,
            "object_type": object_type,
            "mode": mode,
            "scanned_records": counters.scanned_records,
            "rebuilt_records": counters.rebuilt_records,
            "deleted_records": counters.deleted_records,
            "would_delete_records": counters.would_delete_records,
            "unsupported_records": counters.unsupported_records,
            "failed_records": counters.failed_records,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
            "warnings": counters.warnings,
        }

    async def _run_tracked(
        self,
        task_id: str,
        *,
        uri: str,
        object_type: str,
        mode: str,
        dry_run: bool = False,
        ingest_options: IngestOptions | None = None,
        ctx: RequestContext,
    ) -> None:
        tracker = get_task_tracker()
        tracker.register_running_task(task_id)
        try:
            await tracker.start(task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
            with bind_task_context(task_id, ctx.account_id, ctx.user.user_id):
                result = await self._run(
                    uri=uri,
                    object_type=object_type,
                    mode=mode,
                    dry_run=dry_run,
                    ingest_options=ingest_options,
                    ctx=ctx,
                )
            await tracker.complete(
                task_id,
                result,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        except asyncio.CancelledError:
            # TaskWorkIndex finalizes after this active task and its queue work settle.
            return
        except Exception as exc:
            await tracker.fail(
                task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        finally:
            await tracker.unregister_running_task(task_id)

    async def _prune_orphan_vectors(
        self,
        *,
        uri: str,
        object_type: str,
        dry_run: bool,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> None:
        service = get_service()
        vikingdb = service.vikingdb_manager
        delete_groups: dict[tuple[str, str], tuple[RequestContext, list[str]]] = {}

        for context_type in self._prune_context_types(uri=uri, object_type=object_type):
            offset = 0
            while True:
                filter_kwargs: dict[str, Any] = {}
                if offset:
                    filter_kwargs["offset"] = offset
                records = await vikingdb.filter(
                    filter=And(
                        [
                            Eq("account_id", ctx.account_id),
                            Eq("context_type", context_type),
                            Or(
                                [
                                    PathScope("uri", uri, depth=0),
                                    PathScope("uri", uri, depth=-1),
                                ]
                            ),
                        ]
                    ),
                    limit=PRUNE_ORPHAN_CANDIDATE_LIMIT,
                    output_fields=PRUNE_OUTPUT_FIELDS,
                    ctx=ctx,
                    **filter_kwargs,
                )
                if not records:
                    break

                for record in records:
                    counters.scanned_records += 1
                    if not self._is_supported_prune_record(record, counters):
                        continue
                    if not await self._is_orphan_vector_record(record, counters=counters, ctx=ctx):
                        continue

                    if dry_run:
                        counters.would_delete_records += 1
                        continue

                    delete_ctx = self._delete_ctx_for_prune_record(record, ctx)
                    key = (delete_ctx.account_id, delete_ctx.user.user_id)
                    if key not in delete_groups:
                        delete_groups[key] = (delete_ctx, [])
                    delete_groups[key][1].append(str(record["id"]))

                if len(records) < PRUNE_ORPHAN_CANDIDATE_LIMIT:
                    break
                offset += len(records)

        if dry_run:
            return

        for delete_ctx, ids in delete_groups.values():
            try:
                deleted = await vikingdb.delete(ids, ctx=delete_ctx)
                deleted_count = int(deleted if deleted is not None else len(ids))
                counters.deleted_records += deleted_count
                if deleted_count < len(ids):
                    failed_count = len(ids) - max(deleted_count, 0)
                    counters.failed_records += failed_count
                    counters.warnings.append(
                        f"Only deleted {deleted_count} of {len(ids)} orphan vectors for owner "
                        f"{delete_ctx.user.user_id}"
                    )
            except Exception as exc:
                counters.failed_records += len(ids)
                counters.warnings.append(
                    f"Failed to delete {len(ids)} orphan vectors for owner "
                    f"{delete_ctx.user.user_id}: {exc}"
                )

    def _prune_context_types(self, *, uri: str, object_type: str) -> list[str]:
        if object_type == "resource":
            return [ContextType.RESOURCE.value]
        if object_type == "memory":
            return [ContextType.MEMORY.value]
        if object_type in {"skill", "skill_namespace"}:
            return [ContextType.SKILL.value]
        if object_type in {"global_namespace", "user_namespace"}:
            return [
                ContextType.RESOURCE.value,
                ContextType.MEMORY.value,
                ContextType.SKILL.value,
            ]
        return [str(context_type_for_uri(uri))]

    def _is_supported_prune_record(
        self,
        record: dict[str, Any],
        counters: _ReindexCounters,
    ) -> bool:
        record_id = record.get("id")
        uri = record.get("uri")
        context_type = str(record.get("context_type") or "")
        level = record.get("level")
        if not record_id or not uri:
            counters.unsupported_records += 1
            counters.warnings.append(f"Skipping unknown prune record without id/uri: {record!r}")
            return False
        if context_type not in {
            ContextType.RESOURCE.value,
            ContextType.MEMORY.value,
            ContextType.SKILL.value,
        }:
            counters.unsupported_records += 1
            counters.warnings.append(f"Skipping unknown context_type for prune record {uri}")
            return False
        try:
            normalized_level = int(level)
        except (TypeError, ValueError):
            counters.unsupported_records += 1
            counters.warnings.append(f"Skipping unknown level for prune record {uri}: {level}")
            return False
        if normalized_level not in {
            int(ContextLevel.ABSTRACT),
            int(ContextLevel.OVERVIEW),
            int(ContextLevel.DETAIL),
        }:
            counters.unsupported_records += 1
            counters.warnings.append(f"Skipping unknown level for prune record {uri}: {level}")
            return False
        record["_prune_level"] = normalized_level
        return True

    async def _is_orphan_vector_record(
        self,
        record: dict[str, Any],
        *,
        counters: _ReindexCounters,
        ctx: RequestContext,
    ) -> bool:
        uri = str(record["uri"])
        level = int(record.get("_prune_level", record["level"]))
        context_type = str(record["context_type"])
        owner_ctx = self._delete_ctx_for_prune_record(record, ctx)

        if level == int(ContextLevel.ABSTRACT):
            abstract = await self._read_prune_source(
                f"{uri}/.abstract.md",
                ctx=owner_ctx,
            )
            if abstract.error:
                self._record_prune_source_error(
                    counters=counters,
                    uri=uri,
                    source_uri=f"{uri}/.abstract.md",
                    error=abstract.error,
                )
                return False
            return (
                not abstract.exists
                or not abstract.text
                or _is_not_ready_sentinel(abstract.text, _ABSTRACT_NOT_READY_SUFFIX)
            )

        if level == int(ContextLevel.OVERVIEW):
            overview = await self._read_prune_source(
                f"{uri}/.overview.md",
                ctx=owner_ctx,
            )
            if overview.error:
                self._record_prune_source_error(
                    counters=counters,
                    uri=uri,
                    source_uri=f"{uri}/.overview.md",
                    error=overview.error,
                )
                return False
            if (
                overview.exists
                and overview.text
                and not _is_not_ready_sentinel(overview.text, _OVERVIEW_NOT_READY_SUFFIX)
            ):
                return False
            if context_type in {ContextType.RESOURCE.value, ContextType.SKILL.value}:
                abstract = await self._read_prune_source(
                    f"{uri}/.abstract.md",
                    ctx=owner_ctx,
                )
                if abstract.error:
                    self._record_prune_source_error(
                        counters=counters,
                        uri=uri,
                        source_uri=f"{uri}/.abstract.md",
                        error=abstract.error,
                    )
                    return False
                return (
                    not abstract.exists
                    or not abstract.text
                    or _is_not_ready_sentinel(abstract.text, _ABSTRACT_NOT_READY_SUFFIX)
                )
            return True

        if "#" in uri:
            if context_type == ContextType.MEMORY.value and "#chunk_" in uri:
                base_uri = uri.split("#chunk_", 1)[0]
                base = await self._read_prune_source(base_uri, ctx=owner_ctx)
                if base.error:
                    self._record_prune_source_error(
                        counters=counters,
                        uri=uri,
                        source_uri=base_uri,
                        error=base.error,
                    )
                    return False
                if not base.exists:
                    return True
                expected = {
                    chunk_uri for chunk_uri, _chunk in self._chunk_memory_body(base_uri, base.text)
                }
                return uri not in expected
            return False

        if self._is_hidden_meta_file(uri):
            return False
        exists = await self._prune_source_exists(uri, ctx=owner_ctx)
        if exists.error:
            self._record_prune_source_error(
                counters=counters,
                uri=uri,
                source_uri=uri,
                error=exists.error,
            )
            return False
        return not exists.exists

    async def _read_prune_source(self, uri: str, *, ctx: RequestContext) -> _PruneSourceRead:
        viking_fs = get_viking_fs()
        try:
            exists = await viking_fs.exists(uri, ctx=ctx)
        except Exception as exc:
            return _PruneSourceRead(exists=False, error=exc)
        if not exists:
            return _PruneSourceRead(exists=False)
        try:
            content = await viking_fs.read_file(uri, ctx=ctx)
        except Exception as exc:
            return _PruneSourceRead(exists=True, error=exc)
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = str(content or "")
        return _PruneSourceRead(exists=True, text=text)

    async def _prune_source_exists(self, uri: str, *, ctx: RequestContext) -> _PruneSourceRead:
        try:
            exists = await get_viking_fs().exists(uri, ctx=ctx)
        except Exception as exc:
            return _PruneSourceRead(exists=False, error=exc)
        return _PruneSourceRead(exists=exists)

    def _record_prune_source_error(
        self,
        *,
        counters: _ReindexCounters | None,
        uri: str,
        source_uri: str,
        error: Exception,
    ) -> None:
        if counters is None:
            return
        counters.failed_records += 1
        counters.warnings.append(f"Skipped prune for {uri}: failed to read {source_uri}: {error}")

    def _delete_ctx_for_prune_record(
        self,
        record: dict[str, Any],
        ctx: RequestContext,
    ) -> RequestContext:
        uri = str(record.get("uri") or "")
        owner = record.get("owner_user_id")
        if not owner:
            owner = owner_fields_for_uri(uri).get("owner_user_id")
        if not owner or owner == ctx.user.user_id:
            return ctx
        return RequestContext(
            user=UserIdentifier(ctx.account_id, str(owner)),
            role=ctx.role,
            actor_peer_id=ctx.actor_peer_id,
            from_oauth=ctx.from_oauth,
        )

    async def _reindex_resource(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
    ) -> None:
        counters = run.counters
        ctx = run.ctx
        if mode == "semantic_and_vectors":
            await self._run_semantic_processor(
                uri=uri,
                context_type="resource",
                ctx=ctx,
                lock=run.lock,
            )
            await self._reindex_resource_vectors(
                **self._with_ingest_options(
                    {"uri": uri, "counters": counters, "ctx": ctx},
                    run.ingest_options,
                )
            )
            return
        await self._reindex_resource_vectors(
            **self._with_ingest_options(
                {"uri": uri, "counters": counters, "ctx": ctx},
                run.ingest_options,
            )
        )

    async def _reindex_skill(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
    ) -> None:
        counters = run.counters
        ctx = run.ctx
        if mode == "semantic_and_vectors":
            await self._regenerate_skill_semantics(uri=uri, ctx=ctx)
        await self._reindex_skill_vectors(
            **self._with_ingest_options(
                {"uri": uri, "counters": counters, "ctx": ctx},
                run.ingest_options,
            )
        )

    async def _reindex_memory(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
    ) -> None:
        counters = run.counters
        ctx = run.ctx
        if mode == "semantic_and_vectors":
            stat = await get_viking_fs().stat(uri, ctx=ctx)
            if stat.get("isDir", stat.get("is_dir")):
                await self._run_semantic_processor(
                    uri=uri,
                    context_type="memory",
                    ctx=ctx,
                    lock=run.lock,
                )
            await self._reindex_memory_vectors(
                **self._with_ingest_options(
                    {"uri": uri, "counters": counters, "ctx": ctx},
                    run.ingest_options,
                )
            )
            return
        await self._reindex_memory_vectors(
            **self._with_ingest_options(
                {"uri": uri, "counters": counters, "ctx": ctx},
                run.ingest_options,
            )
        )

    async def _run_semantic_processor(
        self,
        *,
        uri: str,
        context_type: str,
        ctx: RequestContext,
        lock: dict | None = None,
    ) -> None:
        processor = SemanticProcessor(
            max_concurrent_llm=get_openviking_config().vlm.max_concurrent,
        )
        owner_ctx = self._content_owner_ctx(uri, ctx)
        msg = SemanticMsg(
            uri=uri,
            context_type=context_type,
            recursive=True,
            account_id=owner_ctx.account_id,
            user_id=owner_ctx.user.user_id,
            peer_id=owner_ctx.user.user_id,
            role=str(ctx.role),
            skip_vectorization=True,
        )
        await processor.on_dequeue({"data": msg.to_json()}, lock=lock)

    async def _reindex_resource_vectors(
        self,
        *,
        uri: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
    ) -> None:
        viking_fs = get_viking_fs()
        try:
            if not await viking_fs.exists(uri, ctx=ctx):
                raise NotFoundError(uri, "resource")
            stat = await viking_fs.stat(uri, ctx=ctx)
            is_dir = stat.get("isDir", stat.get("is_dir")) if isinstance(stat, dict) else False
            if is_dir:
                entries = await self._tree_all(viking_fs, uri, show_all_hidden=True, ctx=ctx)
            else:
                entries = []
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        if is_dir:
            directories = [uri]
            files: list[str] = []
            for entry in entries:
                entry_uri = entry.get("uri")
                if not entry_uri:
                    continue
                if entry.get("isDir"):
                    directories.append(entry_uri)
                elif not self._is_hidden_meta_file(entry_uri):
                    files.append(entry_uri)
        else:
            directories = []
            files = [uri]

        await self._reindex_resource_vectors_from_entries(
            **self._with_ingest_options(
                {
                    "root_uri": uri,
                    "directories": directories,
                    "files": files,
                    "counters": counters,
                    "ctx": ctx,
                },
                ingest_options,
            )
        )

    async def _reindex_resource_vectors_from_entries(
        self,
        *,
        root_uri: str,
        directories: Iterable[str],
        files: Iterable[str],
        counters: _ReindexCounters,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
    ) -> None:
        deduped_directories = []
        seen_directories = set()
        for directory_uri in directories:
            if directory_uri and directory_uri not in seen_directories:
                deduped_directories.append(directory_uri)
                seen_directories.add(directory_uri)

        deduped_files = []
        seen_files = set()
        for file_uri in files:
            if file_uri and file_uri not in seen_files:
                deduped_files.append(file_uri)
                seen_files.add(file_uri)

        for directory_uri in deduped_directories:
            if directory_uri == "viking://":
                continue
            counters.scanned_records += 1
            abstract = await self._read_directory_abstract(directory_uri, ctx=ctx)
            overview = await self._read_directory_overview(directory_uri, ctx=ctx)
            if not overview:
                overview = abstract
            if not abstract and not overview:
                counters.unsupported_records += 1
                counters.warnings.append(f"No semantic source found for {directory_uri}")
                continue
            if abstract:
                try:
                    await self._upsert_context(
                        uri=directory_uri,
                        parent_uri=VikingURI(directory_uri).parent.uri,
                        abstract=abstract,
                        vector_text=embedding_text_for_body(
                            ContextLevel.ABSTRACT, directory_uri, abstract
                        ),
                        is_leaf=False,
                        context_type=context_type_for_uri(directory_uri),
                        level=ContextLevel.ABSTRACT,
                        ctx=ctx,
                        ingest_options=ingest_options,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L0 vector: {exc}")
            if overview:
                try:
                    await self._upsert_context(
                        uri=directory_uri,
                        parent_uri=VikingURI(directory_uri).parent.uri,
                        # L1 abstract scalar carries the overview for Rerank.
                        abstract=_truncate_abstract_bytes(overview),
                        vector_text=embedding_text_for_body(
                            ContextLevel.OVERVIEW, directory_uri, overview
                        ),
                        is_leaf=False,
                        context_type=context_type_for_uri(directory_uri),
                        level=ContextLevel.OVERVIEW,
                        ctx=ctx,
                        ingest_options=ingest_options,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L1 vector: {exc}")

        async def process_file(file_uri: str) -> _ReindexCounters:
            file_counters = _ReindexCounters(scanned_records=1)
            parent_uri = VikingURI(file_uri).parent.uri
            summary = await self._best_file_summary(file_uri, ctx=ctx)
            vector_text = await self._best_resource_file_vector_text(file_uri, summary, ctx=ctx)
            if not vector_text:
                file_counters.unsupported_records += 1
                file_counters.warnings.append(f"No vector source found for {file_uri}")
                return file_counters
            abstract = self._prefer_non_empty(summary, vector_text)
            try:
                await self._upsert_context(
                    uri=file_uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=vector_text,
                    is_leaf=True,
                    context_type=context_type_for_uri(file_uri),
                    level=ContextLevel.DETAIL,
                    ctx=ctx,
                    ingest_options=ingest_options,
                )
                file_counters.rebuilt_records += 1
            except Exception as exc:
                file_counters.failed_records += 1
                file_counters.warnings.append(f"Failed to reindex {file_uri} vector: {exc}")
            return file_counters

        concurrency = self._effective_file_vectorization_concurrency()
        if deduped_files:
            logger.info(
                "Reindex resource file vectorization: root=%s files=%d concurrency=%d",
                root_uri,
                len(deduped_files),
                concurrency,
            )
        await self._run_ordered_counter_batches(
            deduped_files,
            concurrency=concurrency,
            processor=process_file,
            counters=counters,
        )

    async def reindex_directory_marker(
        self, *, dir_uri: str, level: ContextLevel, ctx: RequestContext
    ) -> None:
        """Recompute ONLY this directory's L0 (ABSTRACT) or L1 (OVERVIEW) vector.

        Non-recursive: does not touch descendants. Used by git restore when a
        directory's ``.abstract.md`` / ``.overview.md`` marker changed. When the
        on-disk semantic source is empty, the corresponding vector is deleted
        instead of upserted.
        """
        if level not in (ContextLevel.ABSTRACT, ContextLevel.OVERVIEW):
            raise ValueError(f"reindex_directory_marker only supports L0/L1, got {level!r}")
        if dir_uri == "viking://":
            return

        viking_fs = get_viking_fs()
        marker_name = ".abstract.md" if level == ContextLevel.ABSTRACT else ".overview.md"
        lock_path = viking_fs._uri_to_path(f"{dir_uri}/{marker_name}", ctx=ctx)
        lease = await viking_fs._async_agfs.pathlock_acquire_exact(lock_path)
        try:
            abstract = await self._read_directory_abstract(dir_uri, ctx=ctx)
            if level == ContextLevel.ABSTRACT:
                vector_text = abstract
            else:
                overview = await self._read_directory_overview(dir_uri, ctx=ctx)
                vector_text = overview or abstract

            if not vector_text:
                await self.delete_uri_level(uri=dir_uri, level=level, ctx=ctx)
                return

            # L1 abstract scalar carries the overview for Rerank.
            if level == ContextLevel.OVERVIEW:
                abstract = _truncate_abstract_bytes(vector_text)

            await self._upsert_context(
                uri=dir_uri,
                parent_uri=VikingURI(dir_uri).parent.uri,
                abstract=abstract,
                vector_text=vector_text,
                is_leaf=False,
                context_type=context_type_for_uri(dir_uri),
                level=level,
                ctx=ctx,
            )
        finally:
            await viking_fs._async_agfs.pathlock_release(lease)

    async def delete_uri_level(
        self, *, uri: str, level: ContextLevel | int, ctx: RequestContext
    ) -> int:
        """Delete ONLY the vector record at ``(uri, level)``. Returns count.

        Used by git restore for both directory markers (dir + L0/L1) and
        deleted source files (file + DETAIL).
        """
        service = get_service()
        assert service.vikingdb_manager is not None
        records = await service.vikingdb_manager.get_context_by_uri(
            uri=uri,
            level=int(level),
            limit=100,
            ctx=ctx,
        )
        ids = [str(rec["id"]) for rec in records if rec.get("id")]
        if not ids:
            return 0
        return await service.vikingdb_manager.delete(ids, ctx=ctx)

    async def _reindex_user_namespace(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
    ) -> None:
        counters = run.counters
        ctx = run.ctx
        normalized_uri = uri.rstrip("/")
        target_root = normalized_uri if normalized_uri else uri
        viking_fs = get_viking_fs()
        try:
            entries = await self._tree_all(viking_fs, target_root, show_all_hidden=True, ctx=ctx)
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        if target_root == "viking://user":
            user_roots = [
                entry.get("uri")
                for entry in entries
                if entry.get("isDir") and classify_uri(entry.get("uri", "")).is_user_namespace_root
            ]
            if user_roots:
                for user_root in sorted(set(user_roots)):
                    await self._reindex_user_namespace(
                        uri=user_root,
                        mode=mode,
                        run=run,
                    )
                return

        memory_roots: list[str] = []
        skill_roots: list[str] = []
        resource_directories: list[str] = []
        resource_files: list[str] = []

        for entry in entries:
            entry_uri = entry.get("uri")
            if not entry_uri:
                continue
            if is_session_uri(entry_uri):
                continue
            classification = classify_uri(entry_uri)
            if classification.is_memory:
                if entry.get("isDir") and classification.is_memory_root:
                    memory_roots.append(entry_uri)
                continue
            if classification.is_skill:
                if entry.get("isDir") and classification.is_skill_root:
                    skill_roots.append(entry_uri)
                continue
            if not self._is_resource_entry_for_namespace(entry_uri, target_root):
                continue
            if entry.get("isDir"):
                resource_directories.append(entry_uri)
            elif not self._is_hidden_meta_file(entry_uri):
                resource_files.append(entry_uri)

        for memory_root in sorted(set(memory_roots)):
            memory_mode = (
                "semantic_and_vectors" if mode == "semantic_and_vectors" else "vectors_only"
            )
            await self._reindex_memory(
                uri=memory_root,
                mode=memory_mode,
                run=run,
            )

        for skill_root in sorted(set(skill_roots)):
            skill_mode = (
                "semantic_and_vectors" if mode == "semantic_and_vectors" else "vectors_only"
            )
            await self._reindex_skill(
                uri=skill_root,
                mode=skill_mode,
                run=run,
            )

        if mode == "semantic_and_vectors":
            (
                resource_directories,
                resource_files,
            ) = await self._refresh_namespace_resource_semantics(
                target_root=target_root,
                directories=resource_directories,
                files=resource_files,
                run=run,
            )

        await self._reindex_resource_vectors_from_entries(
            **self._with_ingest_options(
                {
                    "root_uri": target_root,
                    "directories": resource_directories,
                    "files": resource_files,
                    "counters": counters,
                    "ctx": ctx,
                },
                run.ingest_options,
            )
        )

    async def _reindex_global_namespace(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
    ) -> None:
        counters = run.counters
        ctx = run.ctx
        target_root = "viking://"
        viking_fs = get_viking_fs()
        try:
            entries = await self._tree_all(viking_fs, target_root, show_all_hidden=True, ctx=ctx)
        except Exception as exc:
            raise NotFoundError(uri, "resource") from exc

        user_roots: list[str] = []
        resource_directories: list[str] = []
        resource_files: list[str] = []

        for entry in entries:
            entry_uri = entry.get("uri")
            if not entry_uri:
                continue
            if entry_uri == "viking://user":
                continue
            if entry_uri.startswith("viking://user/"):
                remainder = entry_uri[len("viking://user/") :]
                if entry.get("isDir") and remainder and "/" not in remainder:
                    user_roots.append(entry_uri)
                continue
            if is_session_uri(entry_uri):
                continue
            if not self._is_global_resource_entry(entry_uri):
                continue
            if entry.get("isDir"):
                resource_directories.append(entry_uri)
            elif not self._is_hidden_meta_file(entry_uri):
                resource_files.append(entry_uri)

        for user_root in sorted(set(user_roots)):
            await self._reindex_user_namespace(
                uri=user_root,
                mode=mode,
                run=run,
            )

        if mode == "semantic_and_vectors":
            (
                resource_directories,
                resource_files,
            ) = await self._refresh_namespace_resource_semantics(
                target_root=target_root,
                directories=resource_directories,
                files=resource_files,
                run=run,
            )

        await self._reindex_resource_vectors_from_entries(
            **self._with_ingest_options(
                {
                    "root_uri": target_root,
                    "directories": resource_directories,
                    "files": resource_files,
                    "counters": counters,
                    "ctx": ctx,
                },
                run.ingest_options,
            )
        )

    async def _reindex_skill_vectors(
        self,
        *,
        uri: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
    ) -> None:
        viking_fs = get_viking_fs()
        counters.scanned_records += 1

        abstract = await self._read_directory_abstract(uri, ctx=ctx)
        overview = await self._read_directory_overview(uri, ctx=ctx)
        if not abstract:
            record = await self._fetch_existing_record(
                uri=uri,
                level=0,
                ctx=self._content_owner_ctx(uri, ctx),
            )
            abstract = self._record_abstract(record)
        if not overview:
            record = await self._fetch_existing_record(
                uri=uri,
                level=1,
                ctx=self._content_owner_ctx(uri, ctx),
            )
            overview = self._record_abstract(record) or abstract

        if not abstract and not overview:
            counters.unsupported_records += 1
            counters.warnings.append(f"No semantic source found for {uri}")
            return

        parent_uri = VikingURI(uri).parent.uri
        if abstract:
            try:
                await self._upsert_context(
                    uri=uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=embedding_text_for_body(ContextLevel.ABSTRACT, uri, abstract),
                    is_leaf=False,
                    context_type=ContextType.SKILL.value,
                    level=ContextLevel.ABSTRACT,
                    meta=await self._skill_meta(uri=uri, abstract=abstract, ctx=ctx),
                    ctx=ctx,
                    ingest_options=ingest_options,
                )
                counters.rebuilt_records += 1
            except Exception as exc:
                counters.failed_records += 1
                counters.warnings.append(f"Failed to reindex {uri} L0 vector: {exc}")
        if overview:
            try:
                await self._upsert_context(
                    uri=uri,
                    parent_uri=parent_uri,
                    # L1 abstract scalar carries the overview for Rerank.
                    abstract=_truncate_abstract_bytes(overview),
                    vector_text=embedding_text_for_body(ContextLevel.OVERVIEW, uri, overview),
                    is_leaf=False,
                    context_type=ContextType.SKILL.value,
                    level=ContextLevel.OVERVIEW,
                    meta=await self._skill_meta(uri=uri, abstract=abstract, ctx=ctx),
                    ctx=ctx,
                    ingest_options=ingest_options,
                )
                counters.rebuilt_records += 1
            except Exception as exc:
                counters.failed_records += 1
                counters.warnings.append(f"Failed to reindex {uri} L1 vector: {exc}")

        skill_file_uri = f"{uri}/SKILL.md"
        if await viking_fs.exists(skill_file_uri, ctx=ctx):
            counters.scanned_records += 1
            skill_content = await self._safe_read_text(skill_file_uri, ctx=ctx)
            if skill_content:
                detail_abstract = self._prefer_non_empty(abstract, skill_content)
                try:
                    await self._upsert_context(
                        uri=skill_file_uri,
                        parent_uri=uri,
                        abstract=detail_abstract,
                        vector_text=skill_content,
                        is_leaf=True,
                        context_type=ContextType.SKILL.value,
                        level=ContextLevel.DETAIL,
                        ctx=ctx,
                        ingest_options=ingest_options,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {skill_file_uri} vector: {exc}")

    async def _reindex_memory_vectors(
        self,
        *,
        uri: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
    ) -> None:
        viking_fs = get_viking_fs()
        if await viking_fs.exists(uri, ctx=ctx):
            stat = await viking_fs.stat(uri, ctx=ctx)
            if stat.get("isDir", stat.get("is_dir")):
                entries = await self._tree_all(viking_fs, uri, show_all_hidden=False, ctx=ctx)
                directory_uris = {uri}
                for entry in entries:
                    entry_uri = entry.get("uri")
                    if entry_uri and entry.get("isDir"):
                        directory_uris.add(entry_uri)
                await self._reindex_memory_directory_chain(
                    **self._with_ingest_options(
                        {
                            "directory_uris": sorted(directory_uris),
                            "counters": counters,
                            "ctx": ctx,
                        },
                        ingest_options,
                    )
                )
                file_uris = [entry["uri"] for entry in entries if not entry.get("isDir")]
            else:
                file_uris = [uri]
        else:
            raise NotFoundError(uri, "memory")

        async def process_file(file_uri: str) -> _ReindexCounters:
            file_counters = _ReindexCounters(scanned_records=1)
            body_source = await self._read_memory_body(file_uri, ctx=ctx)
            if body_source.error:
                file_counters.failed_records += 1
                file_counters.warnings.append(
                    f"Skipped {file_uri}: failed to read memory body: {body_source.error}"
                )
                return file_counters
            body = body_source.text if body_source.exists else ""
            memory_content = MemoryFileUtils.read(body).content if body else ""
            existing = await self._fetch_existing_record(
                uri=file_uri,
                level=2,
                ctx=self._content_owner_ctx(file_uri, ctx),
            )
            abstract = self._best_non_empty(
                self._record_abstract(existing),
                await self._best_file_summary(file_uri, ctx=ctx),
            )
            if not body and existing is None:
                file_counters.unsupported_records += 1
                file_counters.warnings.append(f"No memory source found for {file_uri}")
                return file_counters

            parent_uri = VikingURI(file_uri.split("#", 1)[0]).parent.uri
            if body:
                detail_abstract = self._prefer_non_empty(abstract, memory_content, body)
                try:
                    await self._upsert_context(
                        uri=file_uri,
                        parent_uri=parent_uri,
                        abstract=detail_abstract,
                        vector_text=body,
                        is_leaf=True,
                        context_type=ContextType.MEMORY.value,
                        level=ContextLevel.DETAIL,
                        ctx=ctx,
                        ingest_options=ingest_options,
                    )
                    file_counters.rebuilt_records += 1
                except Exception as exc:
                    file_counters.failed_records += 1
                    file_counters.warnings.append(f"Failed to reindex {file_uri} vector: {exc}")
                return file_counters

            try:
                await self._upsert_context(
                    uri=file_uri,
                    parent_uri=parent_uri,
                    abstract=abstract,
                    vector_text=abstract,
                    is_leaf=True,
                    context_type=ContextType.MEMORY.value,
                    level=ContextLevel.DETAIL,
                    ctx=ctx,
                    ingest_options=ingest_options,
                )
                file_counters.rebuilt_records += 1
                file_counters.warnings.append(
                    f"Reindexed {file_uri} from abstract fallback because original memory body is unavailable"
                )
            except Exception as exc:
                file_counters.failed_records += 1
                file_counters.warnings.append(f"Failed to reindex {file_uri} vector: {exc}")
            return file_counters

        concurrency = self._effective_file_vectorization_concurrency()
        if file_uris:
            logger.info(
                "Reindex memory file vectorization: root=%s files=%d concurrency=%d",
                uri,
                len(file_uris),
                concurrency,
            )
        await self._run_ordered_counter_batches(
            file_uris,
            concurrency=concurrency,
            processor=process_file,
            counters=counters,
        )

    async def _reindex_memory_directory_chain(
        self,
        *,
        directory_uris: Iterable[str],
        counters: _ReindexCounters,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
    ) -> None:
        for directory_uri in directory_uris:
            counters.scanned_records += 1
            abstract = await self._read_directory_abstract(directory_uri, ctx=ctx)
            overview = await self._read_directory_overview(directory_uri, ctx=ctx)
            if not abstract and not overview:
                continue

            parent_uri = VikingURI(directory_uri).parent.uri
            if abstract:
                try:
                    await self._upsert_context(
                        uri=directory_uri,
                        parent_uri=parent_uri,
                        abstract=abstract,
                        vector_text=embedding_text_for_body(
                            ContextLevel.ABSTRACT, directory_uri, abstract
                        ),
                        is_leaf=False,
                        context_type=ContextType.MEMORY.value,
                        level=ContextLevel.ABSTRACT,
                        ctx=ctx,
                        ingest_options=ingest_options,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L0 vector: {exc}")
            if overview:
                try:
                    await self._upsert_context(
                        uri=directory_uri,
                        parent_uri=parent_uri,
                        # L1 abstract scalar carries the overview for Rerank.
                        abstract=_truncate_abstract_bytes(overview),
                        vector_text=embedding_text_for_body(
                            ContextLevel.OVERVIEW, directory_uri, overview
                        ),
                        is_leaf=False,
                        context_type=ContextType.MEMORY.value,
                        level=ContextLevel.OVERVIEW,
                        ctx=ctx,
                        ingest_options=ingest_options,
                    )
                    counters.rebuilt_records += 1
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L1 vector: {exc}")

    async def _regenerate_skill_semantics(self, *, uri: str, ctx: RequestContext) -> None:
        service = get_service()
        if service.viking_fs is None or service.vikingdb_manager is None:
            raise RuntimeError("OpenVikingService not initialized")

        viking_fs = service.viking_fs
        skill_file_uri = f"{uri}/SKILL.md"
        skill_content = await self._safe_read_text(skill_file_uri, ctx=ctx)
        if not skill_content:
            raise OpenVikingError(
                f"SKILL.md not found for {uri}",
                code="NOT_FOUND",
                details={"uri": uri},
            )

        skill_dict, _, _, _ = SkillProcessor(service.vikingdb_manager)._parse_skill(
            skill_content,
            allow_local_path_resolution=False,
        )
        overview = await SkillProcessor(service.vikingdb_manager)._generate_overview(
            skill_dict,
            get_openviking_config(),
        )
        await viking_fs.write_context(
            uri=uri,
            content=skill_content,
            abstract=skill_dict.get("description", ""),
            overview=overview,
            content_filename="SKILL.md",
            is_leaf=False,
            ctx=ctx,
        )

    async def _read_directory_abstract(self, uri: str, *, ctx: RequestContext) -> str:
        source = await directory_source(get_viking_fs(), uri, int(ContextLevel.ABSTRACT), ctx)
        return source.text if source.state == SourceState.FOUND else ""

    async def _read_directory_overview(self, uri: str, *, ctx: RequestContext) -> str:
        source = await directory_source(get_viking_fs(), uri, int(ContextLevel.OVERVIEW), ctx)
        if source.state != SourceState.FOUND:
            return ""
        abstract = await directory_source(get_viking_fs(), uri, int(ContextLevel.ABSTRACT), ctx)
        return "" if source.text == abstract.text else source.text

    async def _best_file_summary(self, uri: str, *, ctx: RequestContext) -> str:
        parent_uri = VikingURI(uri).parent.uri
        file_name = uri.rsplit("/", 1)[-1]
        overviews = await self._safe_read_text(f"{parent_uri}/.overview.md", ctx=ctx)
        if overviews:
            parsed = parse_overview(overviews)
            if file_name in parsed:
                return parsed[file_name]
        existing = await self._fetch_existing_record(
            uri=uri,
            level=2,
            ctx=self._content_owner_ctx(uri, ctx),
        )
        return self._record_abstract(existing)

    async def _best_resource_file_vector_text(
        self,
        uri: str,
        summary: str,
        ctx: RequestContext,
    ) -> str:
        existing = await self._fetch_existing_record(
            uri=uri,
            level=2,
            ctx=self._content_owner_ctx(uri, ctx),
        )
        fallback = self._record_abstract(existing)
        content_type = get_resource_content_type(uri.rsplit("/", 1)[-1])

        if content_type == ResourceContentType.TEXT:
            embedding_config = get_openviking_config().embedding
            if summary and summary_source_selected(embedding_config):
                return summary
            content = await self._safe_read_text(uri, ctx=ctx)
            return select_resource_file_vector_text(content, summary, fallback, embedding_config)

        if summary:
            return summary
        return fallback

    async def _upsert_context(
        self,
        *,
        uri: str,
        parent_uri: str,
        abstract: str,
        vector_text: str,
        is_leaf: bool,
        context_type: str,
        level: ContextLevel,
        ctx: RequestContext,
        meta: Optional[dict[str, Any]] = None,
        ingest_options: IngestOptions | None = None,
    ) -> None:
        service = get_service()
        assert service.vikingdb_manager is not None
        merged_meta = dict(meta or {})
        owner_ctx = self._content_owner_ctx(uri, ctx)

        context = Context(
            uri=uri,
            parent_uri=parent_uri,
            is_leaf=is_leaf,
            abstract=abstract or "",
            context_type=context_type,
            level=level,
            user=owner_ctx.user,
            account_id=owner_ctx.account_id,
            owner_space=owner_space_for_uri(uri),
            meta=merged_meta,
        )
        context.set_vectorize(Vectorize(text=vector_text))
        msg = EmbeddingMsgConverter.from_context(context)
        _apply_ingest_options(msg, ingest_options)
        if msg is None:
            raise OpenVikingError(
                f"No vector text generated for {uri}",
                code="FAILED_PRECONDITION",
                details={"uri": uri},
            )
        wait_tracker = get_request_wait_tracker()
        wait_tracker.register_embedding_root(msg.telemetry_id, msg.id)
        enqueued = await service.vikingdb_manager.enqueue_embedding_msg(msg)
        if not enqueued:
            wait_tracker.mark_embedding_failed(
                msg.telemetry_id,
                msg.id,
                f"Failed to enqueue reindex vector for {uri}",
            )
            raise OpenVikingError(
                f"Failed to enqueue reindex vector for {uri}",
                code="PROCESSING_ERROR",
                details={"uri": uri, "level": int(level)},
            )

    async def _fetch_existing_record(
        self,
        *,
        uri: str,
        level: int,
        ctx: RequestContext,
    ) -> Optional[dict[str, Any]]:
        service = get_service()
        assert service.vikingdb_manager is not None
        records = await service.vikingdb_manager.get_context_by_uri(
            uri=uri,
            level=level,
            limit=1,
            ctx=ctx,
        )
        return records[0] if records else None

    async def _skill_meta(
        self,
        *,
        uri: str,
        abstract: str,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        name = uri.rstrip("/").split("/")[-1]
        return {"name": name, "description": abstract}

    def _record_abstract(self, record: Optional[dict[str, Any]]) -> str:
        if not record:
            return ""
        return str(record.get("abstract") or "")

    def _is_hidden_meta_file(self, uri: str) -> bool:
        return uri.endswith("/.abstract.md") or uri.endswith("/.overview.md")

    async def _safe_read_text(self, uri: str, *, ctx: RequestContext) -> str:
        viking_fs = get_viking_fs()
        try:
            if not await viking_fs.exists(uri, ctx=ctx):
                return ""
            content = await viking_fs.read_file(uri, ctx=ctx)
            if isinstance(content, bytes):
                return content.decode("utf-8", errors="replace")
            return str(content or "")
        except Exception:
            return ""

    async def _read_memory_body(self, uri: str, *, ctx: RequestContext) -> _PruneSourceRead:
        return await self._read_prune_source(uri, ctx=ctx)

    def _chunk_memory_body(self, uri: str, body: str) -> Iterable[tuple[str, str]]:
        semantic = get_openviking_config().semantic
        chunk_chars = semantic.memory_chunk_chars
        overlap = semantic.memory_chunk_overlap
        if len(body) <= chunk_chars:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(body):
            previous_start = start
            end = start + chunk_chars
            if end < len(body):
                boundary = body.rfind("\n\n", start, end)
                if boundary > start + chunk_chars // 2:
                    end = boundary + 2
            chunks.append(body[start:end].strip())
            start = end - overlap
            if start <= previous_start:
                start = previous_start + 1
            if start >= len(body):
                break

        return [(f"{uri}#chunk_{idx:04d}", chunk) for idx, chunk in enumerate(chunks) if chunk]

    def _best_non_empty(self, *values: str) -> str:
        for value in values:
            if value:
                return value
        return ""

    def _prefer_non_empty(self, *values: str) -> str:
        for value in values:
            if value:
                return value
        return ""

    @staticmethod
    def _parse_overview_md(content: str) -> dict[str, str]:
        return parse_overview(content)
