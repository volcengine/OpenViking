# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
File System Service for OpenViking.

Provides file system operations: ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.core.namespace import context_type_for_uri
from openviking.core.uri_validation import validate_optional_viking_uri, validate_viking_uri
from openviking.privacy import (
    UserPrivacyConfigService,
    get_skill_name_from_uri,
    restore_skill_content,
)
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.queuefs.semantic_msg import build_semantic_coalesce_key
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import build_queue_status_payload
from openviking.utils.embedding_utils import vectorize_directory_meta
from openviking_cli.exceptions import DeadlineExceededError, NotInitializedError
from openviking_cli.utils import VikingURI, get_logger

logger = get_logger(__name__)

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
    ):
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb
        self._privacy_config_service = privacy_config_service
        self._resource_memory_link_service = resource_memory_link_service
        self._watch_scheduler = watch_scheduler

    def set_dependencies(
        self,
        viking_fs: VikingFS,
        vikingdb: Optional["VikingDBManager"] = None,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
        watch_scheduler: Optional["WatchScheduler"] = None,
    ) -> None:
        """Set service dependencies (for deferred initialization)."""
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb
        self._privacy_config_service = privacy_config_service
        self._resource_memory_link_service = resource_memory_link_service
        self._watch_scheduler = watch_scheduler

    def _ensure_initialized(self) -> VikingFS:
        """Ensure VikingFS is initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")
        return self._viking_fs

    def _get_watch_manager(self) -> Optional["WatchManager"]:
        if not self._watch_scheduler:
            return None
        return self._watch_scheduler.watch_manager

    async def ls(
        self,
        uri: str,
        ctx: RequestContext,
        recursive: bool = False,
        simple: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
    ) -> List[Any]:
        """List directory contents.

        Args:
            uri: Viking URI
            recursive: List all subdirectories recursively
            simple: Return only relative path list
            output: str = "original" or "agent"
            abs_limit: int = 256 if output == "agent" else ignore
            show_all_hidden: bool = False (list all hidden files, like -a)
            node_limit: int = 1000 (maximum number of nodes to list)
            sort_by: Optional sort field for non-recursive listings
            sort_order: Sort direction, "asc" or "desc"
        """
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)

        if simple:
            # Only return URIs — skip expensive abstract fetching to save tokens
            if recursive:
                entries = await viking_fs.tree(
                    uri,
                    ctx=ctx,
                    output="original",
                    show_all_hidden=show_all_hidden,
                    node_limit=node_limit,
                    level_limit=level_limit,
                )
            else:
                entries = await viking_fs.ls(
                    uri,
                    ctx=ctx,
                    output="original",
                    show_all_hidden=show_all_hidden,
                    node_limit=node_limit,
                    sort_by=sort_by,
                    sort_order=sort_order,
                )
            return [e.get("uri", "") for e in entries]

        if recursive:
            entries = await viking_fs.tree(
                uri,
                ctx=ctx,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
                level_limit=level_limit,
            )
        else:
            entries = await viking_fs.ls(
                uri,
                ctx=ctx,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
                sort_by=sort_by,
                sort_order=sort_order,
            )
        return entries

    async def mkdir(
        self,
        uri: str,
        ctx: RequestContext,
        description: Optional[str] = None,
    ) -> None:
        """Create directory."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        await viking_fs.mkdir(uri, ctx=ctx)
        abstract = self._normalize_directory_description(description)
        if not abstract:
            return

        directory_uri, abstract_uri = self._resolve_directory_uris(uri)
        await viking_fs.write_file(abstract_uri, abstract, ctx=ctx)
        await vectorize_directory_meta(
            uri=directory_uri,
            abstract=abstract,
            overview="",
            context_type=context_type_for_uri(directory_uri),
            ctx=ctx,
            include_overview=False,
        )

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
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Remove resource."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        cleanup_result: Optional[Dict[str, Any]] = None
        context_type = context_type_for_uri(uri)
        refresh_parent_uri = self._semantic_refresh_parent_uri(uri, context_type)
        memory_overview_uri = self._memory_overview_parent_uri(uri, context_type)
        result = await viking_fs.rm(uri, recursive=recursive, ctx=ctx)
        await self._sync_watch_after_rm(uri, context_type=context_type)
        queue_status = None
        request_registered = False
        telemetry_id = get_current_telemetry().telemetry_id
        try:
            if refresh_parent_uri:
                if wait and telemetry_id:
                    get_request_wait_tracker().register_request(telemetry_id)
                    request_registered = True
                await self._enqueue_delete_refresh(
                    root_uri=refresh_parent_uri,
                    deleted_uri=uri,
                    context_type=context_type,
                    ctx=ctx,
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
            if refresh_parent_uri and wait:
                queue_status = await self._wait_for_refresh(timeout=timeout)
        finally:
            if request_registered:
                get_request_wait_tracker().cleanup(telemetry_id)
        if cleanup_result is not None and isinstance(result, dict):
            result["memory_cleanup"] = cleanup_result
        if refresh_parent_uri and isinstance(result, dict):
            result["semantic_root_uri"] = refresh_parent_uri
            result["semantic_status"] = self._semantic_refresh_status(
                wait=wait,
                queue_status=queue_status,
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
        if context_type != "resource":
            return None
        parent = VikingURI(uri).parent
        return parent.uri if parent else None

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
    ) -> None:
        try:
            queue_manager = get_queue_manager()
        except RuntimeError as exc:
            logger.warning("QueueManager not available, skipping delete refresh: %s", exc)
            return
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
            changes={"deleted": [deleted_uri]},
        )
        if telemetry_id:
            get_request_wait_tracker().register_semantic_root(telemetry_id, msg.id)
        try:
            await semantic_queue.enqueue(msg)
        except Exception as exc:
            if telemetry_id:
                get_request_wait_tracker().mark_semantic_failed(telemetry_id, msg.id, str(exc))
            raise

    async def _wait_for_refresh(self, *, timeout: Optional[float]) -> Dict[str, Any]:
        telemetry_id = get_current_telemetry().telemetry_id
        if telemetry_id:
            try:
                await get_request_wait_tracker().wait_for_request(telemetry_id, timeout=timeout)
            except TimeoutError as exc:
                raise DeadlineExceededError("queue processing", timeout) from exc
            return get_request_wait_tracker().build_queue_status(telemetry_id)
        try:
            return build_queue_status_payload(
                await get_queue_manager().wait_complete(timeout=timeout)
            )
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc

    async def mv(self, from_uri: str, to_uri: str, ctx: RequestContext) -> None:
        """Move resource."""
        from_uri = validate_viking_uri(from_uri, field_name="from_uri")
        to_uri = validate_viking_uri(to_uri, field_name="to_uri")
        viking_fs = self._ensure_initialized()
        watch_manager = self._get_watch_manager()
        if not watch_manager or context_type_for_uri(from_uri) != "resource":
            await viking_fs.mv(from_uri, to_uri, ctx=ctx)
            return
        if context_type_for_uri(to_uri) != "resource":
            await viking_fs.mv(from_uri, to_uri, ctx=ctx)
            return
        if is_watch_task_control_uri(from_uri) or is_watch_task_control_uri(to_uri):
            await viking_fs.mv(from_uri, to_uri, ctx=ctx)
            return

        await watch_manager.sync_tasks_with_resource_move_internal(
            from_uri,
            to_uri,
            move_resource=lambda: viking_fs.mv(from_uri, to_uri, ctx=ctx),
            rollback_resource=lambda: viking_fs.mv(to_uri, from_uri, ctx=ctx),
        )

    async def _sync_watch_after_rm(self, uri: str, *, context_type: str) -> None:
        if context_type != "resource":
            return
        if is_watch_task_control_uri(uri):
            return
        watch_manager = self._get_watch_manager()
        if not watch_manager:
            return
        deactivated = await watch_manager.deactivate_tasks_under_uri_internal(uri)
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
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.tree(
            uri,
            ctx=ctx,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
            level_limit=level_limit,
        )

    async def stat(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        """Get resource status."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.stat(uri, ctx=ctx)

    async def system_sync_status(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        """Return multi-write sync status for one Viking URI subtree."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.system_sync_status(uri, ctx=ctx)

    async def system_sync_retry(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        """Retry multi-write sync work for one Viking URI subtree."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.system_sync_retry(uri, ctx=ctx)

    async def read(self, uri: str, ctx: RequestContext, offset: int = 0, limit: int = -1) -> str:
        """Read file content."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        content = await viking_fs.read_file(uri, ctx=ctx)
        skill_name = get_skill_name_from_uri(uri)
        if skill_name and self._privacy_config_service:
            current = await self._privacy_config_service.get_current(
                ctx=ctx,
                category="skill",
                target_key=skill_name,
            )
            if current:
                content = restore_skill_content(content, skill_name, current.values)

        if offset == 0 and limit == -1:
            return content
        lines = content.splitlines(keepends=True)
        sliced = lines[offset:] if limit == -1 else lines[offset : offset + limit]
        return "".join(sliced)

    async def abstract(self, uri: str, ctx: RequestContext) -> str:
        """Read L0 abstract (.abstract.md)."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.abstract(uri, ctx=ctx)

    async def overview(self, uri: str, ctx: RequestContext) -> str:
        """Read L1 overview (.overview.md)."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
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
    ) -> Dict:
        """Content search."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        exclude_uri = validate_optional_viking_uri(exclude_uri, field_name="exclude_uri") or None
        return await viking_fs.grep(
            uri,
            pattern,
            exclude_uri=exclude_uri,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
        )

    async def glob(
        self,
        pattern: str,
        ctx: RequestContext,
        uri: str = "viking://",
        node_limit: Optional[int] = None,
    ) -> Dict:
        """File pattern matching."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.glob(pattern, uri=uri, node_limit=node_limit, ctx=ctx)

    async def read_file_bytes(self, uri: str, ctx: RequestContext) -> bytes:
        """Read file as raw bytes."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.read_file_bytes(uri, ctx=ctx)

    async def write(
        self,
        uri: str,
        content: str,
        ctx: RequestContext,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Write to an existing file and refresh semantics/vectors."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        coordinator = ContentWriteCoordinator(viking_fs=viking_fs, vikingdb=self._vikingdb)
        return await coordinator.write(
            uri=uri,
            content=content,
            ctx=ctx,
            mode=mode,
            wait=wait,
            timeout=timeout,
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
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
        return await coordinator.set_tags(
            uri=uri,
            tags=tags,
            mode=mode,
            recursive=recursive,
            ctx=ctx,
        )

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
        validated = (
            [validate_viking_uri(p) for p in paths] if paths is not None else None
        )
        return await viking_fs.commit(
            message=message,
            paths=validated,
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
        if project_dir is not None:
            project_dir = validate_viking_uri(project_dir, field_name="project_dir")
        return await viking_fs.restore(
            project_dir=project_dir,
            source_commit=source_commit,
            branch=branch,
            dry_run=dry_run,
            message=message,
            author_name=author_name,
            author_email=author_email,
            ctx=ctx,
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
        # validate_optional_viking_uri returns "" for None input; VikingFS.show needs None.
        path = validate_optional_viking_uri(path, field_name="path") or None
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
        path = validate_viking_uri(path, field_name="path")
        return await viking_fs.show_blob_raw(target_ref, path=path, ctx=ctx)

    async def log(
        self,
        ctx: RequestContext,
        *,
        branch: str = "main",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Forward to VikingFS.log. Walks parents[0] up to limit commits."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.log(branch=branch, limit=limit, ctx=ctx)

    async def get_gitignore(self, *, ctx: RequestContext) -> str:
        """Forward to VikingFS.get_gitignore. Returns the account .ovgitignore
        content, or an empty string if absent."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.get_gitignore(ctx=ctx)

    async def set_gitignore(
        self, *, content: str, ctx: RequestContext
    ) -> None:
        """Forward to VikingFS.set_gitignore. Writes the account .ovgitignore
        control file (validates the size limit)."""
        viking_fs = self._ensure_initialized()
        await viking_fs.set_gitignore(content, ctx=ctx)

    async def delete_gitignore(self, *, ctx: RequestContext) -> None:
        """Forward to VikingFS.delete_gitignore. Removes the account
        .ovgitignore; missing is success."""
        viking_fs = self._ensure_initialized()
        await viking_fs.delete_gitignore(ctx=ctx)
