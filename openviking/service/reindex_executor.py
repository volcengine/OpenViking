# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Admin reindex executor."""

from __future__ import annotations

import asyncio
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional

from openviking.core.context import (
    Context,
    ContextLevel,
    ContextType,
    Vectorize,
)
from openviking.core.namespace import (
    classify_uri,
    content_owner_context_for_uri,
    context_type_for_uri,
    is_hidden_by_actor_peer_view,
    is_session_uri,
    may_include_hidden_actor_peers,
    owner_space_for_uri,
    uri_parts,
)
from openviking.core.namespace_policy import namespace_policy_for_uri
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.service.task_domain import TaskPatch
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import bind_task_context, get_task_context
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.abstract_overview import (
    ABSTRACT_OVERVIEW_FILENAMES,
    body_for_preview,
    embedding_text_for_body,
)
from openviking.storage.errors import LockError
from openviking.storage.internal_names import is_storage_internal_name
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry
from openviking.utils.embedding_utils import (
    _apply_ingest_options,
    _truncate_abstract_bytes,
)
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    OpenVikingError,
    PermissionDeniedError,
)
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)
_MEMORY_CHUNK_PATTERN = re.compile(r"^(?P<base>.+)#chunk_[0-9]{4,}$")

REINDEX_TASK_TYPE = "admin_reindex"
REINDEX_ENQUEUE_ERROR = "Failed to enqueue reindex processing"
_MAX_FILE_VECTORIZATION_CONCURRENCY = 64
_USER_REINDEX_FIXED_ROOTS = ("resources", "skills", "memories", "peers")
_USER_REINDEX_EXCLUDED_ROOTS = frozenset({"sessions", "privacy", "settings"})
_MEMORY_NON_SOURCE_FILENAMES = ABSTRACT_OVERVIEW_FILENAMES | {
    ".relations.json",
    ".ovgitignore",
}


# Trailing markers VikingFS appends when a directory has no generated .abstract.md/.overview.md
# (see openviking/storage/viking_fs.py). The rendered value is a placeholder, not semantic
# content, and must never be embedded as an ABSTRACT (L0) / OVERVIEW (L1) vector (issue #2434).
_ABSTRACT_NOT_READY_SUFFIX = "[Directory abstract is not ready]"
_OVERVIEW_NOT_READY_SUFFIX = "[Directory overview is not ready]"


def _is_not_ready_sentinel(text: str, suffix: str) -> bool:
    """Return True if *text* is a VikingFS not-ready directory placeholder.

    VikingFS renders these as a single ``# <uri>`` header followed only by the not-ready marker.
    Match that exact shape (a ``#`` header with no substantive body before the trailing marker)
    so the check is uri-agnostic yet never drops real directory content that merely ends with,
    or mentions, the user-facing marker phrase.
    """
    if not text:
        return False
    head = text.rstrip()
    if not head.endswith(suffix):
        return False
    head = head[: -len(suffix)].strip()
    return head.startswith("#") and "\n" not in head


@dataclass
class _ReindexCounters:
    scanned_records: int = 0
    rebuilt_records: int = 0
    deleted_records: int = 0
    unsupported_records: int = 0
    failed_records: int = 0
    warnings: list[str] = field(default_factory=list)

    def merge_from(self, other: "_ReindexCounters") -> None:
        self.scanned_records += other.scanned_records
        self.rebuilt_records += other.rebuilt_records
        self.deleted_records += other.deleted_records
        self.unsupported_records += other.unsupported_records
        self.failed_records += other.failed_records
        self.warnings.extend(other.warnings)


@dataclass
class _ReindexRunContext:
    ctx: RequestContext
    counters: _ReindexCounters
    lock: dict | None = None
    lease: dict | None = None
    lease_guard: PathLockGuard | None = None
    lock_root_uri: str | None = None
    ingest_options: IngestOptions | None = None
    force: bool = False
    root_is_dir: bool | None = None


@dataclass
class _SourceRead:
    exists: bool
    text: str = ""
    error: Exception | None = None


