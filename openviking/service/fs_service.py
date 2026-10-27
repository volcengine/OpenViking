# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
File System Service for OpenViking.

Provides file system operations: ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob.
"""

import asyncio
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Literal, Optional

from openviking.core.context import ContextLevel
from openviking.core.namespace import (
    canonical_session_root,
    classify_uri,
    context_type_for_uri,
    is_session_uri,
    uri_leaf_name,
    uri_parts,
)
from openviking.core.namespace_policy import (
    NamespaceProcessingMode,
    namespace_policy_for_uri,
)
from openviking.privacy.sanitizer import PrivacySanitizer
from openviking.privacy.service import UserPrivacyConfigService
from openviking.privacy.session_restore import get_session_id_from_uri, restore_session_content
from openviking.privacy.skill_restore import get_skill_name_from_uri, restore_skill_content
from openviking.resource.uri_mutation_coordinator import UriMutationCoordinator
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.service.task_tracker import get_task_tracker, run_tracked_operation
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import get_enqueued_work_ids, get_task_context
from openviking.session.memory.account_templates import (
    validate_account_memory_template_transfer,
)
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils.content_visibility import visible_content
from openviking.storage.abstract_overview import (
    is_abstract_overview_uri,
    mark_abstract_overview_pending,
    plan_abstract_overview_refresh,
    render_abstract_overview,
)
from openviking.storage.acl import AclAction, AclMode, AclSpec
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.expr import And, Eq, In, Or
from openviking.storage.internal_names import is_storage_internal_name
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.queuefs.semantic_msg import build_semantic_coalesce_key
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.storage.vector_ids import is_vector_record_id
from openviking.storage.viking_fs import VikingFS
from openviking.storage.vikingdb_manager import VikingDBManagerProxy
from openviking.telemetry import get_current_telemetry
from openviking.utils.embedding_utils import vectorize_directory_meta
from openviking.utils.ingest_options import IngestOptions
from openviking.utils.path_safety import validate_safe_viking_uri_path
from openviking.utils.tags import normalize_search_tags
from openviking_cli.exceptions import (
    DeadlineExceededError,
    InvalidArgumentError,
    NotFoundError,
    NotInitializedError,
    PermissionDeniedError,
    ResourceExhaustedError,
)
from openviking_cli.utils import VikingURI, get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


@dataclass
class ListingPage:
    entries: List[Any]
    has_more: bool


def _may_include_memory_content(uri: str) -> bool:
    """Return whether a public subtree read can contain memory files."""
    if is_session_uri(uri):
        return False
    classification = classify_uri(uri)
    if classification.is_memory:
        return True
    if classification.content_index is not None:
        return False
    return not classification.parts or classification.scope in {"user", "agent"}


def _visible_grep_content(content: str, uri: str) -> str:
    return visible_content(content, uri=uri)


if TYPE_CHECKING:
    from openviking.resource.watch_manager import WatchManager
    from openviking.resource.watch_scheduler import WatchScheduler
    from openviking.service.resource_memory_link_service import ResourceMemoryLinkService
    from openviking.storage import VikingDBManager


class FSService:
    """File system operations service."""

    def __init__(
        self,
        viking_fs: Optional[VikingFS] = None,
        vikingdb: Optional["VikingDBManager"] = None,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
        watch_scheduler: Optional["WatchScheduler"] = None,
        uri_mutation_coordinator: Optional[UriMutationCoordinator] = None,
        privacy_sanitizer: Optional[PrivacySanitizer] = None,
    ):
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb
        self._privacy_config_service = privacy_config_service
        self._privacy_sanitizer = privacy_sanitizer or (
            PrivacySanitizer(privacy_config_service) if privacy_config_service is not None else None
        )
        self._resource_memory_link_service = resource_memory_link_service
        self._watch_scheduler = watch_scheduler
        self._uri_mutation_coordinator = uri_mutation_coordinator or UriMutationCoordinator()
        # Installed by OpenVikingService; records Compile output versions after writes.
        self.compile_versions: Optional[Any] = None

    def set_dependencies(
        self,
        viking_fs: VikingFS,
        vikingdb: Optional["VikingDBManager"] = None,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
        watch_scheduler: Optional["WatchScheduler"] = None,
        uri_mutation_coordinator: Optional[UriMutationCoordinator] = None,
        privacy_sanitizer: Optional[PrivacySanitizer] = None,
    ) -> None:
        """Set service dependencies (for deferred initialization)."""
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb
        self._privacy_config_service = privacy_config_service
        self._privacy_sanitizer = privacy_sanitizer or (
            PrivacySanitizer(privacy_config_service) if privacy_config_service is not None else None
        )
        self._resource_memory_link_service = resource_memory_link_service
        self._watch_scheduler = watch_scheduler
        if uri_mutation_coordinator is not None:
            self._uri_mutation_coordinator = uri_mutation_coordinator

    def _ensure_initialized(self) -> VikingFS:
        """Ensure VikingFS is initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")
        return self._viking_fs

    async def _resolve_uri(self, uri_or_id: str, ctx: RequestContext) -> str:
        """If ``uri_or_id`` is a 32-char hex vector record id, resolve it to the
        corresponding Viking URI via the vector store. Otherwise return as-is.
        Used so that service-layer helpers (classify_uri, get_skill_name_from_uri,
        visible_content) always see a real URI, even when callers pass an id.
        """
        if not is_vector_record_id(uri_or_id):
            return uri_or_id
        viking_fs = self._ensure_initialized()
        return await viking_fs.resolve_uri(uri_or_id, ctx=ctx)

    def _get_watch_manager(self) -> Optional["WatchManager"]:
        if not self._watch_scheduler:
            return None
        return self._watch_scheduler.watch_manager

    async def _attach_and_filter_tags(
        self,
        entries: List[Dict[str, Any]],
        ctx: RequestContext,
        tags: Optional[List[str]],
        include_tags: bool,
    ) -> List[Dict[str, Any]]:
        normalized_tags = normalize_search_tags(tags, discard_invalid=True)
        if not entries or (not normalized_tags and not include_tags):
            return entries
        tags_by_uri: Dict[str, List[str]] = {}
        if self._vikingdb:
            uris = list(
                dict.fromkeys(str(entry.get("uri") or "") for entry in entries if entry.get("uri"))
            )
            if uris:
                records = await VikingDBManagerProxy(self._vikingdb, ctx).filter(
                    filter=And(
                        [Or([Eq("uri", item_uri) for item_uri in uris]), In("level", [0, 1, 2])]
                    ),
                    limit=max(len(uris) * 3, 1),
                    output_fields=["uri", "level", "search_tags"],
                )
                records_by_uri: Dict[str, List[Dict[str, Any]]] = {}
                for record in records:
                    records_by_uri.setdefault(str(record.get("uri") or ""), []).append(record)
                for entry in entries:
                    uri = str(entry.get("uri") or "")
                    levels = {0, 1} if entry.get("isDir", False) else {2}
                    found: List[str] = []
                    for record in sorted(
                        records_by_uri.get(uri, []), key=lambda item: item.get("level", 99)
                    ):
                        if record.get("level") in levels:
                            for tag in normalize_search_tags(
                                record.get("search_tags"), discard_invalid=True
                            ):
                                if tag not in found:
                                    found.append(tag)
                    tags_by_uri[uri] = found
        result_entries: List[Dict[str, Any]] = []
        for entry in entries:
            entry_tags = tags_by_uri.get(str(entry.get("uri") or ""), [])
            if normalized_tags and not set(normalized_tags).issubset(entry_tags):
                continue
            if include_tags:
                entry = {**entry, "tags": entry_tags}
            result_entries.append(entry)
        return result_entries

    async def _collect_tagged_page(
        self,
        fetch_page: Callable[[int, Optional[int]], Awaitable[List[Dict[str, Any]]]],
        ctx: RequestContext,
        tags: List[str],
        include_tags: bool,
        offset: int,
        node_limit: int,
    ) -> ListingPage:
        """Collect a visible page after tag filtering.

        Args:
            fetch_page: Loads one pre-tag page by offset and limit.
            ctx: Request identity used for tag lookup.
            tags: Required retrieval tags.
            include_tags: Whether to retain tags in returned entries.
            offset: Number of matched entries to skip.
            node_limit: Maximum matched entries to return.

        Returns:
            The requested page and whether more matched entries are available.
        """
        if node_limit <= 0:
            entries = await fetch_page(0, None)
            entries = await self._attach_and_filter_tags(entries, ctx, tags, include_tags)
            return ListingPage(entries=entries[offset:], has_more=False)

        batch_size = max(node_limit, 256)
        probe_limit = node_limit + 1
        source_offset = 0
        remaining_offset = offset
        result: List[Dict[str, Any]] = []
        while len(result) < probe_limit:
            entries = await fetch_page(source_offset, batch_size)
            filtered = await self._attach_and_filter_tags(entries, ctx, tags, include_tags)
            if remaining_offset >= len(filtered):
                remaining_offset -= len(filtered)
            else:
                result.extend(
                    filtered[remaining_offset : remaining_offset + probe_limit - len(result)]
                )
                remaining_offset = 0
            if len(entries) < batch_size:
                break
            source_offset += len(entries)
        return ListingPage(entries=result[:node_limit], has_more=len(result) > node_limit)

    async def ls(
        self,
        uri: str,
        ctx: RequestContext,
        recursive: bool = False,
        simple: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        include_abstract: Optional[bool] = None,
        include_overview: Optional[bool] = None,
        overview_limit: int = 4000,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        extra_fields: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        include_tags: bool = False,
        offset: int = 0,
    ) -> ListingPage:
        """List directory contents.

        Args:
            uri: Viking URI
            recursive: List all subdirectories recursively
            simple: Return only relative path list
            output: str = "original" or "agent"
            abs_limit: Maximum returned abstract length
            include_abstract: Include directory L0 abstracts; defaults to the output format
            include_overview: Include directory L1 overviews
            overview_limit: Maximum returned overview length
            show_all_hidden: bool = False (list all hidden files, like -a)
            node_limit: int = 1000 (maximum number of nodes to list)
            sort_by: Optional sort field for non-recursive listings
            sort_order: Sort direction, "asc" or "desc"
            extra_fields: Optional extra fields to include (locked, id, count)
        """
        viking_fs = self._ensure_initialized()
        extra_fields = extra_fields or []
        if namespace_policy_for_uri(uri).processing_mode is NamespaceProcessingMode.FILE_ONLY:
            await viking_fs._ensure_access(uri, ctx)
            unsupported_fields = {"id", "count", "abstract", "overview", "tags"}.intersection(
                extra_fields
            )
            if tags or include_tags:
                raise InvalidArgumentError(f"tags are not supported for namespace: {uri}")
            if include_abstract is True or include_overview is True:
                raise InvalidArgumentError(
                    f"semantic listing output is not supported for namespace: {uri}"
                )
            if unsupported_fields:
                raise InvalidArgumentError(
                    f"fields are not supported for namespace {uri}: "
                    + ", ".join(sorted(unsupported_fields))
                )
            include_abstract = False
            include_overview = False
        use_simple_paths = simple and not extra_fields

        async def fetch_page(page_offset: int, page_limit: Optional[int]) -> List[Dict[str, Any]]:
            """Fetch and return one pre-tag page."""
            if recursive:
                return await viking_fs.tree(
                    uri,
                    ctx=ctx,
                    output="original",
                    abs_limit=abs_limit,
                    include_abstract=None,
                    include_overview=False,
                    overview_limit=overview_limit,
                    show_all_hidden=show_all_hidden,
                    node_limit=page_limit,
                    level_limit=level_limit,
                    offset=page_offset,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    extra_fields=None,
                )
            return await viking_fs.ls(
                uri,
                ctx=ctx,
                output="original",
                abs_limit=abs_limit,
                include_abstract=None,
                include_overview=False,
                overview_limit=overview_limit,
                show_all_hidden=show_all_hidden,
                node_limit=page_limit,
                offset=page_offset,
                sort_by=sort_by,
                sort_order=sort_order,
                extra_fields=None,
            )

        if tags:
            page = await self._collect_tagged_page(
                fetch_page,
                ctx,
                tags,
                False if use_simple_paths else include_tags or bool(tags) or "tags" in extra_fields,
                offset,
                node_limit,
            )
        else:
            fetch_limit = node_limit + 1 if node_limit > 0 else node_limit
            entries = await fetch_page(offset, fetch_limit)
            entries = await self._attach_and_filter_tags(
                entries, ctx, None, include_tags or "tags" in extra_fields
            )
            page = ListingPage(
                entries=entries[:node_limit] if node_limit > 0 else entries,
                has_more=node_limit > 0 and len(entries) > node_limit,
            )
        if use_simple_paths:
            return ListingPage(
                entries=[entry.get("uri", "") for entry in page.entries],
                has_more=page.has_more,
            )
        if (
            output != "original"
            or extra_fields
            or include_abstract is True
            or include_overview is True
        ):
            return ListingPage(
                entries=await viking_fs._finalize_listing_entries(
                    page.entries,
                    output,
                    abs_limit,
                    extra_fields,
                    recursive,
                    ctx=ctx,
                    include_abstract=include_abstract,
                    include_overview=include_overview is True,
                    overview_limit=overview_limit,
                ),
                has_more=page.has_more,
            )
        return page

    @staticmethod
    def _reject_storage_internal_target(uri: str) -> None:
        """Reject reserved names in targets and implicitly created parent directories."""
        if any(is_storage_internal_name(part) for part in uri_parts(uri)):
            raise InvalidArgumentError(f"cannot create storage internal name: {uri}")

    @staticmethod
    def _reject_file_only_semantic_sidecar_target(uri: str) -> None:
        if namespace_policy_for_uri(
            uri
        ).processing_mode is NamespaceProcessingMode.FILE_ONLY and is_abstract_overview_uri(uri):
            raise InvalidArgumentError(f"semantic sidecars are not supported for namespace: {uri}")

    async def mkdir(
        self,
        uri: str,
        ctx: RequestContext,
        description: Optional[str] = None,
        acl: AclSpec | Dict[str, Any] | None = None,
    ) -> None:
        """Create directory."""
        viking_fs = self._ensure_initialized()
        self._reject_storage_internal_target(uri)
        directory_uri, abstract_uri = self._resolve_directory_uris(uri)
        namespace_policy = namespace_policy_for_uri(directory_uri)
        if namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY:
            await viking_fs._ensure_access(directory_uri, ctx, action=AclAction.WRITE)
            if acl is not None and not namespace_policy.supports_acl:
                raise InvalidArgumentError(f"ACL is not supported for namespace: {directory_uri}")
            if description is not None:
                raise InvalidArgumentError(
                    f"directory descriptions are not supported for namespace: {directory_uri}"
                )
            async with self._uri_mutation_coordinator.mutation(ctx.account_id, [directory_uri]):
                await viking_fs.mkdir(uri, ctx=ctx)
                return

        versions = self.compile_versions
        managed = versions is not None and await versions.owning_target(
            ctx.account_id, directory_uri
        )

        async def create():
            async with self._uri_mutation_coordinator.mutation(ctx.account_id, [directory_uri]):
                acl_update = (
                    await viking_fs.prepare_acl_update(directory_uri, acl, ctx)
                    if acl is not None
                    else None
                )
                lease = None
                if managed:
                    await viking_fs._ensure_access(directory_uri, ctx, action=AclAction.WRITE)
                    parent = VikingURI(directory_uri).parent.uri
                    while not await viking_fs.exists(parent, ctx=ctx):
                        parent = VikingURI(parent).parent.uri
                    lease = await viking_fs._async_agfs.pathlock_acquire_tree(
                        viking_fs._uri_to_path(parent, ctx=ctx)
                    )
                try:
                    await viking_fs.mkdir(uri, ctx=ctx, **({"lease_ref": lease} if lease else {}))
                    if managed:
                        await versions.record_effects(
                            [{"uri": directory_uri, "kind": "directory"}], ctx
                        )
                    else:
                        lease = await viking_fs._async_agfs.pathlock_acquire_exact(
                            viking_fs._uri_to_path(abstract_uri, ctx=ctx)
                        )
                    await initialize(acl_update, lease)
                finally:
                    if lease is not None:
                        await viking_fs._async_agfs.pathlock_release(lease)

        async def initialize(acl_update, lease):
            abstract = self._normalize_directory_description(description)
            if not abstract:
                abstract = f"# {uri_leaf_name(directory_uri)}"

            await viking_fs.write_file(
                abstract_uri,
                render_abstract_overview(
                    ContextLevel.ABSTRACT,
                    directory_uri,
                    abstract,
                    {
                        "generated_by": {
                            "component": "FSService",
                            "trigger": "mkdir",
                        },
                        "freshness": {
                            "total_entries": 0,
                            "sampled_entries": 0,
                            "unsampled_entries": 0,
                            "pending_child_changes": 0,
                        },
                    },
                ),
                ctx=ctx,
                lease_ref=lease,
            )
            await vectorize_directory_meta(
                uri=directory_uri,
                abstract=abstract,
                overview="",
                context_type=context_type_for_uri(directory_uri),
                ctx=ctx,
                ingest_options=IngestOptions(acl_update=acl_update),
                include_overview=False,
            )

        if managed:
            await run_to_completion(
                lambda: versions.record_operation([directory_uri], ctx, "mkdir", {}, create)
            )
        else:
            await create()

    @staticmethod
    def _normalize_directory_description(description: Optional[str]) -> Optional[str]:
        if description is None:
            return None
        abstract = description.strip()
        return abstract or None

    @staticmethod
    def _resolve_directory_uris(uri: str) -> tuple[str, str]:
        abstract_uri = VikingURI(uri).join(".abstract.md").uri
        directory_uri = VikingURI(abstract_uri).parent.uri
        return directory_uri, abstract_uri

    async def rm(
        self,
        uri: str,
        ctx: RequestContext,
        recursive: bool = False,
        wait: bool = False,
    ) -> Optional[Dict[str, Any]]:
        async def execute() -> Optional[Dict[str, Any]]:
            return await self._rm(
                uri,
                ctx,
                recursive=recursive,
                wait=wait,
            )

        result, _task_id = await run_tracked_operation(
            "fs_rm",
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            operation=execute,
            wait=wait,
            resource_id=uri,
        )
        return result

    async def _rm(
        self,
        uri: str,
        ctx: RequestContext,
        recursive: bool = False,
        wait: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Remove resource."""
        viking_fs = self._ensure_initialized()
        namespace_policy = namespace_policy_for_uri(uri)
        if namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY:
            result = await viking_fs.rm(uri, recursive=recursive, ctx=ctx, no_vector=True)
            if isinstance(result, dict):
                result["semantic_status"] = "skipped"
                result["vector_status"] = "skipped"
            return result

        cleanup_result: Optional[Dict[str, Any]] = None
        context_type = context_type_for_uri(uri)
        refresh_parent_uri = self._semantic_refresh_parent_uri(uri, context_type)
        memory_overview_uri = self._memory_overview_parent_uri(uri, context_type)
        if self.compile_versions is not None:
            async with self.compile_versions.before_remove(uri, ctx) as lease:

                async def remove():
                    result = await viking_fs.rm(
                        uri,
                        recursive=recursive,
                        ctx=ctx,
                        **({"lease_ref": lease} if lease is not None else {}),
                    )
                    await self.compile_versions.record_effects(
                        [{"uri": uri, "kind": "deleted"}], ctx
                    )
                    await self.compile_versions.after_remove(uri, ctx)
                    return result

                async def record_remove():
                    return await self.compile_versions.record_operation(
                        [uri], ctx, "rm", {"recursive": recursive}, remove, recursive=True
                    )

                result = (
                    await run_to_completion(record_remove)
                    if lease is not None
                    else await record_remove()
                )
        else:
            result = await viking_fs.rm(uri, recursive=recursive, ctx=ctx)
        await self._sync_watch_after_rm(uri, account_id=ctx.account_id, context_type=context_type)
        # A refresh on a parent that no longer exists would lock its sidecar
        # paths and thereby recreate the deleted directory. Nothing to
        # summarize there; skip it.
        if refresh_parent_uri and not await viking_fs.exists(refresh_parent_uri, ctx=ctx):
            refresh_parent_uri = None
        queue_status = None
        refresh_action: Optional[FreshnessAction] = None
        try:
            if refresh_parent_uri:
                refresh_action = await self._enqueue_delete_refresh(
                    root_uri=refresh_parent_uri,
                    deleted_uri=uri,
                    context_type=context_type,
                    ctx=ctx,
                    force_refresh=wait,
                )
            if self._resource_memory_link_service and context_type == "resource":
                cleanup_result = await self._resource_memory_link_service.before_resource_delete(
                    ctx=ctx,
                    resource_uri=uri,
                    recursive=recursive,
                )
            if memory_overview_uri:
                await MemoryUpdater.refresh_schema_overview(
                    viking_fs=viking_fs,
                    directory_uri=memory_overview_uri,
                    ctx=ctx,
                )
            for cleanup_overview_uri in self._memory_overview_parent_uris_from_cleanup(
                cleanup_result
            ):
                await MemoryUpdater.refresh_schema_overview(
                    viking_fs=viking_fs,
                    directory_uri=cleanup_overview_uri,
                    ctx=ctx,
                )
            if refresh_parent_uri and wait and refresh_action is not FreshnessAction.MARK_PENDING:
                queue_status = await self._wait_for_refresh()
        finally:
            pass
        if cleanup_result is not None and isinstance(result, dict):
            result["memory_cleanup"] = cleanup_result
        if refresh_parent_uri and isinstance(result, dict):
            result["semantic_root_uri"] = refresh_parent_uri
            result["semantic_status"] = (
                "deferred"
                if refresh_action is FreshnessAction.MARK_PENDING
                else self._semantic_refresh_status(wait=wait, queue_status=queue_status)
            )
            if queue_status is not None:
                result["queue_status"] = queue_status
        return result

    @staticmethod
    def _semantic_refresh_status(
        *,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
    ) -> str:
        if not wait:
            return "queued"
        if not isinstance(queue_status, dict):
            return "complete"
        semantic = queue_status.get("Semantic", {})
        if not isinstance(semantic, dict):
            return "complete"
        try:
            if int(semantic.get("error_count", 0) or 0) > 0:
                return "failed"
        except (TypeError, ValueError):
            if semantic.get("errors"):
                return "failed"
        if semantic.get("errors"):
            return "failed"
        return "complete"

    @staticmethod
    def _semantic_refresh_parent_uri(uri: str, context_type: str) -> Optional[str]:
        if namespace_policy_for_uri(uri).processing_mode is NamespaceProcessingMode.FILE_ONLY:
            return None
        if context_type not in {"resource", "skill"}:
            return None
        parent = VikingURI(uri).parent
        if context_type == "skill":
            if parent is None:
                return None
            classification = classify_uri(parent.uri)
            if (
                not classification.is_skill
                or classification.is_skill_root
                or classification.is_skill_namespace
            ):
                return None
        return parent.uri if parent and parent.scope else None

    @staticmethod
    def _memory_overview_parent_uri(uri: str, context_type: str) -> Optional[str]:
        if context_type != "memory":
            return None
        leaf = uri.rstrip("/").rsplit("/", 1)[-1]
        if leaf in {".abstract.md", ".overview.md", ".relations.json"}:
            return None
        parent = VikingURI(uri).parent
        if parent is None:
            return None
        if not MemoryUpdater.memory_type_from_uri(parent.uri):
            return None
        return parent.uri

    @classmethod
    def _memory_overview_parent_uris_from_cleanup(
        cls,
        cleanup_result: Optional[Dict[str, Any]],
    ) -> List[str]:
        if not isinstance(cleanup_result, dict):
            return []

        overview_uris: List[str] = []
        for field in ("memory_uris", "deleted_memory_uris"):
            values = cleanup_result.get(field)
            if not isinstance(values, list):
                continue
            for memory_uri in values:
                if not isinstance(memory_uri, str):
                    continue
                overview_uri = cls._memory_overview_parent_uri(
                    memory_uri,
                    context_type_for_uri(memory_uri),
                )
                if overview_uri:
                    overview_uris.append(overview_uri)
        return list(dict.fromkeys(overview_uris))

    async def _enqueue_delete_refresh(
        self,
        *,
        root_uri: str,
        deleted_uri: str,
        context_type: str,
        ctx: RequestContext,
        force_refresh: bool = False,
    ) -> FreshnessAction:
        semantic_config = get_openviking_config().semantic
        decision = await plan_abstract_overview_refresh(
            viking_fs=self._viking_fs,
            dir_uri=root_uri,
            changed_entries=1,
            ctx=ctx,
            overview_sample_limit=getattr(semantic_config, "overview_sample_limit", 32),
            refresh_ratio=getattr(semantic_config, "freshness_refresh_ratio", 0.10),
            force_refresh=force_refresh,
        )
        if decision.action is not FreshnessAction.REFRESH_NOW:
            return decision.action
        try:
            queue_manager = get_queue_manager()
        except RuntimeError as exc:
            logger.warning("QueueManager not available, skipping delete refresh: %s", exc)
            return decision.action
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry_id = get_current_telemetry().telemetry_id
        msg = SemanticMsg(
            uri=root_uri,
            context_type=context_type,
            recursive=False,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            group_ids=ctx.group_ids,
            peer_id=ctx.user.user_id,
            role=str(ctx.role),
            skip_vectorization=False,
            telemetry_id=telemetry_id,
            coalesce_key=build_semantic_coalesce_key(
                context_type=context_type,
                uri=root_uri,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                peer_id=ctx.user.user_id,
            ),
            changes={"deleted": [deleted_uri]},
            generation_trigger="content_delete",
        )
        await semantic_queue.enqueue(msg)
        return decision.action

    async def _enqueue_copy_refresh(
        self,
        *,
        root_uri: str,
        source_uri: str,
        copied_uri: str,
        context_type: str,
        ctx: RequestContext,
        change_kind: Literal["added", "deleted"] = "added",
    ) -> str:
        """Queue a parent-only semantic refresh after a committed transfer."""
        await mark_abstract_overview_pending(
            viking_fs=self._viking_fs,
            dir_uri=root_uri,
            changed_entries=1,
            ctx=ctx,
        )
        try:
            queue_manager = get_queue_manager()
        except RuntimeError as exc:
            logger.warning("QueueManager not available, skipping copy refresh: %s", exc)
            return "skipped"

        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry_id = get_current_telemetry().telemetry_id
        msg = SemanticMsg(
            uri=root_uri,
            context_type=context_type,
            recursive=False,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            peer_id=ctx.user.user_id,
            role=str(ctx.role),
            skip_vectorization=False,
            telemetry_id=telemetry_id,
            coalesce_key=build_semantic_coalesce_key(
                context_type=context_type,
                uri=root_uri,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                peer_id=ctx.user.user_id,
            ),
            changes={change_kind: [copied_uri]},
            generation_trigger="content_copy",
            copy_source_uri=source_uri,
        )
        await semantic_queue.enqueue(msg)
        return "queued"

    async def _enqueue_transfer_rebuild(
        self,
        *,
        root_uri: str,
        target_uri: str,
        recursive: bool,
        context_type: str,
        ctx: RequestContext,
    ) -> str:
        """Queue fresh semantic processing when the source has no index artifacts."""
        if not recursive:
            await mark_abstract_overview_pending(
                viking_fs=self._viking_fs,
                dir_uri=root_uri,
                changed_entries=1,
                ctx=ctx,
            )
        try:
            queue_manager = get_queue_manager()
        except RuntimeError as exc:
            logger.warning("QueueManager not available, skipping transfer rebuild: %s", exc)
            return "skipped"
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry_id = get_current_telemetry().telemetry_id
        msg = SemanticMsg(
            uri=root_uri,
            context_type=context_type,
            recursive=recursive,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            group_ids=ctx.group_ids,
            peer_id=ctx.user.user_id,
            role=str(ctx.role),
            skip_vectorization=False,
            telemetry_id=telemetry_id,
            coalesce_key=(
                ""
                if recursive
                else build_semantic_coalesce_key(
                    context_type=context_type,
                    uri=root_uri,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    peer_id=ctx.user.user_id,
                )
            ),
            changes=None if recursive else {"added": [target_uri]},
            generation_trigger="content_transfer_rebuild",
        )
        await semantic_queue.enqueue(msg)
        return "queued"

    async def _wait_for_refresh(self) -> Dict[str, Any]:
        context = get_task_context()
        if context is None:
            raise RuntimeError("TaskContext is required for request-scoped queue waiting")
        work_ids = get_enqueued_work_ids()
        tracker = get_task_tracker()
        try:
            if context.work_id is None:
                await tracker.wait_for_task_work(context.task_id, timeout=None)
            elif work_ids:
                await tracker.wait_for_work(
                    context.task_id,
                    work_ids,
                    timeout=None,
                )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise DeadlineExceededError("queue processing", None) from exc
        return await tracker.build_queue_status(
            context.task_id, work_ids if context.work_id is not None else None
        )

    async def cp(
        self,
        from_uri: str,
        to_uri: str,
        recursive: bool,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Copy a resource without exposing a cancellable partial transaction."""
        from_uri = VikingFS._normalize_transfer_uri(from_uri)
        to_uri = VikingFS._normalize_transfer_uri(to_uri)
        self._reject_storage_internal_target(to_uri)
        self._reject_file_only_semantic_sidecar_target(to_uri)
        validate_account_memory_template_transfer(to_uri)
        return await self._finish_transfer_after_caller_cancel(
            self._cp_and_refresh(from_uri, to_uri, recursive=recursive, ctx=ctx),
            operation="copy",
        )

    async def _cp_and_refresh(
        self,
        from_uri: str,
        to_uri: str,
        *,
        recursive: bool,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Commit copy and enqueue its parent refresh as one cancellation-safe unit."""
        viking_fs = self._ensure_initialized()
        source_policy = namespace_policy_for_uri(from_uri)
        target_policy = namespace_policy_for_uri(to_uri)
        source_indexed = source_policy.processing_mode is NamespaceProcessingMode.INDEXED
        target_indexed = target_policy.processing_mode is NamespaceProcessingMode.INDEXED

        async def copy() -> Dict[str, Any]:
            async with self._uri_mutation_coordinator.mutation(ctx.account_id, [from_uri, to_uri]):
                async with self._record_transfer(
                    from_uri, to_uri, ctx, recursive=recursive
                ) as lease:
                    options = {"lease_ref": lease} if lease is not None else {}
                    if not (source_indexed and target_indexed):
                        options.update(
                            maintain_vectors=False,
                            copy_semantic_sidecars=False,
                        )
                    return await viking_fs.cp(
                        from_uri,
                        to_uri,
                        recursive=recursive,
                        ctx=ctx,
                        **options,
                    )

        if self.compile_versions is None:
            transfer_result = await copy()
        else:
            transfer_result = await self.compile_versions.record_operation(
                [to_uri],
                ctx,
                "cp",
                {"from": from_uri, "to": to_uri, "recursive": recursive},
                copy,
                recursive=True,
            )

        result = dict(transfer_result or {})
        result.setdefault("from", from_uri)
        result.setdefault("to", to_uri)
        result.setdefault("recursive", recursive)
        context_type = context_type_for_uri(to_uri)
        if not source_indexed and target_indexed:
            target_stat = await viking_fs.stat(to_uri, ctx=ctx, skip_count=True)
            target_is_dir = bool(target_stat.get("isDir"))
            rebuild_root_uri = (
                to_uri if target_is_dir else self._semantic_refresh_parent_uri(to_uri, context_type)
            )
            if not rebuild_root_uri:
                result["semantic_status"] = "skipped"
                result["vector_status"] = "skipped"
                return result
            result["semantic_root_uri"] = rebuild_root_uri
            try:
                result["semantic_status"] = await self._enqueue_transfer_rebuild(
                    root_uri=rebuild_root_uri,
                    target_uri=to_uri,
                    recursive=target_is_dir,
                    context_type=context_type,
                    ctx=ctx,
                )
            except Exception as exc:
                logger.warning(
                    "Copy committed but semantic rebuild failed for %s: %s",
                    to_uri,
                    exc,
                )
                result["semantic_status"] = "failed"
                result["semantic_error"] = str(exc)
            return result

        refresh_parent_uri = self._semantic_refresh_parent_uri(to_uri, context_type)
        if not refresh_parent_uri:
            if target_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY:
                result["semantic_status"] = "skipped"
                result["vector_status"] = "skipped"
            return result

        result["semantic_root_uri"] = refresh_parent_uri
        try:
            result["semantic_status"] = await self._enqueue_copy_refresh(
                root_uri=refresh_parent_uri,
                source_uri=from_uri,
                copied_uri=to_uri,
                context_type=context_type,
                ctx=ctx,
            )
        except Exception as exc:
            logger.warning(
                "Copy committed but parent semantic refresh failed for %s: %s",
                to_uri,
                exc,
            )
            result["semantic_status"] = "failed"
            result["semantic_error"] = str(exc)
        return result

    async def mv(self, from_uri: str, to_uri: str, ctx: RequestContext) -> None:
        """Move a resource without exposing a cancellable partial transaction."""
        from_uri = VikingFS._normalize_transfer_uri(from_uri)
        to_uri = VikingFS._normalize_transfer_uri(to_uri)
        self._reject_storage_internal_target(to_uri)
        self._reject_file_only_semantic_sidecar_target(to_uri)
        validate_account_memory_template_transfer(to_uri)
        await self._finish_transfer_after_caller_cancel(
            self._mv_and_refresh(from_uri, to_uri, ctx=ctx),
            operation="move",
        )

    async def _mv_and_refresh(
        self,
        from_uri: str,
        to_uri: str,
        *,
        ctx: RequestContext,
    ) -> None:
        """Commit move/watch state and enqueue all affected parent refreshes."""
        viking_fs = self._ensure_initialized()
        watch_manager = self._get_watch_manager()
        use_watch_transaction = (
            watch_manager is not None
            and context_type_for_uri(from_uri) == "resource"
            and context_type_for_uri(to_uri) == "resource"
            and namespace_policy_for_uri(from_uri).processing_mode
            is NamespaceProcessingMode.INDEXED
            and namespace_policy_for_uri(to_uri).processing_mode is NamespaceProcessingMode.INDEXED
            and not is_watch_task_control_uri(from_uri)
            and not is_watch_task_control_uri(to_uri)
        )
        source_policy = namespace_policy_for_uri(from_uri)
        target_policy = namespace_policy_for_uri(to_uri)
        source_indexed = source_policy.processing_mode is NamespaceProcessingMode.INDEXED
        target_indexed = target_policy.processing_mode is NamespaceProcessingMode.INDEXED
        vector_mode: Literal["transfer", "delete_source", "none"]
        if source_indexed and target_indexed:
            vector_mode = "transfer"
        elif source_indexed:
            vector_mode = "delete_source"
        else:
            vector_mode = "none"

        async def move() -> None:
            async with self._uri_mutation_coordinator.mutation(ctx.account_id, [from_uri, to_uri]):
                if use_watch_transaction:
                    await watch_manager.validate_target_prefix_rewrite_internal(
                        from_uri, to_uri, account_id=ctx.account_id
                    )
                async with self._record_transfer(from_uri, to_uri, ctx, move=True) as lease:
                    options = {"lease_ref": lease} if lease is not None else {}
                    if vector_mode != "transfer":
                        options.update(
                            vector_mode=vector_mode,
                            copy_semantic_sidecars=False,
                        )
                    await viking_fs.mv(
                        from_uri,
                        to_uri,
                        ctx=ctx,
                        **options,
                    )
                    if use_watch_transaction:
                        try:
                            await watch_manager.rewrite_target_prefix_internal(
                                from_uri, to_uri, account_id=ctx.account_id
                            )
                        except Exception as commit_error:
                            try:
                                # The authorized move may leave the file inheriting only
                                # write at its destination; rollback must still restore it.
                                await viking_fs.mv(
                                    to_uri,
                                    from_uri,
                                    ctx=replace(ctx, bypass_acl=True),
                                    **options,
                                )
                            except Exception as rollback_error:
                                logger.exception("Move rollback failed: %s -> %s", to_uri, from_uri)
                                raise rollback_error from commit_error
                            raise

        if self.compile_versions is None:
            await move()
        else:
            await self.compile_versions.record_operation(
                [from_uri, to_uri],
                ctx,
                "mv",
                {"from": from_uri, "to": to_uri},
                move,
                recursive=True,
            )
        rebuild_target = not source_indexed and target_indexed
        if rebuild_target:
            await self._refresh_move_parents(
                from_uri=from_uri,
                to_uri=to_uri,
                ctx=ctx,
                refresh_target=False,
            )
            target_stat = await viking_fs.stat(to_uri, ctx=ctx, skip_count=True)
            target_is_dir = bool(target_stat.get("isDir"))
            context_type = context_type_for_uri(to_uri)
            rebuild_root_uri = (
                to_uri if target_is_dir else self._semantic_refresh_parent_uri(to_uri, context_type)
            )
            if rebuild_root_uri:
                try:
                    await self._enqueue_transfer_rebuild(
                        root_uri=rebuild_root_uri,
                        target_uri=to_uri,
                        recursive=target_is_dir,
                        context_type=context_type,
                        ctx=ctx,
                    )
                except Exception as exc:
                    logger.warning(
                        "Move committed but semantic rebuild failed for %s: %s",
                        to_uri,
                        exc,
                    )
        else:
            await self._refresh_move_parents(from_uri=from_uri, to_uri=to_uri, ctx=ctx)

    @asynccontextmanager
    async def _record_transfer(
        self,
        from_uri: str,
        to_uri: str,
        ctx: RequestContext,
        *,
        move: bool = False,
        recursive: bool = True,
    ):
        """Hold managed transfer scopes through their final snapshot registration.

        The yielded lease extends the native transfer's existing locking. Once
        native transfer is attempted, its final directory state forms an ordinary
        rollback baseline even on failure. Service-level guards run before locking.
        """
        versions = self.compile_versions
        if versions is None:
            yield None
            return
        viking_fs = self._ensure_initialized()
        manager = self._get_watch_manager()
        watched_uris = (
            [
                task.to_uri
                for task in await manager.get_all_tasks(ctx.account_id, ctx.user.user_id, "root")
                if task.account_id == ctx.account_id and task.to_uri
            ]
            if manager is not None
            else []
        )
        if not await versions.guard_transfer(from_uri, to_uri, ctx, watched_uris=watched_uris):
            yield None
            return
        if move:
            await versions.before_move(from_uri.rstrip("/"), to_uri.rstrip("/"), ctx)
            await viking_fs._ensure_move_source_access(from_uri, ctx)
            await viking_fs._ensure_move_target_access(to_uri, ctx)
        else:
            await viking_fs._ensure_copy_source_access(from_uri, recursive=recursive, ctx=ctx)
            await viking_fs._ensure_access(to_uri, ctx, action=AclAction.WRITE)
        source_path = viking_fs._uri_to_path(from_uri, ctx=ctx)
        target_path = viking_fs._uri_to_path(to_uri, ctx=ctx)
        viking_fs._validate_transfer_paths(source_path, target_path)
        is_dir = (await viking_fs.stat(from_uri, ctx=ctx, skip_count=True)).get("isDir", False)
        if is_dir and not recursive:
            raise InvalidArgumentError(f"Cannot copy directory without --recursive: {from_uri}")
        if move and is_dir:
            await viking_fs._ensure_access(from_uri, ctx, action=AclAction.MANAGE)
        if not is_dir and to_uri.rstrip("/") != to_uri:
            raise InvalidArgumentError(
                f"Transfer destination for a file must include the target file name: {to_uri}"
            )
        await viking_fs._ensure_transfer_parent_directory(
            target_path, to_uri, operation="mv" if move else "cp"
        )
        await viking_fs._ensure_transfer_target_type(target_path, to_uri, is_dir=is_dir)
        if is_dir:
            if not await viking_fs.exists(to_uri, ctx=ctx):
                target_path = viking_fs._transfer_parent_path(target_path)
            roots = sorted({source_path, target_path}, key=len)
            requests = [
                {"path": path, "kind": "tree"}
                for path in roots
                if path == roots[0] or not path.startswith(roots[0] + "/")
            ]
        else:
            requests = [{"path": path, "kind": "exact"} for path in (source_path, target_path)]
        lease = await viking_fs._async_agfs.pathlock_acquire_batch(requests)
        try:
            yield lease
        finally:
            try:
                await run_to_completion(
                    lambda: versions.register_transfer(
                        from_uri, to_uri, ctx, lease_ref=lease, move=move
                    )
                )
            finally:
                await viking_fs._async_agfs.pathlock_release(lease)

    async def _finish_transfer_after_caller_cancel(
        self,
        transaction: Coroutine[Any, Any, Any],
        *,
        operation: str,
    ) -> Any:
        """Finish an already-started transfer before propagating caller cancellation."""
        transaction_task = asyncio.create_task(transaction)
        try:
            return await asyncio.shield(transaction_task)
        except asyncio.CancelledError:
            while not transaction_task.done():
                try:
                    await asyncio.shield(transaction_task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                transaction_task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.error(
                    "Filesystem %s transaction failed while caller was cancelled",
                    operation,
                    exc_info=True,
                )
            raise

    async def _refresh_move_parents(
        self,
        *,
        from_uri: str,
        to_uri: str,
        ctx: RequestContext,
        refresh_target: bool = True,
    ) -> None:
        """Queue transfer refreshes for both affected parents after mv commits."""
        if is_watch_task_control_uri(from_uri) or is_watch_task_control_uri(to_uri):
            return

        source_context_type = context_type_for_uri(from_uri)
        target_context_type = context_type_for_uri(to_uri)
        source_parent_uri = self._semantic_refresh_parent_uri(from_uri, source_context_type)
        target_parent_uri = self._semantic_refresh_parent_uri(to_uri, target_context_type)

        refreshes: List[tuple[str, str, Literal["added", "deleted"], str]] = []
        if source_parent_uri and source_parent_uri != target_parent_uri:
            refreshes.append((source_parent_uri, from_uri, "deleted", source_context_type))
        if target_parent_uri and refresh_target:
            refreshes.append((target_parent_uri, to_uri, "added", target_context_type))

        for parent_uri, changed_uri, change_kind, context_type in refreshes:
            try:
                await self._enqueue_copy_refresh(
                    root_uri=parent_uri,
                    source_uri=from_uri,
                    copied_uri=changed_uri,
                    change_kind=change_kind,
                    context_type=context_type,
                    ctx=ctx,
                )
            except Exception as exc:
                logger.warning(
                    "Move committed but %s parent semantic refresh failed for %s: %s",
                    change_kind,
                    parent_uri,
                    exc,
                )

    async def _sync_watch_after_rm(self, uri: str, *, account_id: str, context_type: str) -> None:
        if context_type != "resource":
            return
        if is_watch_task_control_uri(uri):
            return
        watch_manager = self._get_watch_manager()
        if not watch_manager:
            return
        deactivated = await watch_manager.deactivate_tasks_under_uri_internal(uri, account_id)
        if deactivated:
            logger.info(
                "Deactivated %d watch task(s) after deleting %s",
                len(deactivated),
                uri,
            )

    async def tree(
        self,
        uri: str,
        ctx: RequestContext,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
        extra_fields: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        include_tags: bool = False,
        offset: int = 0,
        directories_only: bool = False,
        include_abstract: Optional[bool] = None,
        include_overview: Optional[bool] = None,
        overview_limit: int = 4000,
    ) -> ListingPage:
        """Get directory tree."""
        viking_fs = self._ensure_initialized()
        if namespace_policy_for_uri(uri).processing_mode is NamespaceProcessingMode.FILE_ONLY:
            await viking_fs._ensure_access(uri, ctx)
            unsupported_fields = {"id", "count", "abstract", "overview", "tags"}.intersection(
                extra_fields or []
            )
            if tags or include_tags:
                raise InvalidArgumentError(f"tags are not supported for namespace: {uri}")
            if include_abstract is True or include_overview is True:
                raise InvalidArgumentError(
                    f"semantic listing output is not supported for namespace: {uri}"
                )
            if unsupported_fields:
                raise InvalidArgumentError(
                    f"fields are not supported for namespace {uri}: "
                    + ", ".join(sorted(unsupported_fields))
                )
            include_abstract = False
            include_overview = False

        async def fetch_page(page_offset: int, page_limit: Optional[int]) -> List[Dict[str, Any]]:
            """Fetch and return one pre-tag tree page."""
            return await viking_fs.tree(
                uri,
                ctx=ctx,
                output="original" if tags else output,
                abs_limit=abs_limit,
                include_abstract=None if tags else include_abstract,
                include_overview=False if tags else include_overview is True,
                overview_limit=overview_limit,
                show_all_hidden=show_all_hidden,
                directories_only=directories_only,
                node_limit=page_limit,
                level_limit=level_limit,
                extra_fields=None if tags else extra_fields,
                offset=page_offset,
            )

        if tags:
            page = await self._collect_tagged_page(
                fetch_page,
                ctx,
                tags,
                include_tags or bool(tags) or "tags" in (extra_fields or []),
                offset,
                node_limit,
            )
            if (
                output != "original"
                or extra_fields
                or include_abstract is True
                or include_overview is True
            ):
                return ListingPage(
                    entries=await viking_fs._finalize_listing_entries(
                        page.entries,
                        output,
                        abs_limit,
                        extra_fields,
                        True,
                        ctx=ctx,
                        include_abstract=include_abstract,
                        include_overview=include_overview is True,
                        overview_limit=overview_limit,
                    ),
                    has_more=page.has_more,
                )
            return page

        fetch_limit = node_limit + 1 if node_limit > 0 else node_limit
        entries = await fetch_page(offset, fetch_limit)
        entries = await self._attach_and_filter_tags(
            entries, ctx, None, include_tags or "tags" in (extra_fields or [])
        )
        return ListingPage(
            entries=entries[:node_limit] if node_limit > 0 else entries,
            has_more=node_limit > 0 and len(entries) > node_limit,
        )

    async def stat(
        self,
        uri: str,
        ctx: RequestContext,
        skip_count: bool = False,
        include_lock_status: bool = False,
    ) -> Dict[str, Any]:
        """Get resource status."""
        viking_fs = self._ensure_initialized()
        namespace_policy = namespace_policy_for_uri(uri)
        kwargs = {
            "ctx": ctx,
            "skip_count": skip_count,
            "include_lock_status": include_lock_status,
        }
        if namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY:
            kwargs["skip_count"] = True
            kwargs["include_vector_fields"] = False
        return await viking_fs.stat(uri, **kwargs)

    async def ensure_write_access(self, uri: str, ctx: RequestContext) -> None:
        """Validate write access without mutating the target."""
        viking_fs = self._ensure_initialized()
        await viking_fs._ensure_access(uri, ctx, action=AclAction.WRITE)

    async def _read_content(self, uri: str, ctx: RequestContext) -> tuple[str, str]:
        """Resolve the URI and read stored content without Privacy restoration."""
        viking_fs = self._ensure_initialized()
        resolved_uri = await self._resolve_uri(uri, ctx)
        try:
            content = await viking_fs.read_file(resolved_uri, ctx=ctx)
        except NotFoundError:
            if "%20" not in resolved_uri:
                raise
            legacy_uri = validate_safe_viking_uri_path(resolved_uri.replace("%20", " "))
            content = await viking_fs.read_file(legacy_uri, ctx=ctx)
            resolved_uri = legacy_uri
        return resolved_uri, content

    async def read(self, uri: str, ctx: RequestContext, offset: int = 0, limit: int = -1) -> str:
        """Read file content, restoring Privacy references for Skills and Sessions."""
        resolved_uri, content = await self._read_content(uri, ctx)
        if self._privacy_config_service is not None:
            skill_name = get_skill_name_from_uri(resolved_uri)
            session_id = get_session_id_from_uri(resolved_uri, canonical_session_root(ctx))
            if skill_name is not None:
                content = await restore_skill_content(
                    self._privacy_config_service,
                    content,
                    ctx,
                    skill_name=skill_name,
                )
            elif session_id is not None:
                content = await restore_session_content(
                    self._privacy_config_service,
                    content,
                    ctx,
                    session_id=session_id,
                    jsonl=resolved_uri.endswith("/messages.jsonl"),
                )

        if offset == 0 and limit == -1:
            return content
        lines = content.splitlines(keepends=True)
        sliced = lines[offset:] if limit == -1 else lines[offset : offset + limit]
        return "".join(sliced)

    async def read_visible(
        self,
        uri: str,
        ctx: RequestContext,
        offset: int = 0,
        limit: int = -1,
    ) -> str:
        """Read public content, hiding reserved metadata from memory files.
        Accepts a Viking URI or a 32-char hex vector record id.
        """
        # Resolve id to URI before classify_uri / visible_content which expect
        # a real viking:// URI.
        resolved_uri = await self._resolve_uri(uri, ctx)
        if self._privacy_sanitizer is not None:
            stat = await self._ensure_initialized().stat(
                resolved_uri,
                ctx=ctx,
                skip_count=True,
            )
            size = stat.get("size") if isinstance(stat, dict) else None
            if isinstance(size, int) and size > self._privacy_sanitizer.max_content_bytes:
                raise ResourceExhaustedError(
                    "File exceeds the Privacy visible-read size limit",
                    details={
                        "uri": resolved_uri,
                        "reason": "privacy_visible_read_limit_exceeded",
                        "size_bytes": size,
                        "max_bytes": self._privacy_sanitizer.max_content_bytes,
                    },
                )
        resolved_uri, content = await self._read_content(resolved_uri, ctx)
        if classify_uri(resolved_uri).is_memory:
            content = visible_content(content, uri=resolved_uri)
        if self._privacy_sanitizer is not None:
            content = (
                await self._privacy_sanitizer.sanitize(
                    content,
                    ctx=ctx,
                    uri=resolved_uri,
                    prefer_credentials=(
                        get_session_id_from_uri(resolved_uri, canonical_session_root(ctx))
                        is not None
                    ),
                )
            ).content
        if offset == 0 and limit == -1:
            return content
        lines = content.splitlines(keepends=True)
        sliced = lines[offset:] if limit == -1 else lines[offset : offset + limit]
        return "".join(sliced)

    async def abstract(self, uri: str, ctx: RequestContext) -> str:
        """Read L0 abstract (.abstract.md)."""
        viking_fs = self._ensure_initialized()
        if namespace_policy_for_uri(uri).processing_mode is NamespaceProcessingMode.FILE_ONLY:
            await viking_fs._ensure_access(uri, ctx)
            raise InvalidArgumentError(f"abstract is not supported for namespace: {uri}")
        return await viking_fs.abstract(uri, ctx=ctx)

    async def overview(self, uri: str, ctx: RequestContext) -> str:
        """Read L1 overview (.overview.md)."""
        viking_fs = self._ensure_initialized()
        if namespace_policy_for_uri(uri).processing_mode is NamespaceProcessingMode.FILE_ONLY:
            await viking_fs._ensure_access(uri, ctx)
            raise InvalidArgumentError(f"overview is not supported for namespace: {uri}")
        return await viking_fs.overview(uri, ctx=ctx)

    async def grep(
        self,
        uri: str,
        pattern: str,
        ctx: RequestContext,
        exclude_uri: Optional[str] = None,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        level_limit: int = 10,
        tags: Optional[List[str]] = None,
        include_tags: bool = False,
        before_context: int = 0,
        after_context: int = 0,
    ) -> Dict:
        """Content search."""
        viking_fs = self._ensure_initialized()
        namespace_policy = namespace_policy_for_uri(uri)
        if namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY:
            await viking_fs._ensure_access(uri, ctx)
            if tags or include_tags:
                raise InvalidArgumentError(f"tags are not supported for namespace: {uri}")
        normalized_tags = normalize_search_tags(tags, discard_invalid=True)
        tag_filter = None
        if normalized_tags:
            from openviking.utils.tags import build_search_tags_filter

            tag_filter = build_search_tags_filter(normalized_tags)
        kwargs = {
            "exclude_uri": exclude_uri,
            "case_insensitive": case_insensitive,
            "node_limit": node_limit,
            "level_limit": level_limit,
            "ctx": ctx,
            "tag_filter": tag_filter,
            "include_tags": include_tags or bool(normalized_tags),
            "before_context": before_context,
            "after_context": after_context,
            "force_fs": namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY,
        }
        if _may_include_memory_content(uri):
            kwargs["content_transform"] = _visible_grep_content
        result = dict(await viking_fs.grep(uri, pattern, **kwargs))
        matches = result.get("matches", [])
        if include_tags and not normalized_tags and any("tags" not in match for match in matches):
            matches = await self._attach_and_filter_tags(matches, ctx, None, include_tags=True)
        result["matches"] = matches
        result["count"] = len(matches)
        return result

    async def glob(
        self,
        pattern: str,
        ctx: RequestContext,
        uri: str = "viking://",
        node_limit: Optional[int] = None,
        extra_fields: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        include_tags: bool = False,
    ) -> Dict:
        """File pattern matching."""
        viking_fs = self._ensure_initialized()
        namespace_policy = namespace_policy_for_uri(uri)
        if namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY:
            await viking_fs._ensure_access(uri, ctx)
            if tags or include_tags:
                raise InvalidArgumentError(f"tags are not supported for namespace: {uri}")
        unsupported_fields = {"id", "count", "abstract", "overview", "tags"}.intersection(
            extra_fields or []
        )
        if unsupported_fields and (
            namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY
        ):
            raise InvalidArgumentError(
                f"fields are not supported for namespace {uri}: "
                + ", ".join(sorted(unsupported_fields))
            )
        normalized_tags = normalize_search_tags(tags, discard_invalid=True)
        project_tags = bool(normalized_tags) or include_tags
        tag_filter = None
        if normalized_tags:
            from openviking.utils.tags import build_search_tags_filter

            tag_filter = build_search_tags_filter(normalized_tags)
        result = dict(
            await viking_fs.glob(
                pattern,
                uri=uri,
                node_limit=node_limit,
                ctx=ctx,
                extra_fields=extra_fields
                if extra_fields is not None
                else ([] if project_tags else None),
                tag_filter=tag_filter,
                force_fs=namespace_policy.processing_mode is NamespaceProcessingMode.FILE_ONLY,
            )
        )
        if not project_tags:
            return result

        matches = await self._attach_and_filter_tags(
            result.get("matches", []), ctx, normalized_tags, include_tags=True
        )
        if node_limit is not None and node_limit > 0:
            matches = matches[:node_limit]
        result["matches"] = matches
        result["count"] = len(matches)
        return result

    async def read_file_bytes(self, uri: str, ctx: RequestContext) -> bytes:
        """Read file as raw bytes."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.read_file_bytes(uri, ctx=ctx)

    async def write(
        self,
        uri: str,
        content: str | bytes,
        ctx: RequestContext,
        mode: str = "replace",
        wait: bool = False,
        processing_mode: str = "semantic_and_vectors",
        tags: Optional[List[str]] = None,
        tag_mode: str = "replace",
        acl: AclSpec | Dict[str, Any] | None = None,
        edit_source: Literal["manual", "agent", "compile"] = "agent",
        record_version: bool = True,
        commit_content: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> Dict[str, Any]:
        """Write content and refresh it after persisting managed output versions.

        Internal restores supply ``commit_content`` to record their own version.
        """
        viking_fs = self._ensure_initialized()
        if commit_content is None and self.compile_versions is not None:
            commit_content = partial(
                self.compile_versions.record_write,
                ctx=ctx,
                edit_source=edit_source,
                record_version=record_version and edit_source != "compile",
            )
        coordinator = ContentWriteCoordinator(
            viking_fs=viking_fs,
            vikingdb=self._vikingdb,
            privacy_sanitizer=self._privacy_sanitizer,
            commit_content=commit_content,
        )
        write = partial(
            coordinator.write,
            uri=uri,
            content=content,
            ctx=ctx,
            mode=mode,
            wait=wait,
            processing_mode=processing_mode,
            tags=tags,
            tag_mode=tag_mode,
            acl=acl,
        )
        if self.compile_versions is None or edit_source == "compile":
            return await write()
        return await self.compile_versions.record_operation(
            [uri], ctx, f"{edit_source}_revise", {"mode": mode}, write
        )

    async def batch_write(
        self,
        *,
        root_uri: str,
        operations: list[dict[str, Any]],
        ctx: RequestContext,
        wait: bool = True,
        skip_conflicts: bool = False,
        defer_processing: bool = False,
        processing_mode: str = "semantic_and_vectors",
        edit_source: Literal["manual", "agent", "compile"] = "agent",
    ) -> Dict[str, Any]:
        """Apply multiple file writes and aggregate downstream refresh."""
        viking_fs = self._ensure_initialized()
        coordinator = ContentWriteCoordinator(
            viking_fs=viking_fs,
            vikingdb=self._vikingdb,
            privacy_sanitizer=self._privacy_sanitizer,
            commit_batch=(
                partial(self.compile_versions.finish_batch, ctx=ctx, edit_source=edit_source)
                if self.compile_versions is not None and edit_source != "compile"
                else None
            ),
            commit_content=(
                partial(
                    self.compile_versions.record_write,
                    ctx=ctx,
                    edit_source=edit_source,
                    record_version=False,
                )
                if self.compile_versions is not None
                else None
            ),
        )
        write = partial(
            coordinator.batch_write,
            root_uri=root_uri,
            operations=operations,
            ctx=ctx,
            wait=wait,
            skip_conflicts=skip_conflicts,
            defer_processing=defer_processing,
            processing_mode=processing_mode,
        )
        if self.compile_versions is None or edit_source == "compile":
            return await write()
        return await self.compile_versions.record_operation(
            [operation["uri"] for operation in operations],
            ctx,
            f"{edit_source}_revise",
            {"batch": True},
            write,
        )

    async def set_tags(
        self,
        uri: str,
        tags: list[str],
        mode: str,
        recursive: bool,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Set explicit retrieval tags for a file or directory semantic nodes."""
        viking_fs = self._ensure_initialized()
        coordinator = ContentWriteCoordinator(
            viking_fs=viking_fs,
            privacy_sanitizer=self._privacy_sanitizer,
        )
        return await coordinator.set_tags(
            uri=uri,
            tags=tags,
            mode=mode,
            recursive=recursive,
            ctx=ctx,
        )

    async def get_acl(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        return await self._ensure_initialized().get_acl(uri, ctx=ctx)

    async def set_acl(
        self,
        uri: str,
        entries: Optional[List[Dict[str, str]]],
        ctx: RequestContext,
        acl_mode: Optional[AclMode] = None,
    ) -> Dict[str, Any]:
        return await self._ensure_initialized().set_acl(uri, entries, ctx=ctx, acl_mode=acl_mode)

    async def grant_acl(
        self, uri: str, principal: str, level: str, ctx: RequestContext
    ) -> Dict[str, Any]:
        return await self._ensure_initialized().grant_acl(uri, principal, level, ctx=ctx)

    async def revoke_acl(self, uri: str, principal: str, ctx: RequestContext) -> Dict[str, Any]:
        return await self._ensure_initialized().revoke_acl(uri, principal, ctx=ctx)

    async def delete_acl(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        return await self._ensure_initialized().delete_acl(uri, ctx=ctx)

    async def commit(
        self,
        *,
        message: str,
        ctx: RequestContext,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Forward to VikingFS.commit. See viking_fs.commit for semantics."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.commit(
            message=message,
            paths=paths,
            branch=branch,
            author_name=author_name,
            author_email=author_email,
            ctx=ctx,
        )

    async def restore(
        self,
        *,
        project_dir: Optional[str],
        source_commit: str,
        ctx: RequestContext,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Forward to VikingFS.restore. See viking_fs.restore for semantics."""
        viking_fs = self._ensure_initialized()
        restore = partial(
            viking_fs.restore,
            project_dir=project_dir,
            source_commit=source_commit,
            branch=branch,
            dry_run=dry_run,
            message=message,
            author_name=author_name,
            author_email=author_email,
            ctx=ctx,
            record_restore=(
                partial(self.compile_versions.record_restored, ctx=ctx)
                if self.compile_versions is not None and not dry_run
                else None
            ),
        )
        if self.compile_versions is None or dry_run:
            return await restore()
        return await self.compile_versions.record_operation(
            [
                viking_fs._tree_path_to_uri(
                    viking_fs._uri_to_tree_path(project_dir, ctx=ctx)
                ).rstrip("/")
                if project_dir
                else "viking://"
            ],
            ctx,
            "restore",
            {"source_commit": source_commit, "branch": branch},
            restore,
            recursive=True,
        )

    async def show(
        self,
        target_ref: str,
        ctx: RequestContext,
        *,
        path: Optional[str] = None,
    ) -> Any:
        """Forward to VikingFS.show. Returns dict (metadata) or bytes (blob)."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.show(target_ref, path=path, ctx=ctx)

    async def show_blob_raw(
        self,
        target_ref: str,
        ctx: RequestContext,
        *,
        path: str,
    ) -> Dict[str, Any]:
        """Forward to VikingFS.show_blob_raw. Returns ``{"oid", "size", "bytes"}``."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.show_blob_raw(target_ref, path=path, ctx=ctx)

    async def diff(
        self,
        *,
        path: str,
        from_ref: Optional[str],
        to_ref: str,
        raw: bool = True,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Return a unified text diff for one path between two snapshots."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.diff(
            path=path,
            from_ref=from_ref,
            to_ref=to_ref,
            raw=raw,
            ctx=ctx,
        )

    async def diff_paths(
        self,
        paths: List[str],
        *,
        from_ref: Optional[str],
        to_ref: str,
        ctx: RequestContext,
        offset: int = 0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Page changed source URIs between snapshots; a null base means an empty tree.

        Every changed path requires current read permission, including deleted paths.
        Any denied descendant rejects the whole query without exposing its URI.
        """
        viking_fs = self._ensure_initialized()
        if not paths or not to_ref.strip() or (from_ref is not None and not from_ref.strip()):
            raise InvalidArgumentError("paths and non-empty snapshot refs are required")
        if offset < 0 or not 1 <= limit <= 1000:
            raise InvalidArgumentError("offset must be non-negative and limit must be 1..1000")
        changes = await viking_fs.diff_paths(
            dirs=list(dict.fromkeys(paths)), base=from_ref, head=to_ref, ctx=ctx
        )
        changes_by_uri = {change["uri"]: change for change in changes}
        uris = sorted(changes_by_uri)
        access = await viking_fs._can_access_many(uris, ctx, action=AclAction.READ)
        if any(not access.get(uri, False) for uri in uris):
            raise PermissionDeniedError("Snapshot changes include inaccessible paths")
        page = [changes_by_uri[uri] for uri in uris[offset : offset + limit]]
        next_offset = offset + len(page)
        has_more = next_offset < len(uris)
        return {
            "changes": page,
            "offset": offset,
            "limit": limit,
            "total": len(uris),
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
        }

    async def log(
        self,
        ctx: RequestContext,
        *,
        branch: str = "main",
        limit: int = 20,
        paths: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Forward to VikingFS.log. Walks parents[0] up to limit commits."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.log(branch=branch, limit=limit, paths=paths, ctx=ctx)

    async def get_gitignore(self, *, ctx: RequestContext) -> str:
        """Forward to VikingFS.get_gitignore. Returns the account .ovgitignore
        content, or an empty string if absent."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.get_gitignore(ctx=ctx)

    async def set_gitignore(self, *, content: str, ctx: RequestContext) -> None:
        """Forward to VikingFS.set_gitignore. Writes the account .ovgitignore
        control file (validates the size limit)."""
        viking_fs = self._ensure_initialized()
        await viking_fs.set_gitignore(content, ctx=ctx)

    async def delete_gitignore(self, *, ctx: RequestContext) -> None:
        """Forward to VikingFS.delete_gitignore. Removes the account
        .ovgitignore; missing is success."""
        viking_fs = self._ensure_initialized()
        await viking_fs.delete_gitignore(ctx=ctx)