class ReindexExecutor:
    """Reindex convergence orchestration for admin maintenance flows."""

    _NAMESPACE_LS_PAGE_SIZE = 1000

    SUPPORTED_MODES_BY_TYPE = {
        "global_namespace": {"vectors_only", "semantic_and_vectors"},
        "user_namespace": {"vectors_only", "semantic_and_vectors"},
        "skill_namespace": {"vectors_only", "semantic_and_vectors"},
        "resource": {"vectors_only", "semantic_and_vectors"},
        "skill": {"vectors_only", "semantic_and_vectors"},
        "memory": {"vectors_only", "semantic_and_vectors"},
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
            tasks = [asyncio.create_task(processor(item)) for item in batch]
            batch_result = asyncio.gather(*tasks)
            try:
                results = await asyncio.shield(batch_result)
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await run_to_completion(
                    lambda batch_result=batch_result, tasks=tasks: asyncio.gather(
                        batch_result, *tasks, return_exceptions=True
                    )
                )
                raise
            for item_counters in results:
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
        force: bool = False,
        recursive: bool = True,
        tags: list[str] | None = None,
        tag_mode: str = "replace",
        ctx: RequestContext,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Run or enqueue reindex; task_id resumes an internal async submission.

        A replay reuses accepted work. A pending record without queued work
        resumes enqueueing under the same identity.
        """
        if not namespace_policy_for_uri(uri).supports_reindex:
            raise InvalidArgumentError(f"reindex is not supported for namespace: {uri}")
        self._ensure_reconciliation_scope_visible(uri, ctx)
        if task_id is not None and wait:
            raise InvalidArgumentError("task_id requires asynchronous reindex")
        object_type = self._infer_target_type(uri)
        self._validate_mode(object_type, mode)
        if not recursive and object_type in {
            "global_namespace",
            "user_namespace",
            "skill_namespace",
        }:
            raise InvalidArgumentError(
                f"recursive=false is not supported for namespace reindex target {uri}"
            )
        self._resolve_ingest_options(
            mode=mode,
            tags=tags,
            tag_mode=tag_mode,
        )

        tracker = get_task_tracker()
        task = await tracker.create_if_no_running(
            REINDEX_TASK_TYPE,
            uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            **({"task_id": task_id} if task_id is not None else {}),
        )
        if task is None:
            raise OpenVikingError(
                f"URI {uri} already has a reindex in progress",
                code="CONFLICT",
                details={"uri": uri},
            )

        if task_id is not None and task.error == REINDEX_ENQUEUE_ERROR:
            raise OpenVikingError(REINDEX_ENQUEUE_ERROR, code="UNAVAILABLE")
        if task_id is not None and (
            task.stage == "queued"
            or task.status != "pending"
            or await tracker.has_work(
                task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
            )
        ):
            return {
                "task_id": task.task_id,
                "status": "accepted",
                "uri": uri,
                "object_type": object_type,
                "mode": mode,
            }

        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.reindex_msg import ReindexMsg

        service = None
        lock_guard = None
        enqueued = False
        try:
            service = get_service()
            path = service.viking_fs._uri_to_path(uri, ctx=ctx)
            stat = await service.viking_fs.stat(uri, ctx=ctx, skip_count=True)
            acquire = (
                service.viking_fs._async_agfs.pathlock_acquire_tree
                if stat.get("isDir", stat.get("is_dir"))
                else service.viking_fs._async_agfs.pathlock_acquire_exact
            )
            lease = await acquire(path)
            lock_guard = PathLockGuard(service.viking_fs._async_agfs, lease)
            handoff = await lock_guard.to_handoff()
            msg = ReindexMsg(
                task_id=task.task_id,
                uri=uri,
                object_type=object_type,
                mode=mode,
                force=force,
                recursive=recursive,
                tags=tags,
                tag_mode=tag_mode,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                group_ids=list(ctx.group_ids),
                role=str(ctx.role),
                actor_peer_id=ctx.actor_peer_id,
                bypass_acl=ctx.bypass_acl,
                telemetry_id=get_current_telemetry().telemetry_id or None,
                lock_handoff=handoff,
            )
            queue_manager = get_queue_manager()

            async def dispatch() -> str:
                nonlocal enqueued
                with bind_task_context(task.task_id, ctx.account_id, ctx.user.user_id):
                    result = await queue_manager.enqueue(queue_manager.REINDEX, msg.to_dict())
                enqueued = True
                return result

            await lock_guard.handoff_and_dispatch(handoff, dispatch)
            await tracker.patch_task(
                task.task_id,
                TaskPatch(stage="queued"),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        except BaseException:
            if lock_guard is not None:
                await lock_guard.close()
            if not enqueued and not await tracker.has_work(
                task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
            ):
                await tracker.fail(
                    task.task_id,
                    REINDEX_ENQUEUE_ERROR,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
            raise
        if wait:
            terminal = await tracker.wait(
                task.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            result = deepcopy(terminal.result or {})
            if terminal.status == TaskStatus.FAILED and not self._accept_partial_result(result):
                raise OpenVikingError(
                    terminal.error or "Task processing failed",
                    code=result.get("code") or "PROCESSING_ERROR",
                    details={"task_id": task.task_id, **result.get("details", {})},
                )
            if terminal.status == TaskStatus.CANCELLED:
                raise OpenVikingError(
                    "Task processing was cancelled",
                    code="PROCESSING_ERROR",
                    details={"task_id": task.task_id, "status": "cancelled"},
                )
            result.pop("task_id", None)
            return result
        return {
            "task_id": task.task_id,
            "status": "accepted",
            "uri": uri,
            "object_type": object_type,
            "mode": mode,
        }

    async def execute_in_current_task(
        self,
        *,
        uri: str,
        mode: str,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        """Run reindex work under an already-bound task.

        This is for composite operations such as snapshot restore. Public
        reindex requests must use execute(), which owns admin task creation and
        URI-level duplicate detection.
        """
        if get_task_context() is None:
            raise RuntimeError("TaskContext is required for nested reindex execution")
        object_type = self._infer_target_type(uri)
        self._validate_mode(object_type, mode)
        return await self._run(
            uri=uri,
            object_type=object_type,
            mode=mode,
            ctx=ctx,
        )

    @staticmethod
    def _accept_partial_result(result: Any) -> bool:
        """Keep record-level reindex failures in the structured result."""
        return isinstance(result, dict) and int(result.get("failed_records", 0) or 0) > 0

    @staticmethod
    def _resolve_ingest_options(
        *,
        mode: str,
        tags: list[str] | None,
        tag_mode: str,
    ) -> IngestOptions | None:
        if tags is None and tag_mode != "clear":
            return None
        if tag_mode not in {"replace", "append", "clear"}:
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
        if parts[0] in {"resources", "user"} or (parts[0] == "agent" and len(parts) >= 2):
            return "resource"
        raise OpenVikingError(
            f"Unsupported reindex URI: {uri}",
            code="UNSUPPORTED_URI",
            details={"uri": uri},
        )

    @staticmethod
    def _ensure_reconciliation_scope_visible(uri: str, ctx: RequestContext) -> None:
        if not ctx.actor_peer_id:
            return
        # Reconciliation also probes F under the content owner's identity.
        scope_ctx = content_owner_context_for_uri(uri, ctx)
        if (
            uri.rstrip("/") in {"viking:", "viking://user"}
            or may_include_hidden_actor_peers(uri, scope_ctx)
            or is_hidden_by_actor_peer_view(uri, scope_ctx)
        ):
            raise PermissionDeniedError(
                "Reindex cannot reconcile a scope containing hidden actor peers; "
                "request an explicit visible content scope",
                resource=uri,
            )

    async def _tree_all(
        self,
        viking_fs: Any,
        uri: str,
        *,
        show_all_hidden: bool,
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        entries = await viking_fs.tree(
            uri,
            output="original",
            show_all_hidden=show_all_hidden,
            node_limit=None,
            level_limit=None,
            ctx=ctx,
        )
        root_parts = uri_parts(uri)
        if any(
            entry.get("access") == "denied"
            or uri_parts(str(entry.get("uri") or ""))[: len(root_parts)] != root_parts
            for entry in entries
        ):
            raise ValueError("cannot reindex from an incomplete formal tree")
        return entries

    async def _list_scope_children(
        self,
        uri: str,
        *,
        ctx: RequestContext,
        show_all_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        entries, seen = [], set()
        offset = 0
        root_parts = uri_parts(uri)
        while True:
            page = await get_viking_fs().ls(
                uri,
                output="original",
                include_abstract=False,
                include_overview=False,
                show_all_hidden=show_all_hidden,
                node_limit=self._NAMESPACE_LS_PAGE_SIZE,
                offset=offset,
                ctx=ctx,
            )
            for entry in page:
                entry_uri = str(entry.get("uri") or "")
                parts = uri_parts(entry_uri)
                if entry.get("access") == "denied" or entry_uri in seen or parts[:-1] != root_parts:
                    raise ValueError("cannot discover reindex roots from an incomplete listing")
                seen.add(entry_uri)
                entries.append(entry)
            if len(page) < self._NAMESPACE_LS_PAGE_SIZE:
                return entries
            offset += len(page)

    async def _namespace_facts(
        self,
        uri: str,
        *,
        ctx: RequestContext,
        list_children: bool = False,
        show_all_hidden: bool = False,
    ) -> tuple[bool, bool, list[dict[str, Any]]]:
        try:
            stat = await get_viking_fs().stat(uri, ctx=ctx, skip_count=True)
        except NotFoundError:
            return False, True, []
        if stat.get("access") == "denied":
            raise ValueError("cannot reindex an unreadable formal root")
        is_dir = bool(stat.get("isDir", stat.get("is_dir")))
        entries = []
        if is_dir and list_children:
            try:
                entries = await self._list_scope_children(
                    uri,
                    ctx=ctx,
                    show_all_hidden=show_all_hidden,
                )
            except NotFoundError:
                exists, _, _ = await self._namespace_facts(uri, ctx=ctx)
                if exists:
                    raise
                return False, True, []
        return True, is_dir, entries

    @staticmethod
    def _child_inventories(
        root: str,
        inventory: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Mapping[str, Any]]]:
        root_parts = uri_parts(root)
        groups: dict[str, dict[str, Mapping[str, Any]]] = {}
        for record_id, record in inventory.items():
            parts = uri_parts(str(record.get("uri") or ""))
            if parts[: len(root_parts)] != root_parts:
                raise ValueError("reindex inventory is outside its namespace")
            if len(parts) > len(root_parts):
                child = root + "/" + parts[len(root_parts)]
                groups.setdefault(child, {})[record_id] = record
        return groups

    def _custom_user_scopes(
        self,
        user_uri: str,
        children: list[dict[str, Any]],
        direct_inventory: Mapping[str, Mapping[str, Any]],
    ) -> list[tuple[str, bool, bool, dict[str, Mapping[str, Any]]]]:
        children_by_uri = {str(entry["uri"]): entry for entry in children}
        groups = self._child_inventories(user_uri, direct_inventory)
        scopes = []
        for root in sorted(set(children_by_uri) | set(groups)):
            name = uri_parts(root)[-1]
            if (
                name in _USER_REINDEX_FIXED_ROOTS
                or name in _USER_REINDEX_EXCLUDED_ROOTS
                or is_storage_internal_name(name)
                or self._is_hidden_meta_file(root)
            ):
                continue
            entry = children_by_uri.get(root)
            records = groups.get(root, {})
            if entry is not None:
                is_dir = bool(entry.get("isDir", entry.get("is_dir")))
            else:
                levels = {int(record.get("level", -1)) for record in records.values()}
                if 2 in levels and levels.intersection({0, 1}):
                    raise ValueError(f"custom user scope has conflicting vector levels: {root}")
                is_dir = bool(levels.intersection({0, 1}))
            scopes.append((root, entry is not None, is_dir, records))
        return scopes

    async def _resource_scope(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
        facts: tuple[bool, bool, list[dict[str, Any]]],
        inventory: Mapping[str, Mapping[str, Any]],
    ) -> None:
        exists, is_dir, _ = facts
        if exists or inventory:
            await self._reindex_rfv(
                uri=uri,
                mode=mode,
                context_type="resource",
                recursive=True,
                run=run,
                root_is_dir=is_dir,
                root_exists=exists,
                formal_entries=None if exists else [],
                vector_inventory=inventory,
            )

    async def _skill_scopes(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
        facts: tuple[bool, bool, list[dict[str, Any]]],
        inventory: Mapping[str, Mapping[str, Any]],
    ) -> None:
        exists, is_dir, children = facts
        if exists and not is_dir:
            raise InvalidArgumentError(f"skill namespace must be a directory: {uri}")
        children_by_uri = {str(entry["uri"]): entry for entry in children}
        groups = self._child_inventories(uri, inventory)
        roots = {
            key for key, entry in children_by_uri.items() if entry.get("isDir", entry.get("is_dir"))
        }
        roots.update(groups)
        for root in sorted(roots):
            if uri_parts(root)[-1].startswith("."):
                continue
            child = children_by_uri.get(root)
            if child is not None and not child.get("isDir", child.get("is_dir")):
                raise InvalidArgumentError(f"skill package must be a directory: {root}")
            await self._reindex_rfv(
                uri=root,
                mode=mode,
                context_type="skill",
                recursive=True,
                run=run,
                root_is_dir=True,
                root_exists=child is not None,
                formal_entries=None if child is not None else [],
                vector_inventory=groups.get(root, {}),
            )

    async def _reindex_peer_scopes(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
        inventory: Mapping[str, Mapping[str, Any]],
        facts: tuple[bool, bool, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._ensure_reconciliation_scope_visible(uri, run.ctx)
        if facts is None:
            facts = await self._namespace_facts(uri, ctx=run.ctx, list_children=True)
        exists, is_dir, children = facts
        if exists and not is_dir:
            raise InvalidArgumentError(f"peer namespace must be a directory: {uri}")
        peers = {str(entry["uri"]): entry for entry in children}
        groups = self._child_inventories(uri, inventory)
        roots = {key for key, entry in peers.items() if entry.get("isDir", entry.get("is_dir"))}
        roots.update(groups)
        for peer in sorted(roots):
            if uri_parts(peer)[-1].startswith("."):
                continue
            child = peers.get(peer)
            if child is not None and not child.get("isDir", child.get("is_dir")):
                raise InvalidArgumentError(f"peer root must be a directory: {peer}")
            peer_facts = (
                await self._namespace_facts(peer, ctx=run.ctx, list_children=True)
                if child is not None
                else (False, True, [])
            )
            content = {str(entry["uri"]): entry for entry in peer_facts[2]}
            content_groups = self._child_inventories(peer, groups.get(peer, {}))
            resources, memories = peer + "/resources", peer + "/memories"
            resource_entry = content.get(resources)
            resource_facts = (
                resource_entry is not None,
                bool((resource_entry or {}).get("isDir", True)),
                [],
            )
            await self._resource_scope(
                uri=resources,
                mode=mode,
                run=run,
                facts=resource_facts,
                inventory=content_groups.get(resources, {}),
            )
            memory_entry = content.get(memories)
            memory_facts = (
                await self._namespace_facts(
                    memories,
                    ctx=run.ctx,
                    list_children=True,
                    show_all_hidden=True,
                )
                if memory_entry is not None
                else (False, True, [])
            )
            if memory_entry is not None or content_groups.get(memories):
                await self._reindex_memory(
                    uri=memories,
                    mode=mode,
                    run=run,
                    root_exists=memory_facts[0],
                    root_is_dir=memory_facts[1],
                    formal_entries=memory_facts[2],
                    vector_inventory=content_groups.get(memories, {}),
                )

    async def _memory_scopes(
        self,
        *,
        uri: str,
        run: _ReindexRunContext,
        children: list[dict[str, Any]],
        inventory: Mapping[str, Mapping[str, Any]],
        reconcile_only: bool = False,
    ) -> None:
        by_uri = {
            str(entry["uri"]): entry
            for entry in children
            if entry.get("isDir", entry.get("is_dir"))
            or self._is_memory_source_uri(str(entry["uri"]))
        }
        root_parts = uri_parts(uri)
        directory_roots = {
            key for key, entry in by_uri.items() if entry.get("isDir", entry.get("is_dir"))
        }
        real_files = {
            key for key, entry in by_uri.items() if not entry.get("isDir", entry.get("is_dir"))
        }

        def scope_root(record):
            record_uri = str(record.get("uri") or "")
            if int(record.get("level", -1)) == 2 and record_uri not in real_files:
                match = _MEMORY_CHUNK_PATTERN.match(record_uri)
                if match:
                    record_uri = match.group("base")
            parts = uri_parts(record_uri)
            if parts[: len(root_parts)] != root_parts:
                raise ValueError("memory inventory is outside its namespace")
            suffix = parts[len(root_parts) :]
            return (uri + "/" + suffix[0] if suffix else None), len(suffix)

        for record in inventory.values():
            root, depth = scope_root(record)
            if (
                root
                and (depth > 1 or int(record.get("level", -1)) in {0, 1})
            ):
                directory_roots.add(root)
        groups: dict[str, dict[str, Mapping[str, Any]]] = {}
        root_inventory = {}
        for record_id, record in inventory.items():
            root, _ = scope_root(record)
            if root in directory_roots:
                groups.setdefault(root, {})[record_id] = record
            else:
                root_inventory[record_id] = record
        root_files = [
            entry
            for key, entry in by_uri.items()
            if key not in directory_roots and not entry.get("isDir", entry.get("is_dir"))
        ]
        await self._reindex_memory_vectors(
            **self._with_ingest_options(
                {
                    "uri": uri,
                    "ctx": run.ctx,
                    "counters": run.counters,
                    "formal_entries": root_files,
                    "root_is_dir": True,
                    "vector_inventory": root_inventory,
                    "lease_ref": run.lease,
                    "reconcile_only": reconcile_only,
                },
                run.ingest_options,
            )
        )
        for root in sorted(directory_roots):
            entry = by_uri.get(root)
            root_exists = entry is not None
            is_dir = bool((entry or {}).get("isDir", True))
            entries = []
            if root_exists and is_dir:
                try:
                    entries = await self._tree_all(
                        get_viking_fs(), root, show_all_hidden=True, ctx=run.ctx
                    )
                except NotFoundError:
                    root_exists, _, _ = await self._namespace_facts(root, ctx=run.ctx)
                    if root_exists:
                        raise
            await self._reindex_memory_vectors(
                **self._with_ingest_options(
                    {
                        "uri": root,
                        "ctx": run.ctx,
                        "counters": run.counters,
                        "formal_entries": entries,
                        "root_is_dir": is_dir,
                        "root_exists": root_exists,
                        "vector_inventory": groups.get(root, {}),
                        "lease_ref": run.lease,
                        "reconcile_only": reconcile_only,
                    },
                    run.ingest_options,
                )
            )

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

    @staticmethod
    def _apply_semantic_tree_stats(counters: _ReindexCounters, stats: Any | None) -> None:
        if stats is None:
            return
        counters.rebuilt_records += int(getattr(stats, "indexed_records", 0) or 0)
        failures = list(getattr(stats, "failures", ()) or ())
        counters.failed_records += len(failures)
        counters.warnings.extend(failures)

    async def _reindex_skill_namespace(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
        vector_inventory: Mapping[str, Mapping[str, Any]] | None = None,
        facts: tuple[bool, bool, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._ensure_reconciliation_scope_visible(uri, run.ctx)
        if vector_inventory is None:
            vector_inventory = await self._read_reindex_inventory(
                get_service().vikingdb_manager,
                uri=uri,
                ctx=self._content_owner_ctx(uri, run.ctx),
                recursive=True,
            )
        if facts is None:
            facts = await self._namespace_facts(uri, ctx=run.ctx, list_children=True)
        await self._skill_scopes(
            uri=uri,
            mode=mode,
            run=run,
            facts=facts,
            inventory=vector_inventory,
        )

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
        force: bool = False,
        recursive: bool = True,
        ingest_options: IngestOptions | None = None,
        ctx: RequestContext,
        lease_guard: PathLockGuard | None = None,
    ) -> dict[str, Any]:
        try:
            self._ensure_reconciliation_scope_visible(uri, ctx)
            service = get_service()
            if service.viking_fs is None or service.vikingdb_manager is None:
                raise RuntimeError("OpenVikingService not initialized")
            if not service.vikingdb_manager.has_queue_manager:
                raise OpenVikingError(
                    "Reindex requires embedding queue",
                    code="FAILED_PRECONDITION",
                    details={"uri": uri},
                )
            if lease_guard is None:
                lease_guard = PathLockGuard(service.viking_fs._async_agfs)

            path = service.viking_fs._uri_to_path(uri, ctx=ctx)
            started_at = time.perf_counter()
            counters = _ReindexCounters()
            telemetry_id = get_current_telemetry().telemetry_id
            acquire_lock = service.viking_fs._async_agfs.pathlock_acquire_tree
            stat = await service.viking_fs.stat(uri, ctx=ctx, skip_count=True)
            root_is_dir = bool(stat.get("isDir", stat.get("is_dir")))
            if not root_is_dir:
                acquire_lock = service.viking_fs._async_agfs.pathlock_acquire_exact
            lease = lease_guard.lease
            if lease is None:
                lease = await lease_guard.acquire(lambda: acquire_lock(path))
            borrowed = (
                await service.viking_fs._async_agfs.pathlock_as_borrowed(lease)
                if lease is not None
                else None
            )
            run = _ReindexRunContext(
                ctx=ctx,
                counters=counters,
                lock=borrowed,
                lease=lease,
                lease_guard=lease_guard,
                ingest_options=ingest_options,
                force=force,
                root_is_dir=root_is_dir,
                lock_root_uri=uri.rstrip("/"),
            )
            if object_type == "global_namespace":
                namespace_kwargs = {"uri": uri, "mode": mode, "run": run}
                if not recursive:
                    namespace_kwargs["recursive"] = False
                await self._reindex_global_namespace(**namespace_kwargs)
            elif object_type == "user_namespace":
                namespace_kwargs = {"uri": uri, "mode": mode, "run": run}
                if not recursive:
                    namespace_kwargs["recursive"] = False
                await self._reindex_user_namespace(**namespace_kwargs)
            elif object_type == "skill_namespace":
                namespace_kwargs = {"uri": uri, "mode": mode, "run": run}
                if not recursive:
                    namespace_kwargs["recursive"] = False
                await self._reindex_skill_namespace(**namespace_kwargs)
            elif object_type == "resource":
                resource_kwargs = {"uri": uri, "mode": mode, "run": run}
                if not recursive:
                    resource_kwargs["recursive"] = False
                await self._reindex_resource(**resource_kwargs)
            elif object_type == "skill":
                skill_kwargs = {"uri": uri, "mode": mode, "run": run}
                if not recursive:
                    skill_kwargs["recursive"] = False
                await self._reindex_skill(**skill_kwargs)
            elif object_type == "memory":
                memory_kwargs = {"uri": uri, "mode": mode, "run": run}
                if not recursive:
                    memory_kwargs["recursive"] = False
                await self._reindex_memory(**memory_kwargs)
            else:
                raise OpenVikingError(
                    f"Unsupported reindex type: {object_type}",
                    code="UNSUPPORTED_URI",
                    details={"uri": uri},
                )

            task_context = get_task_context()
            if task_context is not None:
                await get_task_tracker().wait_for_task_work(
                    task_context.task_id, exclude_work_id=task_context.work_id
                )
                self._apply_semantic_tree_stats(
                    counters,
                    SemanticProcessor.consume_tree_stats(telemetry_id=telemetry_id),
                )
                self._apply_embedding_wait_status(
                    counters,
                    await get_task_tracker().build_queue_status(task_context.task_id),
                )
        finally:
            if lease_guard is not None:
                await lease_guard.close()

        return {
            "status": "completed",
            "uri": uri,
            "object_type": object_type,
            "mode": mode,
            "scanned_records": counters.scanned_records,
            "rebuilt_records": counters.rebuilt_records,
            "deleted_records": counters.deleted_records,
            "unsupported_records": counters.unsupported_records,
            "failed_records": counters.failed_records,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
            "warnings": counters.warnings,
        }

    async def _reindex_resource(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
        recursive: bool = True,
    ) -> None:
        await self._reindex_rfv(
            uri=uri,
            mode=mode,
            context_type="resource",
            recursive=recursive,
            run=run,
        )

    async def _reindex_rfv(
        self,
        *,
        uri: str,
        mode: str,
        context_type: str,
        recursive: bool,
        run: _ReindexRunContext,
        root_is_dir: bool | None = None,
        formal_entries: list[dict[str, Any]] | None = None,
        root_exists: bool = True,
        vector_inventory: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        from openviking.storage.context_update_plan import build_rfv_context_update_plan
        from openviking.storage.resource_rfv import build_rfv_snapshot
        from openviking.storage.resource_rnfv import RequestIntent
        from openviking.utils.resource_processor import ResourceProcessor

        service = get_service()
        request = RequestIntent.from_ingest_options(
            target_uri=uri,
            processing_mode=mode,
            ingest_options=run.ingest_options,
            # A Skill package has exactly one capability document plus its L0/L1
            # sidecars. ``vectors_only`` is expected to rebuild all three even
            # when their md5 values are unchanged (the public API regression
            # contract relies on this), while other RFV targets retain the
            # caller-controlled force behavior.
            force=run.force or (context_type == "skill" and mode == "vectors_only"),
            scalar_target_levels=frozenset({0, 1, 2}),
        )
        resolved_root_is_dir = run.root_is_dir if root_is_dir is None else root_is_dir
        if not recursive and resolved_root_is_dir is None:
            stat = await service.viking_fs.stat(uri, ctx=run.ctx, skip_count=True)
            resolved_root_is_dir = bool(stat.get("isDir", stat.get("is_dir")))
        if vector_inventory is None:
            vector_inventory = await self._read_reindex_inventory(
                service.vikingdb_manager,
                uri=uri,
                ctx=self._content_owner_ctx(uri, run.ctx),
                recursive=recursive,
                include_direct_children=bool(resolved_root_is_dir) and not recursive,
            )
        snapshot = await build_rfv_snapshot(
            viking_fs=service.viking_fs,
            vikingdb=service.vikingdb_manager,
            target_uri=uri,
            ctx=run.ctx,
            request_intent=request,
            recursive=recursive,
            source_read_concurrency=self._effective_file_vectorization_concurrency(),
            root_is_dir=resolved_root_is_dir,
            formal_inventory=(
                (True if root_is_dir is None else root_is_dir, formal_entries, True)
                if formal_entries is not None
                else None
            ),
            root_exists=root_exists,
            vector_inventory=vector_inventory,
        )
        diff, plan = build_rfv_context_update_plan(
            snapshot=snapshot,
            context_type=context_type,
            account_id=run.ctx.account_id,
            source_metadata=snapshot.source_metadata,
        )
        root_entry = snapshot.formal.entries.get("")
        if (
            mode == "vectors_only"
            and root_entry is not None
            and not root_entry.is_dir
            and any(
                action.level == int(ContextLevel.DETAIL)
                and action.action.value in {"merge", "upsert"}
                for action in plan.direct_index_actions
            )
        ):
            enriched_snapshot = await self._with_single_file_parent_overview(
                snapshot,
                ctx=run.ctx,
                viking_fs=service.viking_fs,
            )
            if enriched_snapshot is not snapshot:
                snapshot = enriched_snapshot
                diff, plan = build_rfv_context_update_plan(
                    snapshot=snapshot,
                    context_type=context_type,
                    account_id=run.ctx.account_id,
                    source_metadata=snapshot.source_metadata,
                )
        run.counters.scanned_records += len(snapshot.formal.entries)
        if plan.direct_index_actions:
            owner_ctx = self._content_owner_ctx(uri, run.ctx)
            delete_actions = tuple(
                action for action in plan.direct_index_actions if action.action.value == "delete"
            )
            enqueue_actions = tuple(
                action for action in plan.direct_index_actions if action.action.value != "delete"
            )
            if delete_actions:
                run.counters.deleted_records += await service.vikingdb_manager.strict_delete(
                    [action.record_id for action in delete_actions],
                    ctx=owner_ctx,
                )
            if enqueue_actions:
                processor = getattr(service, "_resource_processor", None)
                if processor is None:
                    processor = ResourceProcessor(service.vikingdb_manager)
                run.counters.rebuilt_records += await processor._enqueue_index_actions(
                    enqueue_actions,
                    ctx=owner_ctx,
                    source_contents=dict(snapshot.source_contents),
                    source_metadata=snapshot.source_metadata,
                )
        if plan.semantic_plan is not None:
            await self._enqueue_reindex_semantic_plan(
                semantic_plan=plan.semantic_plan,
                context_type=context_type,
                recursive=recursive,
                run=run,
            )
        elif plan.file_refresh is not None:
            service_processor = getattr(service, "_resource_processor", None)
            if service_processor is None:
                service_processor = ResourceProcessor(service.vikingdb_manager)
            await service_processor._get_summarizer().refresh_file_parent(
                file_uri=plan.file_refresh.file_uri,
                ctx=run.ctx,
                ingest_options=run.ingest_options,
                file_md5=plan.file_refresh.md5,
            )

    @staticmethod
    async def _with_single_file_parent_overview(
        snapshot: Any,
        *,
        ctx: RequestContext,
        viking_fs: Any,
    ) -> Any:
        """Best-effort hydrate a file root's parent overview for L2 summary reuse."""
        parent = VikingURI(snapshot.request.target_uri).parent
        if parent is None:
            return snapshot
        try:
            raw = await viking_fs.read_file_bytes(f"{parent.uri}/.overview.md", ctx=ctx)
            overview = body_for_preview(raw)
        except Exception:
            return snapshot
        if not overview.strip():
            return snapshot
        return replace(
            snapshot,
            source_contents={
                **snapshot.source_contents,
                (parent.uri, int(ContextLevel.OVERVIEW)): overview,
            },
        )

    async def _enqueue_reindex_semantic_plan(
        self,
        *,
        semantic_plan: Any,
        context_type: str,
        recursive: bool,
        run: _ReindexRunContext,
    ) -> None:
        """Persist one fully planned reindex DAG without persisting its source bytes."""
        owner_ctx = self._content_owner_ctx(semantic_plan.root_uri, run.ctx)
        telemetry_id = get_current_telemetry().telemetry_id
        msg = SemanticMsg(
            uri=semantic_plan.root_uri,
            context_type=context_type,
            recursive=recursive,
            account_id=owner_ctx.account_id,
            user_id=owner_ctx.user.user_id,
            group_ids=list(owner_ctx.group_ids),
            peer_id=owner_ctx.user.user_id,
            role=str(owner_ctx.role),
            telemetry_id=telemetry_id,
            generation_trigger="reindex",
            propagate_to_parent=recursive,
            plan=semantic_plan,
        )
        root_entry = next(entry for entry in semantic_plan.tree.entries if not entry.relative_path)
        await self._enqueue_reindex_semantic_message(
            msg=msg,
            root_is_dir=root_entry.kind == "directory",
            run=run,
        )

    async def _enqueue_reindex_semantic_message(
        self,
        *,
        msg: SemanticMsg,
        root_is_dir: bool,
        run: _ReindexRunContext,
    ) -> None:
        """Queue shared semantic/vector work with the current reindex lock."""
        from openviking.storage.queuefs import get_queue_manager

        queue_manager = get_queue_manager()
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)

        async def enqueue():
            if msg.context_type == "memory":
                return await semantic_queue.enqueue(msg, deduplicate_memory=False)
            return await semantic_queue.enqueue(msg)

        if run.lease is None:
            await enqueue()
            return

        viking_fs = get_service().viking_fs
        agfs = viking_fs._async_agfs
        use_outer_lease = run.lock_root_uri in {None, msg.uri.rstrip("/")}
        plan_lease = run.lease
        acquired_plan_lease = False
        if not use_outer_lease:
            acquire = agfs.pathlock_acquire_tree if root_is_dir else agfs.pathlock_acquire_exact
            kwargs = {"owner_lease_ref": run.lease} if run.lease is not None else {}
            plan_lease = await acquire(viking_fs._uri_to_path(msg.uri, ctx=run.ctx), **kwargs)
            acquired_plan_lease = True
        assert plan_lease is not None
        plan_guard = (
            run.lease_guard
            if not acquired_plan_lease and run.lease_guard is not None
            else PathLockGuard(agfs, plan_lease)
        )
        handoff = await plan_guard.to_handoff()
        msg.lock_handoff = handoff
        try:
            await plan_guard.handoff_and_dispatch(handoff, enqueue)
            if not acquired_plan_lease:
                run.lease = None
                run.lock = None
        except BaseException:
            if acquired_plan_lease:
                await plan_guard.close()
            elif plan_guard.lease is not None:
                run.lease = plan_guard.lease
                run.lock = await agfs.pathlock_as_borrowed(run.lease)
            raise

    async def _reindex_skill(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
        recursive: bool = True,
        formal_entries: list[dict[str, Any]] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "uri": uri,
            "mode": mode,
            "context_type": "skill",
            "recursive": recursive,
            "run": run,
        }
        if formal_entries is not None:
            kwargs["root_is_dir"] = True
            kwargs["formal_entries"] = formal_entries
        await self._reindex_rfv(**kwargs)

    async def _reindex_memory(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
        recursive: bool = True,
        vector_inventory: Mapping[str, Mapping[str, Any]] | None = None,
        root_exists: bool = True,
        root_is_dir: bool | None = None,
        formal_entries: list[dict[str, Any]] | None = None,
    ) -> None:
        self._ensure_reconciliation_scope_visible(uri, run.ctx)
        if root_exists and root_is_dir is None:
            stat = await get_viking_fs().stat(uri, ctx=run.ctx, skip_count=True)
            root_is_dir = bool(stat.get("isDir", stat.get("is_dir")))
        if vector_inventory is None:
            vector_inventory = await self._read_reindex_inventory(
                get_service().vikingdb_manager,
                uri=uri,
                ctx=self._content_owner_ctx(uri, run.ctx),
                recursive=(bool(root_is_dir) and recursive) or not root_exists,
                include_virtual_chunks=root_exists and not root_is_dir,
            )
        shard_root = root_exists and root_is_dir and recursive and classify_uri(uri).is_memory_root
        children = formal_entries
        if shard_root and children is None:
            children = await self._list_scope_children(
                uri,
                ctx=run.ctx,
                show_all_hidden=True,
            )
        queue_semantics = mode == "semantic_and_vectors" and root_exists and root_is_dir
        if shard_root:
            await self._memory_scopes(
                uri=uri,
                run=run,
                children=children,
                inventory=vector_inventory,
                reconcile_only=queue_semantics,
            )
        else:
            await self._reindex_memory_vectors(
                **self._with_ingest_options(
                    {
                        "uri": uri,
                        "counters": run.counters,
                        "ctx": run.ctx,
                        "recursive": recursive,
                        "vector_inventory": vector_inventory,
                        "root_exists": root_exists,
                        "root_is_dir": root_is_dir,
                        "formal_entries": formal_entries,
                        "lease_ref": run.lease,
                        "reconcile_only": queue_semantics,
                    },
                    run.ingest_options,
                )
            )
        if queue_semantics:
            owner_ctx = self._content_owner_ctx(uri, run.ctx)
            await self._enqueue_reindex_semantic_message(
                msg=SemanticMsg(
                    uri=uri,
                    context_type="memory",
                    recursive=recursive,
                    account_id=owner_ctx.account_id,
                    user_id=owner_ctx.user.user_id,
                    group_ids=list(owner_ctx.group_ids),
                    peer_id=owner_ctx.user.user_id,
                    role=str(owner_ctx.role),
                    telemetry_id=get_current_telemetry().telemetry_id,
                    ingest_options=run.ingest_options,
                    generation_trigger="reindex",
                    use_hierarchical_aggregation=True,
                    propagate_to_parent=recursive,
                ),
                root_is_dir=True,
                run=run,
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

    async def delete_uri_level(self, *, uri: str, level: ContextLevel, ctx: RequestContext) -> int:
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
        recursive: bool = True,
    ) -> None:
        self._ensure_reconciliation_scope_visible(uri, run.ctx)
        if not recursive:
            return
        uri = uri.rstrip("/")
        if uri == "viking://user":
            children = await self._list_scope_children(uri, ctx=run.ctx)
            for entry in children:
                child = str(entry["uri"])
                if (
                    entry.get("isDir", entry.get("is_dir"))
                    and classify_uri(child).is_user_namespace_root
                ):
                    await self._reindex_user_namespace(uri=child, mode=mode, run=run)
            return
        children = await self._list_scope_children(uri, ctx=run.ctx, show_all_hidden=True)
        roots = {name: uri + "/" + name for name in _USER_REINDEX_FIXED_ROOTS}
        vikingdb = get_service().vikingdb_manager
        inventories = {}
        for name, root in roots.items():
            inventories[name] = await self._read_reindex_inventory(
                vikingdb,
                uri=root,
                ctx=self._content_owner_ctx(root, run.ctx),
                recursive=True,
            )
        direct_inventory = await self._read_reindex_inventory(
            vikingdb,
            uri=uri,
            ctx=self._content_owner_ctx(uri, run.ctx),
            recursive=False,
            include_direct_children=True,
        )
        custom_scopes = []
        for root, root_exists, root_is_dir, inventory in self._custom_user_scopes(
            uri, children, direct_inventory
        ):
            if root_is_dir:
                inventory = await self._read_reindex_inventory(
                    vikingdb,
                    uri=root,
                    ctx=self._content_owner_ctx(root, run.ctx),
                    recursive=True,
                )
            custom_scopes.append((root, root_exists, root_is_dir, inventory))
        facts = {}
        for name, root in roots.items():
            facts[name] = await self._namespace_facts(
                root,
                ctx=run.ctx,
                list_children=name != "resources",
                show_all_hidden=name == "memories",
            )
        for name in ("skills", "peers"):
            if facts[name][0] and not facts[name][1]:
                raise InvalidArgumentError(f"{name} namespace must be a directory: {roots[name]}")
        await self._resource_scope(
            uri=roots["resources"],
            mode=mode,
            run=run,
            facts=facts["resources"],
            inventory=inventories["resources"],
        )
        await self._reindex_skill_namespace(
            uri=roots["skills"],
            mode=mode,
            run=run,
            facts=facts["skills"],
            vector_inventory=inventories["skills"],
        )
        memory_facts = facts["memories"]
        if memory_facts[0] or inventories["memories"]:
            await self._reindex_memory(
                uri=roots["memories"],
                mode=mode,
                run=run,
                root_exists=memory_facts[0],
                root_is_dir=memory_facts[1],
                formal_entries=memory_facts[2],
                vector_inventory=inventories["memories"],
            )
        await self._reindex_peer_scopes(
            uri=roots["peers"],
            mode=mode,
            run=run,
            facts=facts["peers"],
            inventory=inventories["peers"],
        )
        for root, root_exists, root_is_dir, inventory in custom_scopes:
            await self._reindex_rfv(
                uri=root,
                mode=mode,
                context_type="resource",
                recursive=root_is_dir,
                run=run,
                root_is_dir=root_is_dir,
                root_exists=root_exists,
                formal_entries=None if root_exists else [],
                vector_inventory=inventory,
            )

    async def _reindex_global_namespace(
        self,
        *,
        uri: str,
        mode: str,
        run: _ReindexRunContext,
        recursive: bool = True,
    ) -> None:
        self._ensure_reconciliation_scope_visible(uri, run.ctx)
        if not recursive:
            return
        resources, skills = "viking://resources", "viking://agent/skills"
        inventories = {}
        for root in (resources, skills):
            inventories[root] = await self._read_reindex_inventory(
                get_service().vikingdb_manager,
                uri=root,
                ctx=run.ctx,
                recursive=True,
            )
        resource_facts = await self._namespace_facts(resources, ctx=run.ctx)
        skill_facts = await self._namespace_facts(skills, ctx=run.ctx, list_children=True)
        _, _, users = await self._namespace_facts(
            "viking://user",
            ctx=run.ctx,
            list_children=True,
        )
        await self._resource_scope(
            uri=resources,
            mode=mode,
            run=run,
            facts=resource_facts,
            inventory=inventories[resources],
        )
        await self._reindex_skill_namespace(
            uri=skills,
            mode=mode,
            run=run,
            facts=skill_facts,
            vector_inventory=inventories[skills],
        )
        for entry in users:
            child = str(entry["uri"])
            if (
                entry.get("isDir", entry.get("is_dir"))
                and classify_uri(child).is_user_namespace_root
            ):
                await self._reindex_user_namespace(uri=child, mode=mode, run=run)

    async def _reindex_memory_vectors(
        self,
        *,
        uri: str,
        counters: _ReindexCounters,
        ctx: RequestContext,
        recursive: bool = True,
        ingest_options: IngestOptions | None = None,
        vector_inventory: Optional[Mapping[str, Mapping[str, Any]]] = None,
        formal_entries: list[dict[str, Any]] | None = None,
        root_is_dir: bool | None = None,
        root_exists: bool = True,
        lease_ref: dict[str, Any] | None = None,
        reconcile_only: bool = False,
    ) -> None:
        viking_fs = get_viking_fs()
        if not root_exists:
            service = get_service()
            inventory = vector_inventory
            if inventory is None:
                inventory = await self._read_reindex_inventory(
                    service.vikingdb_manager,
                    uri=uri,
                    ctx=self._content_owner_ctx(uri, ctx),
                    recursive=True,
                )
            root_parts = uri_parts(uri)
            ids = []
            for record_id, record in inventory.items():
                record_uri = str(record.get("uri") or "")
                if uri_parts(record_uri)[: len(root_parts)] != root_parts:
                    raise ValueError("memory inventory is outside the missing root")
                if classify_uri(record_uri).is_memory:
                    ids.append(str(record_id))
            if ids:
                counters.deleted_records += await service.vikingdb_manager.strict_delete(
                    ids,
                    ctx=self._content_owner_ctx(uri, ctx),
                )
            return
        directory_uris: set[str] = set()
        if formal_entries is not None:
            root_parts = uri_parts(uri)
            if any(
                entry.get("access") == "denied"
                or uri_parts(str(entry.get("uri") or ""))[: len(root_parts)] != root_parts
                for entry in formal_entries
            ):
                raise ValueError("cannot reindex from an incomplete formal tree")
            root_is_dir = True if root_is_dir is None else root_is_dir
            entries = formal_entries
        elif await viking_fs.exists(uri, ctx=ctx):
            stat = await viking_fs.stat(uri, ctx=ctx, skip_count=True)
            root_is_dir = bool(stat.get("isDir", stat.get("is_dir")))
            entries = (
                await self._tree_all(viking_fs, uri, show_all_hidden=True, ctx=ctx)
                if root_is_dir and recursive
                else []
            )
        else:
            raise NotFoundError(uri, "memory")
        if root_is_dir:
            directory_uris = {uri}
            for entry in entries:
                entry_uri = entry.get("uri")
                if entry_uri and entry.get("isDir", entry.get("is_dir")):
                    directory_uris.add(entry_uri)
            file_uris = [
                entry["uri"]
                for entry in entries
                if not entry.get("isDir", entry.get("is_dir"))
                and self._is_memory_source_uri(str(entry["uri"]))
            ]
        else:
            file_uris = [uri]

        # Read the complete V inventory before mutating any directory or file so a
        # rate-limited or failed vector read never leaves partial directory writes.
        if vector_inventory is None:
            service = get_service()
            assert service.vikingdb_manager is not None
            inventory = await self._read_reindex_inventory(
                service.vikingdb_manager,
                uri=uri,
                ctx=self._content_owner_ctx(uri, ctx),
                recursive=root_is_dir and recursive,
                include_virtual_chunks=not root_is_dir,
            )
        else:
            inventory = dict(vector_inventory)

        if not root_is_dir:
            for chunk_uri in sorted(
                {str(record.get("uri") or "") for record in inventory.values()}
            ):
                if not re.fullmatch(re.escape(uri) + r"#chunk_[0-9]{4,}", chunk_uri):
                    continue
                try:
                    chunk_stat = await viking_fs.stat(chunk_uri, ctx=ctx, skip_count=True)
                except NotFoundError:
                    continue
                if chunk_stat.get("access") == "denied":
                    raise PermissionDeniedError(f"Cannot confirm memory chunk source: {chunk_uri}")
                if not chunk_stat.get("isDir", chunk_stat.get("is_dir")):
                    file_uris.append(chunk_uri)

        directory_sources: dict[tuple[str, int], _SourceRead] = {}
        if root_is_dir and directory_uris:
            if not reconcile_only:
                directory_sources = (
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
                    or {}
                )
            else:
                for directory_uri in directory_uris:
                    for level, filename in ((0, ".abstract.md"), (1, ".overview.md")):
                        directory_sources[
                            (directory_uri, level)
                        ] = await self._read_memory_directory_source(
                            directory_uri + "/" + filename, ctx=ctx
                        )
        existing_by_uri_level: dict[tuple[str, int], dict[str, Any]] = {}
        for record_id, record in sorted(inventory.items()):
            record_uri = str(record.get("uri") or "")
            try:
                level = int(record.get("level", -1))
            except (TypeError, ValueError):
                continue
            if record_uri and level in {0, 1, 2}:
                existing_by_uri_level.setdefault((record_uri, level), dict(record, id=record_id))

        overview_cache: dict[str, dict[str, str]] = {
            directory: SemanticProcessor._parse_overview_md(source.text) if source.text else {}
            for (directory, level), source in directory_sources.items()
            if level == 1
        }

        async def process_file(file_uri: str) -> _ReindexCounters:
            file_counters = _ReindexCounters(scanned_records=1)
            failure_message = f"Skipped {file_uri}: failed to read memory body"
            try:
                body_source = await self._read_memory_body(file_uri, ctx=ctx)
                if body_source.error:
                    raise body_source.error
                failure_message = f"Failed to reindex {file_uri} vector"
                body = body_source.text if body_source.exists else ""
                memory_content = MemoryFileUtils.read(body).content if body else ""
                existing = existing_by_uri_level.get((file_uri, int(ContextLevel.DETAIL)))
                abstract = self._record_abstract(existing)
                if not abstract:
                    abstract = await self._best_memory_file_summary(
                        file_uri,
                        ctx=ctx,
                        overview_cache=overview_cache,
                    )
                if not body and existing is None:
                    file_counters.unsupported_records += 1
                    file_counters.warnings.append(f"No memory source found for {file_uri}")
                    return file_counters

                parent_uri = VikingURI(file_uri).parent.uri
                detail_abstract = abstract or memory_content or body
                await self._upsert_context(
                    uri=file_uri,
                    parent_uri=parent_uri,
                    abstract=detail_abstract,
                    vector_text=body or abstract,
                    is_leaf=True,
                    context_type=ContextType.MEMORY.value,
                    level=ContextLevel.DETAIL,
                    ctx=ctx,
                    ingest_options=ingest_options,
                )
                file_counters.rebuilt_records += 1
                if not body:
                    file_counters.warnings.append(
                        f"Reindexed {file_uri} from abstract fallback because original memory body is unavailable"
                    )
            except (asyncio.CancelledError, LockError):
                raise
            except Exception as exc:
                file_counters.failed_records += 1
                file_counters.warnings.append(f"{failure_message}: {exc}")
            return file_counters

        concurrency = self._effective_file_vectorization_concurrency()
        if file_uris:
            logger.info(
                "Reindex memory file vectorization: root=%s files=%d concurrency=%d",
                uri,
                len(file_uris),
                concurrency,
            )
        if reconcile_only:
            counters.scanned_records += len(file_uris) + len(directory_uris)
        else:
            await self._run_ordered_counter_batches(
                [file_uri for file_uri in file_uris if root_is_dir or file_uri == uri],
                concurrency=concurrency,
                processor=process_file,
                counters=counters,
            )

        await self._delete_memory_orphans(
            root=uri,
            vector_inventory=inventory,
            live_file_uris=set(file_uris),
            live_directory_uris=directory_uris,
            live_directory_sources={
                key for key, source in directory_sources.items() if source.text or source.error
            },
            counters=counters,
            ctx=ctx,
            file_scope=not root_is_dir,
            lease_ref=lease_ref,
        )

    async def _delete_memory_orphans(
        self,
        *,
        root: str,
        vector_inventory: Mapping[str, Mapping[str, Any]],
        live_file_uris: set[str],
        live_directory_uris: set[str],
        live_directory_sources: set[tuple[str, int]],
        counters: _ReindexCounters,
        ctx: RequestContext,
        file_scope: bool = False,
        lease_ref: dict[str, Any] | None = None,
    ) -> None:
        """Delete memory V records whose formal source no longer exists.

        Covers plain L2 files, directory L0/L1 markers, and virtual
        legacy ``#chunk_NNNN`` records, which current memory indexing no longer produces.
        Session URIs and any record outside the memory namespace are never
        deleted here.
        """
        orphan_ids: list[str] = []
        for record_id, record in vector_inventory.items():
            record_uri = str(record.get("uri") or "")
            try:
                level = int(record.get("level", -1))
            except (TypeError, ValueError):
                continue
            if level not in {0, 1, 2}:
                continue
            if is_session_uri(record_uri) or classify_uri(record_uri).context_type != "memory":
                continue
            record_id = str(record_id)
            if not record_id:
                continue
            if level in {0, 1}:
                if (
                    record_uri not in live_directory_uris
                    or (record_uri, level) not in live_directory_sources
                ):
                    orphan_ids.append(record_id)
                continue
            # level == 2
            # An actual filename wins over the virtual-chunk suffix syntax.
            if record_uri in live_file_uris:
                continue
            orphan_ids.append(record_id)

        if not orphan_ids:
            return
        service = get_service()
        if service.vikingdb_manager is None:
            return
        owner_ctx = self._content_owner_ctx(root, ctx)
        chunk_uris = {
            str(vector_inventory[record_id].get("uri") or "")
            for record_id in orphan_ids
            if file_scope
            and re.fullmatch(
                re.escape(root) + r"#chunk_[0-9]{4,}",
                str(vector_inventory[record_id].get("uri") or ""),
            )
        }
        guard = None
        try:
            if chunk_uris:
                fs = get_viking_fs()
                guard = PathLockGuard(fs._async_agfs)
                paths = [fs._uri_to_path(uri, ctx=owner_ctx) for uri in sorted(chunk_uris)]
                kwargs = {"owner_lease_ref": lease_ref} if lease_ref is not None else {}
                await guard.acquire(
                    lambda: fs._async_agfs.pathlock_acquire_exact_batch(paths, **kwargs)
                )
                real_files = set()
                for uri in sorted(chunk_uris):
                    try:
                        stat = await fs.stat(uri, ctx=owner_ctx, skip_count=True)
                    except NotFoundError:
                        continue
                    if stat.get("access") == "denied":
                        raise PermissionDeniedError(f"Cannot confirm memory chunk source: {uri}")
                    if not stat.get("isDir", stat.get("is_dir")):
                        real_files.add(uri)
                orphan_ids = [
                    record_id
                    for record_id in orphan_ids
                    if str(vector_inventory[record_id].get("uri") or "") not in real_files
                ]
            if orphan_ids:
                counters.deleted_records += await service.vikingdb_manager.strict_delete(
                    orphan_ids, ctx=owner_ctx
                )
        finally:
            if guard is not None:
                await guard.close()

    async def _read_reindex_inventory(
        self,
        vikingdb: Any,
        *,
        uri: str,
        ctx: RequestContext,
        recursive: bool,
        include_direct_children: bool = False,
        include_virtual_chunks: bool = False,
    ) -> dict[str, dict[str, Any]]:
        retry_delays = (0.0, 0.25, 1.0)
        for attempt, delay in enumerate(retry_delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                kwargs = {"include_direct_children": True} if include_direct_children else {}
                if include_virtual_chunks:
                    kwargs["include_virtual_chunks"] = True
                return await vikingdb.get_reindex_inventory_under_uri(
                    uri,
                    ctx=ctx,
                    output_fields=[
                        "abstract",
                        "context_type",
                        "owner_user_id",
                        "name",
                        "description",
                        "tags",
                        "search_tags",
                    ],
                    recursive=recursive,
                    **kwargs,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                incomplete = isinstance(exc, RuntimeError) and any(
                    marker in str(exc)
                    for marker in (
                        "cursor ended after",
                        "ended after",
                        "records but count was",
                    )
                )
                if attempt == len(retry_delays) or not (
                    bool(getattr(exc, "retryable", False)) or incomplete
                ):
                    raise
                logger.warning(
                    "Reindex vector inventory was rate limited; retrying uri=%s attempt=%d/%d "
                    "error=%s",
                    uri,
                    attempt,
                    len(retry_delays),
                    exc,
                )
        raise AssertionError("unreachable")

    async def _best_memory_file_summary(
        self,
        uri: str,
        *,
        ctx: RequestContext,
        overview_cache: dict[str, dict[str, str]],
    ) -> str:
        parent_uri = VikingURI(uri).parent.uri
        if parent_uri not in overview_cache:
            raw = await self._safe_read_text(f"{parent_uri}/.overview.md", ctx=ctx)
            overview_cache[parent_uri] = SemanticProcessor._parse_overview_md(raw) if raw else {}
        file_name = uri.rsplit("/", 1)[-1]
        return overview_cache[parent_uri].get(file_name, "")

    async def _reindex_memory_directory_chain(
        self,
        *,
        directory_uris: Iterable[str],
        counters: _ReindexCounters,
        ctx: RequestContext,
        ingest_options: IngestOptions | None = None,
    ) -> dict[tuple[str, int], _SourceRead]:
        sources: dict[tuple[str, int], _SourceRead] = {}
        for directory_uri in directory_uris:
            counters.scanned_records += 1
            for level, filename in ((0, ".abstract.md"), (1, ".overview.md")):
                source = await self._read_memory_directory_source(
                    directory_uri + "/" + filename, ctx=ctx
                )
                sources[(directory_uri, level)] = source
                if source.error:
                    counters.failed_records += 1
                    counters.warnings.append(
                        f"Failed to read {directory_uri}/{filename}: {source.error}"
                    )
            abstract = sources[(directory_uri, 0)].text
            overview = sources[(directory_uri, 1)].text
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
                except (asyncio.CancelledError, LockError):
                    raise
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
                except (asyncio.CancelledError, LockError):
                    raise
                except Exception as exc:
                    counters.failed_records += 1
                    counters.warnings.append(f"Failed to reindex {directory_uri} L1 vector: {exc}")
        return sources

    async def _read_memory_directory_source(self, uri: str, *, ctx: RequestContext) -> _SourceRead:
        try:
            raw = await get_viking_fs().read_file(uri, ctx=ctx)
        except (asyncio.CancelledError, LockError):
            raise
        except NotFoundError:
            return _SourceRead(exists=False)
        except Exception as exc:
            return _SourceRead(exists=True, error=exc)
        try:
            text = body_for_preview(raw).strip()
            if _is_not_ready_sentinel(text, _ABSTRACT_NOT_READY_SUFFIX) or _is_not_ready_sentinel(
                text, _OVERVIEW_NOT_READY_SUFFIX
            ):
                text = ""
            return _SourceRead(exists=True, text=text)
        except Exception as exc:
            return _SourceRead(exists=True, error=exc)

    async def _read_directory_abstract(self, uri: str, *, ctx: RequestContext) -> str:
        try:
            value = await get_viking_fs().abstract(uri, ctx=ctx)
        except Exception:
            return ""
        return "" if _is_not_ready_sentinel(value, _ABSTRACT_NOT_READY_SUFFIX) else value

    async def _read_directory_overview(self, uri: str, *, ctx: RequestContext) -> str:
        try:
            value = await get_viking_fs().overview(uri, ctx=ctx)
        except Exception:
            return ""
        return "" if _is_not_ready_sentinel(value, _OVERVIEW_NOT_READY_SUFFIX) else value

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
        md5: str | None = None,
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
            md5=md5,
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
        enqueue_error = f"Failed to enqueue reindex vector for {uri}"
        enqueued = await service.vikingdb_manager.enqueue_embedding_msg(msg)
        if not enqueued:
            raise OpenVikingError(
                enqueue_error,
                code="PROCESSING_ERROR",
                details={"uri": uri, "level": int(level)},
            )

    def _record_abstract(self, record: Optional[dict[str, Any]]) -> str:
        if not record:
            return ""
        return str(record.get("abstract") or "")

    @staticmethod
    def _is_memory_source_uri(uri: str) -> bool:
        name = uri_parts(uri)[-1]
        return name not in _MEMORY_NON_SOURCE_FILENAMES and not is_storage_internal_name(name)

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

    async def _read_memory_body(self, uri: str, *, ctx: RequestContext) -> _SourceRead:
        viking_fs = get_viking_fs()
        try:
            exists = await viking_fs.exists(uri, ctx=ctx)
        except Exception as exc:
            return _SourceRead(exists=False, error=exc)
        if not exists:
            return _SourceRead(exists=False)
        try:
            content = await viking_fs.read_file(uri, ctx=ctx)
        except Exception as exc:
            return _SourceRead(exists=True, error=exc)
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = str(content or "")
        return _SourceRead(exists=True, text=text)
