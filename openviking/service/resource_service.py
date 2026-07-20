# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Resource Service for OpenViking.

Provides resource management operations: add_resource, add_skill, wait_processed.
"""

import asyncio
import contextlib
import inspect
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

import httpx

from openviking.connector.client import ConnectorClient
from openviking.core.content_targets import ContentTargetSpec
from openviking.core.uri_validation import validate_optional_content_target_uri
from openviking.resource.feishu_watch_auth import (
    FEISHU_ACCESS_TOKEN_ARG,
    FEISHU_REFRESH_TOKEN_ARG,
    create_feishu_auth_state,
    load_feishu_app_credentials,
)
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import (
    is_remote_resource_source,
    require_remote_resource_source,
)
from openviking.server.user_config import (
    effective_resource_add_target,
    effective_skill_add_target,
)
from openviking.storage import VikingDBManager
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.transaction import NO_LOCK, LockLease
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import (
    build_queue_status_payload,
    record_resource_wait_metrics,
    register_wait_telemetry,
    unregister_wait_telemetry,
)
from openviking.utils import is_git_repo_url, parse_code_hosting_url
from openviking.utils.media_processor import _smart_stem
from openviking.utils.network_guard import ensure_public_remote_target
from openviking.utils.resource_processor import ResourceProcessor
from openviking.utils.skill_processor import SkillProcessingPreparation, SkillProcessor
from openviking_cli.exceptions import (
    ConflictError,
    DeadlineExceededError,
    InternalError,
    InvalidArgumentError,
    NotInitializedError,
)
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.resource.watch_manager import WatchManager
    from openviking.resource.watch_scheduler import WatchScheduler
    from openviking.service.resource_memory_link_service import ResourceMemoryLinkService

logger = get_logger(__name__)


_ADD_RESOURCE_ARGS_RESERVED_FIELDS = frozenset(
    {
        "path",
        "ctx",
        "to",
        "parent",
        "reason",
        "instruction",
        "wait",
        "timeout",
        "build_index",
        "summarize",
        "watch_interval",
        "skip_watch_management",
        "allow_local_path_resolution",
        "enforce_public_remote_targets",
        "resource_lock",
        "stage_callback",
        "args",
        "strict",
        "source_name",
        "ignore_dirs",
        "include",
        "exclude",
        "directly_upload_media",
        "preserve_structure",
        "create_parent",
        "telemetry",
        "request_validator",
        "defer_post_processing",
    }
)


@dataclass
class _ResourceSourceInfo:
    source_name: Optional[str] = None
    source_path: Optional[str] = None
    source_format: Optional[str] = None


@dataclass
class _NormalizedAddResourceArgs:
    processor_kwargs: Dict[str, Any]
    watch_auth_state: Optional[Dict[str, Any]] = None


class ResourceService:
    """Resource management service."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        resource_processor: Optional[ResourceProcessor] = None,
        skill_processor: Optional[SkillProcessor] = None,
        watch_scheduler: Optional["WatchScheduler"] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor
        self._watch_scheduler = watch_scheduler
        self._resource_memory_link_service = resource_memory_link_service
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        viking_fs: VikingFS,
        resource_processor: ResourceProcessor,
        skill_processor: SkillProcessor,
        watch_scheduler: Optional["WatchScheduler"] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
    ) -> None:
        """Set dependencies (for deferred initialization)."""
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor
        self._watch_scheduler = watch_scheduler
        self._resource_memory_link_service = resource_memory_link_service

    def _get_watch_manager(self) -> Optional["WatchManager"]:
        if not self._watch_scheduler:
            return None
        return self._watch_scheduler.watch_manager

    def _get_parser_router(self):
        if not hasattr(self, "_parser_router"):
            from openviking.parse.parser_router import ParserRouter
            from openviking.parse.registry import get_registry

            self._parser_router = ParserRouter(get_registry())
        return self._parser_router

    def _sanitize_watch_processor_kwargs(self, processor_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        for key, value in processor_kwargs.items():
            try:
                json.dumps(value, ensure_ascii=False)
            except TypeError:
                continue
            sanitized[key] = value
        return sanitized

    async def _manage_watch_if_needed(
        self,
        *,
        watch_manager: Optional["WatchManager"],
        skip_watch_management: bool,
        watch_interval: float,
        target: ContentTargetSpec,
        root_uri: str,
        path: str,
        reason: str,
        instruction: str,
        build_index: bool,
        summarize: bool,
        processor_kwargs: Dict[str, Any],
        watch_auth_state: Optional[Dict[str, Any]],
        ctx: RequestContext,
    ) -> None:
        if not watch_manager or skip_watch_management:
            return
        telemetry = get_current_telemetry()
        with telemetry.measure("resource.watch"):
            if watch_interval > 0:
                watch_to = target.to
                parent_uri = target.parent
                if not watch_to:
                    watch_to = validate_optional_content_target_uri(
                        root_uri,
                        ctx,
                        kind="resource",
                        field_name="root_uri",
                    )
                    parent_uri = None
                if not watch_to:
                    raise InvalidArgumentError(
                        "watch_interval > 0 requires a stable target URI. "
                        "Pass 'to' explicitly, or add a resource type that returns root_uri."
                    )
                if processor_kwargs.get("temp_file_id"):
                    # An uploaded source is a one-time snapshot: the staged upload is
                    # consumed at ingest, so a watch task recorded against it would
                    # re-process the frozen snapshot every interval — silently ignoring
                    # all edits to the client-side source — instead of watching anything
                    # live. Reject at creation instead of pretending to watch.
                    raise InvalidArgumentError(
                        "watch_interval > 0 is not supported for uploaded content: an "
                        "upload is consumed as a one-time snapshot at ingest, so the "
                        "watch would re-process stale content forever. Watch a URL / "
                        "sitemap / RSS source instead, or re-add the resource when the "
                        "source changes."
                    )
                try:
                    sanitized = self._sanitize_watch_processor_kwargs(processor_kwargs)
                    if watch_auth_state is not None:
                        sanitized.pop(FEISHU_ACCESS_TOKEN_ARG, None)
                    await self._handle_watch_task_creation(
                        path=path,
                        to_uri=watch_to,
                        parent_uri=parent_uri,
                        reason=reason,
                        instruction=instruction,
                        watch_interval=watch_interval,
                        build_index=build_index,
                        summarize=summarize,
                        processor_kwargs=sanitized,
                        auth_state=watch_auth_state,
                        ctx=ctx,
                    )
                except ConflictError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"[ResourceService] Failed to create watch task for {watch_to}: {e}"
                    )
            elif target.to:
                try:
                    await self._handle_watch_task_cancellation(to_uri=target.to, ctx=ctx)
                except Exception as e:
                    logger.warning(
                        f"[ResourceService] Failed to cancel watch task for {target.to}: {e}"
                    )

    def _normalize_add_resource_args(
        self,
        args: Optional[Dict[str, Any]],
        *,
        watch_interval: float,
    ) -> _NormalizedAddResourceArgs:
        if args is None:
            return _NormalizedAddResourceArgs({})
        if not isinstance(args, dict):
            raise InvalidArgumentError("args must be an object.")
        if not args:
            return _NormalizedAddResourceArgs({})

        reserved = sorted(set(args).intersection(_ADD_RESOURCE_ARGS_RESERVED_FIELDS))
        if reserved:
            raise InvalidArgumentError(
                "args cannot contain core add_resource fields: " + ", ".join(reserved)
            )

        normalized = dict(args)
        token = normalized.get(FEISHU_ACCESS_TOKEN_ARG)
        refresh_token = normalized.pop(FEISHU_REFRESH_TOKEN_ARG, None)
        watch_auth_state = None
        if token is not None:
            if not isinstance(token, str) or not token.strip():
                raise InvalidArgumentError("args.feishu_access_token must be a non-empty string.")
            token = token.strip()
            normalized[FEISHU_ACCESS_TOKEN_ARG] = token
            if watch_interval > 0:
                if not isinstance(refresh_token, str) or not refresh_token.strip():
                    raise InvalidArgumentError(
                        "args.feishu_refresh_token must be a non-empty string when "
                        "args.feishu_access_token is used with watch_interval > 0."
                    )
                self._ensure_feishu_credentials_for_watch()
                watch_auth_state = create_feishu_auth_state(token, refresh_token.strip())
            elif refresh_token is not None:
                raise InvalidArgumentError(
                    "args.feishu_refresh_token is only supported with "
                    "args.feishu_access_token and watch_interval > 0."
                )
        elif refresh_token is not None:
            raise InvalidArgumentError(
                "args.feishu_refresh_token requires args.feishu_access_token."
            )

        return _NormalizedAddResourceArgs(normalized, watch_auth_state)

    def _ensure_feishu_credentials_for_watch(self) -> None:
        try:
            load_feishu_app_credentials()
        except Exception as exc:
            raise InvalidArgumentError(
                "Feishu user-token watch requires FEISHU_APP_ID and "
                "FEISHU_APP_SECRET, or feishu.app_id and feishu.app_secret in ov.conf."
            ) from exc

    def _ensure_initialized(self) -> None:
        """Ensure all dependencies are initialized."""
        if not self._resource_processor:
            raise NotInitializedError("ResourceProcessor")
        if not self._skill_processor:
            raise NotInitializedError("SkillProcessor")
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")

    async def close_background_tasks(self) -> None:
        """Cancel in-flight connector monitoring tasks during service shutdown."""
        if not self._background_tasks:
            return
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def _enqueue_add_resource_job(
        self,
        msg: Any,
        *,
        resource_lock: LockLease = NO_LOCK,
    ) -> Any:
        """Persist a job before its TaskRecord so a crash cannot orphan the task."""
        from openviking.service.task_tracker import get_task_tracker
        from openviking.storage.queuefs import QueueManager, get_queue_manager

        try:
            await get_queue_manager().enqueue(QueueManager.ADD_RESOURCE, msg.to_dict())
            await resource_lock.handoff()
        except BaseException:
            await resource_lock.close()
            raise

        tracker = get_task_tracker()
        task = await tracker.create(
            "add_resource",
            resource_id=msg.root_uri,
            account_id=msg.account_id,
            user_id=msg.user_id,
            task_id=msg.task_id,
        )
        await tracker.update_stage(
            task.task_id,
            "queued",
            account_id=msg.account_id,
            user_id=msg.user_id,
        )
        return task

    async def execute_add_resource_job(
        self,
        msg: Any,
        *,
        ctx: RequestContext,
        resource_lock: Optional[LockLease],
        stage_callback: Callable[[str], Any],
    ) -> Dict[str, Any]:
        """Execute one durable add-resource job inside its QueueFS consumer."""
        resource_lock = resource_lock or NO_LOCK
        if msg.prepared is None:
            return await self.add_resource(
                path=msg.path,
                ctx=ctx,
                to=msg.root_uri,
                reason=msg.reason,
                instruction=msg.instruction,
                wait=True,
                timeout=msg.timeout,
                build_index=msg.build_index,
                summarize=msg.summarize,
                watch_interval=msg.watch_interval,
                skip_watch_management=msg.skip_watch_management,
                allow_local_path_resolution=msg.allow_local_path_resolution,
                enforce_public_remote_targets=msg.enforce_public_remote_targets,
                resource_lock=resource_lock,
                stage_callback=stage_callback,
                strict=msg.strict,
                source_name=msg.source_name,
                ignore_dirs=msg.ignore_dirs,
                include=msg.include,
                exclude=msg.exclude,
                directly_upload_media=msg.directly_upload_media,
                preserve_structure=msg.preserve_structure,
                create_parent=msg.create_parent,
                args=msg.args,
            )

        telemetry_id = get_current_telemetry().telemetry_id
        request_wait_tracker = get_request_wait_tracker()
        request_wait_tracker.register_request(telemetry_id)
        try:
            stage_result = stage_callback("processing_queue")
            if inspect.isawaitable(stage_result):
                await stage_result
            result = await self._resource_processor.finish_prepared_resource(
                msg.prepared,
                ctx=ctx,
                resource_lock=resource_lock,
                summarize=msg.summarize,
                build_index=msg.build_index,
            )
            await request_wait_tracker.wait_for_request(
                telemetry_id,
                timeout=msg.timeout,
            )
            status = request_wait_tracker.build_queue_status(telemetry_id)
            result["queue_status"] = status
            await self._link_resource_reason_memory(
                result=result,
                ctx=ctx,
                reason=msg.reason,
                source_name=msg.source_name,
                timeout=msg.timeout,
            )
            return result
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", msg.timeout) from exc
        finally:
            request_wait_tracker.cleanup(telemetry_id)
            unregister_wait_telemetry(telemetry_id)

    async def reacquire_add_resource_job_lock(
        self,
        root_uri: str,
        ctx: RequestContext,
    ) -> LockLease:
        """Acquire a fresh lock when a recovered job's old handoff was released."""
        if not self._resource_processor or not self._viking_fs:
            raise NotInitializedError("ResourceProcessor")
        from openviking.storage.transaction import get_lock_manager

        dst_path = self._viking_fs._uri_to_path(root_uri, ctx=ctx)
        return await self._resource_processor.acquire_resource_lock(
            get_lock_manager(),
            dst_path,
            uri=root_uri,
            timeout=0.0,
        )

    async def enqueue_git_add_resource(
        self,
        path: str,
        ctx: RequestContext,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        timeout: Optional[float] = None,
        build_index: bool = True,
        summarize: bool = False,
        watch_interval: float = 0,
        skip_watch_management: bool = False,
        allow_local_path_resolution: bool = True,
        enforce_public_remote_targets: bool = False,
        args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Start background ingestion for Git repositories while reserving the target URI."""
        self._ensure_initialized()
        normalized_args = self._normalize_add_resource_args(args, watch_interval=watch_interval)
        kwargs.update(normalized_args.processor_kwargs)

        target = ContentTargetSpec.from_fields(
            ctx=ctx,
            kind="resource",
            to=to,
            parent=parent,
            create_parent=bool(kwargs.get("create_parent", False)),
        )

        from openviking.storage.queuefs.add_resource_msg import AddResourceMsg

        resource_lock: LockLease = NO_LOCK
        try:
            if enforce_public_remote_targets and is_remote_resource_source(path):
                path = require_remote_resource_source(path)
                kwargs.setdefault("request_validator", ensure_public_remote_target)

            source_info = await self._preflight_git_source(path)
            source_name = kwargs.get("source_name") or source_info.source_name
            if source_name:
                kwargs["source_name"] = source_name
            root_uri, resource_lock = await self._plan_resource_target(
                path=path,
                ctx=ctx,
                target=target,
                source_name=source_name,
                source_info=source_info,
            )

            task_id = str(uuid4())
            lock_handoff = resource_lock.to_handoff()
            processor_args = {
                key: value
                for key, value in kwargs.items()
                if key not in _ADD_RESOURCE_ARGS_RESERVED_FIELDS
            }
            msg = AddResourceMsg(
                task_id=task_id,
                path=path,
                root_uri=root_uri,
                telemetry_id=get_current_telemetry().telemetry_id or None,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                role=str(ctx.role),
                actor_peer_id=ctx.actor_peer_id,
                lock_handoff=lock_handoff.to_dict() if lock_handoff else None,
                reason=reason,
                instruction=instruction,
                timeout=timeout,
                build_index=build_index,
                summarize=summarize,
                watch_interval=watch_interval,
                skip_watch_management=skip_watch_management,
                allow_local_path_resolution=allow_local_path_resolution,
                enforce_public_remote_targets=enforce_public_remote_targets,
                strict=bool(kwargs.get("strict", False)),
                ignore_dirs=kwargs.get("ignore_dirs"),
                include=kwargs.get("include"),
                exclude=kwargs.get("exclude"),
                directly_upload_media=bool(kwargs.get("directly_upload_media", True)),
                preserve_structure=kwargs.get("preserve_structure"),
                create_parent=bool(kwargs.get("create_parent", False)),
                source_name=source_name,
                args=self._sanitize_watch_processor_kwargs(processor_args),
            )
            task = await self._enqueue_add_resource_job(msg, resource_lock=resource_lock)
            resource_lock = NO_LOCK
            return {
                "status": "success",
                "root_uri": root_uri,
                "task_id": task.task_id,
            }
        except Exception:
            await resource_lock.close()
            raise

    async def _plan_resource_target(
        self,
        *,
        path: str,
        ctx: RequestContext,
        target: ContentTargetSpec,
        source_name: Optional[str],
        source_info: _ResourceSourceInfo,
    ) -> tuple[str, LockLease]:
        if not self._resource_processor or not self._viking_fs:
            raise NotInitializedError("ResourceProcessor")

        doc_name = self._target_doc_name(path, source_name, source_info)
        source_path = source_info.source_path or source_name or path
        root_uri, candidate_uri = await self._resource_processor.tree_builder.resolve_target_uri(
            ctx=ctx,
            doc_name=doc_name,
            scope="resources",
            to_uri=target.to,
            parent_uri=target.parent,
            source_path=source_path,
            source_format=source_info.source_format,
            create_parent=target.create_parent,
        )
        if candidate_uri:
            return await self._resource_processor.reserve_unique_candidate(
                candidate_uri=candidate_uri,
                ctx=ctx,
            )

        from openviking.storage.transaction import get_lock_manager

        dst_path = self._viking_fs._uri_to_path(root_uri, ctx=ctx)
        resource_lock = await self._resource_processor.acquire_resource_lock(
            get_lock_manager(),
            dst_path,
            uri=root_uri,
            timeout=0.0,
        )
        return root_uri, resource_lock

    def _should_use_understanding_api(self, path: str) -> bool:
        return self._get_parser_router().should_use_understanding_api(path)

    @staticmethod
    def _target_doc_name(
        path: str,
        source_name: Optional[str],
        source_info: _ResourceSourceInfo,
    ) -> str:
        if source_name:
            return _smart_stem(source_name)
        if source_info.source_name:
            return _smart_stem(source_info.source_name)
        if source_info.source_format == "repository":
            parsed = parse_code_hosting_url(path)
            if parsed:
                return parsed.rsplit("/", 1)[-1]
        return _smart_stem(Path(path).name or "resource")

    async def _preflight_git_source(self, source: str) -> _ResourceSourceInfo:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "ls-remote",
                "--heads",
                source,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except TimeoutError as exc:
            with contextlib.suppress(Exception):
                proc.kill()  # type: ignore[possibly-undefined]
                await proc.communicate()  # type: ignore[possibly-undefined]
            raise InvalidArgumentError(
                f"Cannot access Git repository: {source}. The check timed out after 10s."
            ) from exc
        except Exception as exc:
            raise InvalidArgumentError(f"Cannot access Git repository: {source}. {exc}") from exc

        if proc.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
            raise InvalidArgumentError(
                f"Cannot access Git repository: {source}. {detail or 'git ls-remote failed'}"
            )
        repo_name = parse_code_hosting_url(source)
        return _ResourceSourceInfo(
            source_name=repo_name.rsplit("/", 1)[-1] if repo_name else None,
            source_path=source,
            source_format="repository",
        )

    async def add_resource(
        self,
        path: str,
        ctx: RequestContext,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        build_index: bool = True,
        summarize: bool = False,
        watch_interval: float = 0,
        skip_watch_management: bool = False,
        allow_local_path_resolution: bool = True,
        enforce_public_remote_targets: bool = False,
        resource_lock: Optional[LockLease] = None,
        stage_callback: Optional[Callable[[str], Any]] = None,
        args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking (only supports resources scope).

        Args:
            path: Resource path (local file or URL)
            to: Target URI (e.g., "viking://resources/my_resource")
            parent: Parent URI under which the resource will be stored
            reason: Reason for adding the resource
            instruction: Processing instruction for semantic extraction
            wait: Whether to wait for semantic extraction and vectorization to complete
            timeout: Wait timeout in seconds
            build_index: Whether to build vector index immediately (default: True)
            summarize: Whether to generate summary (default: False)
            watch_interval: Watch interval in minutes for automatic resource monitoring.
                - watch_interval > 0: Creates or updates a watch task. The resource will be
                  automatically re-processed at the specified interval by the scheduler.
                - watch_interval = 0: No watch task is created. If a watch task exists for
                  this resource, it will be cancelled (deactivated).
                - watch_interval < 0: Same as watch_interval = 0, cancels any existing watch task.
                Default is 0 (no monitoring).

                Note: If the target URI already has an active watch task, a ConflictError will be
                raised. You must first cancel the existing watch (set watch_interval <= 0) before
                creating a new one.
            skip_watch_management: If True, skip watch task management (used by scheduler to
                avoid recursive watch task creation during scheduled execution)
            enforce_public_remote_targets: When True, reject non-public remote hosts and
                validate each outbound HTTP request URL during fetch.
            args: Parser/accessor-specific options forwarded to the processing chain.
            **kwargs: Extra options forwarded to the parser chain

        Returns:
            Processing result containing 'root_uri' and other metadata

        Raises:
            ConflictError: If the target URI already has an active watch task
            InvalidArgumentError: If the URI scope is not 'resources'
        """
        self._ensure_initialized()
        normalized_args = self._normalize_add_resource_args(args, watch_interval=watch_interval)
        kwargs.update(normalized_args.processor_kwargs)
        if watch_interval > 0 and kwargs.get("temp_file_id"):
            # Fail fast, before any ingestion: an uploaded source is a one-time
            # snapshot, so a watch on it can never observe the live source (see the
            # matching guard in _manage_watch_if_needed, the watch-creation choke
            # point that protects all other call paths).
            raise InvalidArgumentError(
                "watch_interval > 0 is not supported for uploaded content: an "
                "upload is consumed as a one-time snapshot at ingest, so the "
                "watch would re-process stale content forever. Watch a URL / "
                "sitemap / RSS source instead, or re-add the resource when the "
                "source changes."
            )
        if not to and not parent:
            from openviking.server.dependencies import get_server_config

            default_parent = await effective_resource_add_target(
                viking_fs=self._viking_fs,
                ctx=ctx,
                server_config=get_server_config(),
            )
            if default_parent:
                parent = default_parent
                kwargs["create_parent"] = True

        if self._should_use_connector(
            path,
            ctx=ctx,
            to=to,
            parent=parent,
            wait=wait,
            reason=reason,
            instruction=instruction,
            build_index=build_index,
            summarize=summarize,
            watch_interval=watch_interval,
            connector_args=args or {},
            kwargs=kwargs,
        ):
            return await self._add_resource_via_connector(
                path=path,
                ctx=ctx,
                parent=parent,
                **kwargs,
            )

        if not wait and is_git_repo_url(path):
            return await self.enqueue_git_add_resource(
                path=path,
                ctx=ctx,
                to=to,
                parent=parent,
                reason=reason,
                instruction=instruction,
                timeout=timeout,
                build_index=build_index,
                summarize=summarize,
                watch_interval=watch_interval,
                skip_watch_management=skip_watch_management,
                allow_local_path_resolution=allow_local_path_resolution,
                enforce_public_remote_targets=enforce_public_remote_targets,
                **kwargs,
            )

        request_start = time.perf_counter()
        telemetry = get_current_telemetry()
        telemetry_id = register_wait_telemetry(wait)
        request_wait_tracker = get_request_wait_tracker()
        job_enqueued = False
        deferred_lock: LockLease = NO_LOCK
        if telemetry_id:
            request_wait_tracker.register_request(telemetry_id)
        watch_manager = self._get_watch_manager()
        watch_enabled = bool(watch_manager and not skip_watch_management and watch_interval > 0)

        telemetry.set("resource.flags.wait", wait)
        telemetry.set("resource.flags.build_index", build_index)
        telemetry.set("resource.flags.summarize", summarize)
        telemetry.set("resource.flags.watch_enabled", watch_enabled)

        try:
            target = ContentTargetSpec.from_fields(
                ctx=ctx,
                kind="resource",
                to=to,
                parent=parent,
                create_parent=bool(kwargs.get("create_parent", False)),
            )
            if enforce_public_remote_targets and is_remote_resource_source(path):
                path = require_remote_resource_source(path)
                kwargs.setdefault("request_validator", ensure_public_remote_target)
            if resource_lock is not None:
                kwargs["resource_lock"] = resource_lock

            if (
                not wait
                and not is_git_repo_url(path)
                and self._should_use_understanding_api(path)
                and not allow_local_path_resolution
                and self._resource_processor is not None
            ):
                from openviking.storage.queuefs.add_resource_msg import AddResourceMsg

                source_name = kwargs.get("source_name")
                source_info = _ResourceSourceInfo(
                    source_name=source_name,
                    source_path=path,
                    source_format="file",
                )
                doc_name = self._target_doc_name(path, source_name, source_info)
                source_path = source_info.source_path or source_name or path
                (
                    root_uri,
                    candidate_uri,
                ) = await self._resource_processor.tree_builder.resolve_target_uri(
                    ctx=ctx,
                    doc_name=doc_name,
                    scope="resources",
                    to_uri=target.to,
                    parent_uri=target.parent,
                    source_path=source_path,
                    source_format=source_info.source_format,
                    create_parent=target.create_parent,
                )
                if self._viking_fs is None:
                    raise NotInitializedError("VikingFS")
                from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
                from openviking.storage.transaction import OwnedLockLease, get_lock_manager

                lock_manager = get_lock_manager()
                lock_lease: LockLease = NO_LOCK

                async def _reserve_tree(uri: str) -> LockLease:
                    dst_path = self._viking_fs._uri_to_path(uri, ctx=ctx)
                    try:
                        return await OwnedLockLease.acquire_tree(
                            lock_manager, dst_path, timeout=0.0
                        )
                    except LockAcquisitionError as exc:
                        raise ResourceBusyError(
                            f"Resource is busy: {uri}",
                            uri=uri,
                            conflict_type="path_busy",
                            retryable=True,
                        ) from exc

                if candidate_uri:
                    root_uri, lock_lease = await self._resource_processor.reserve_unique_candidate(
                        candidate_uri=candidate_uri,
                        ctx=ctx,
                    )
                else:
                    lock_lease = await _reserve_tree(root_uri)

                lock_handoff = lock_lease.to_handoff()
                msg = AddResourceMsg(
                    task_id=str(uuid4()),
                    telemetry_id=telemetry_id or None,
                    path=path,
                    root_uri=root_uri,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    role=str(ctx.role),
                    actor_peer_id=ctx.actor_peer_id,
                    reason=reason,
                    instruction=instruction,
                    timeout=timeout,
                    build_index=build_index,
                    summarize=summarize,
                    strict=bool(kwargs.get("strict", False)),
                    ignore_dirs=kwargs.get("ignore_dirs"),
                    include=kwargs.get("include"),
                    exclude=kwargs.get("exclude"),
                    directly_upload_media=bool(kwargs.get("directly_upload_media", True)),
                    preserve_structure=kwargs.get("preserve_structure"),
                    create_parent=bool(kwargs.get("create_parent", False)),
                    allow_local_path_resolution=allow_local_path_resolution,
                    enforce_public_remote_targets=enforce_public_remote_targets,
                    args=self._sanitize_watch_processor_kwargs(normalized_args.processor_kwargs),
                    source_name=source_name,
                    lock_handoff=lock_handoff.to_dict() if lock_handoff else None,
                    skip_watch_management=True,
                )
                task = await self._enqueue_add_resource_job(msg, resource_lock=lock_lease)
                lock_lease = NO_LOCK
                job_enqueued = True
                logger.info(
                    "[ResourceService] Enqueued AddResourceMsg task_id=%s root_uri=%s",
                    task.task_id,
                    root_uri,
                )
                await self._manage_watch_if_needed(
                    watch_manager=watch_manager,
                    skip_watch_management=skip_watch_management,
                    watch_interval=watch_interval,
                    target=target,
                    root_uri=root_uri,
                    path=path,
                    reason=reason,
                    instruction=instruction,
                    build_index=build_index,
                    summarize=summarize,
                    processor_kwargs=kwargs,
                    watch_auth_state=normalized_args.watch_auth_state,
                    ctx=ctx,
                )
                return {
                    "status": "success",
                    "root_uri": root_uri,
                    "task_id": task.task_id,
                }

            result = await self._resource_processor.process_resource(
                path=path,
                ctx=ctx,
                reason=reason,
                instruction=instruction,
                scope="resources",
                to=target.to,
                parent=target.parent,
                build_index=build_index,
                summarize=summarize,
                stage_callback=stage_callback,
                allow_local_path_resolution=allow_local_path_resolution,
                defer_post_processing=not wait,
                **kwargs,
            )

            if result.get("status") == "error":
                return result
            prepared = result.pop("_post_process", None)
            deferred_lock = result.pop("_resource_lock", NO_LOCK)
            if wait:
                if stage_callback is not None:
                    stage_result = stage_callback("processing_queue")
                    if inspect.isawaitable(stage_result):
                        await stage_result
                wait_start = time.perf_counter()
                try:
                    with telemetry.measure("resource.wait"):
                        if telemetry_id:
                            await request_wait_tracker.wait_for_request(
                                telemetry_id,
                                timeout=timeout,
                                poll_interval=0.05,
                            )
                            status = request_wait_tracker.build_queue_status(telemetry_id)
                        else:
                            qm = get_queue_manager()
                            status = build_queue_status_payload(
                                await qm.wait_complete(timeout=timeout)
                            )
                except TimeoutError as exc:
                    telemetry.set_error(
                        "resource_service.wait_complete",
                        "DEADLINE_EXCEEDED",
                        str(exc),
                    )
                    raise DeadlineExceededError("queue processing", timeout) from exc
                queue_wait_duration_ms = round((time.perf_counter() - wait_start) * 1000, 3)
                try:
                    from openviking.metrics.datasources.resource import (
                        ResourceIngestionEventDataSource,
                    )

                    ResourceIngestionEventDataSource.record_wait(
                        operation="queue_processing",
                        duration_seconds=float(queue_wait_duration_ms) / 1000.0,
                        account_id=getattr(ctx, "account_id", None),
                    )
                except Exception:
                    pass
                result["queue_status"] = status
                record_resource_wait_metrics(
                    telemetry_id=telemetry_id,
                    queue_status=status,
                    root_uri=result.get("root_uri"),
                )
                telemetry.set("queue.wait.duration_ms", queue_wait_duration_ms)
            if not wait:
                from openviking.storage.queuefs.add_resource_msg import AddResourceMsg

                root_uri = result.get("root_uri", "")
                if not isinstance(prepared, dict):
                    raise InternalError("Deferred resource processing payload is missing")
                lock_handoff = deferred_lock.to_handoff()
                msg = AddResourceMsg(
                    task_id=str(uuid4()),
                    root_uri=root_uri,
                    prepared=prepared,
                    telemetry_id=telemetry_id or None,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    role=str(ctx.role),
                    actor_peer_id=ctx.actor_peer_id,
                    lock_handoff=lock_handoff.to_dict() if lock_handoff else None,
                    reason=reason,
                    instruction=instruction,
                    timeout=timeout,
                    build_index=build_index,
                    summarize=summarize,
                    strict=bool(kwargs.get("strict", False)),
                    ignore_dirs=kwargs.get("ignore_dirs"),
                    include=kwargs.get("include"),
                    exclude=kwargs.get("exclude"),
                    directly_upload_media=bool(kwargs.get("directly_upload_media", True)),
                    preserve_structure=kwargs.get("preserve_structure"),
                    create_parent=bool(kwargs.get("create_parent", False)),
                    allow_local_path_resolution=allow_local_path_resolution,
                    enforce_public_remote_targets=enforce_public_remote_targets,
                    source_name=kwargs.get("source_name"),
                    skip_watch_management=True,
                )
                task = await self._enqueue_add_resource_job(
                    msg,
                    resource_lock=deferred_lock,
                )
                deferred_lock = NO_LOCK
                result["task_id"] = task.task_id
                job_enqueued = True
            await self._manage_watch_if_needed(
                watch_manager=watch_manager,
                skip_watch_management=skip_watch_management,
                watch_interval=watch_interval,
                target=target,
                root_uri=str(result.get("root_uri") or ""),
                path=path,
                reason=reason,
                instruction=instruction,
                build_index=build_index,
                summarize=summarize,
                processor_kwargs=kwargs,
                watch_auth_state=normalized_args.watch_auth_state,
                ctx=ctx,
            )
            if wait:
                await self._link_resource_reason_memory(
                    result=result,
                    ctx=ctx,
                    reason=reason,
                    source_name=kwargs.get("source_name"),
                    timeout=timeout,
                )
            return result
        except Exception as exc:
            telemetry.set_error(
                "resource_service.add_resource",
                type(exc).__name__,
                str(exc),
            )
            raise
        finally:
            telemetry.set(
                "resource.request.duration_ms",
                round((time.perf_counter() - request_start) * 1000, 3),
            )
            if wait or not telemetry_id or not job_enqueued:
                get_request_wait_tracker().cleanup(telemetry_id)
                unregister_wait_telemetry(telemetry_id)
            if deferred_lock.active:
                await deferred_lock.close()

    async def _link_resource_reason_memory(
        self,
        *,
        result: Dict[str, Any],
        ctx: RequestContext,
        reason: str,
        source_name: Optional[str],
        timeout: Optional[float] = None,
    ) -> None:
        if not self._resource_memory_link_service:
            return
        if not (reason or "").strip():
            return
        root_uri = result.get("root_uri")
        if not root_uri:
            return
        try:
            link_result = await self._resource_memory_link_service.on_resource_added(
                ctx=ctx,
                resource_uri=root_uri,
                reason=reason,
                source_name=source_name,
                timeout=timeout,
            )
            result["memory_linking"] = link_result
        except Exception as exc:
            logger.warning("[ResourceService] Failed to link resource reason memory: %s", exc)
            result.setdefault("warnings", []).append(f"Memory linking failed: {exc}")

    async def _monitor_queue_processing(
        self,
        task_id: str,
        telemetry_id: str,
        account_id: str,
        user_id: str,
    ) -> None:
        from openviking.service.task_tracker import get_task_tracker

        task_tracker = get_task_tracker()
        request_wait_tracker = get_request_wait_tracker()
        await task_tracker.start(task_id, account_id=account_id, user_id=user_id)
        try:
            await request_wait_tracker.wait_for_request(telemetry_id)
            status = request_wait_tracker.build_queue_status(telemetry_id)
            errors = sum(int(group.get("error_count", 0) or 0) for group in status.values())
            if errors:
                await task_tracker.fail(
                    task_id,
                    f"queue processing failed: {status}",
                    account_id=account_id,
                    user_id=user_id,
                )
            else:
                await task_tracker.complete(
                    task_id,
                    {"queue_status": status},
                    account_id=account_id,
                    user_id=user_id,
                )
        except Exception as exc:
            await task_tracker.fail(task_id, str(exc), account_id=account_id, user_id=user_id)
        finally:
            request_wait_tracker.cleanup(telemetry_id)
            unregister_wait_telemetry(telemetry_id)

    # ── Connector routing ──

    # Schemes only the external Connector can import; the standard pipeline
    # has no accessor for them, so degrading to it would only fail later with
    # a misleading parse error.
    _CONNECTOR_ONLY_SCHEMES = ("tos://",)

    def _should_use_connector(
        self,
        path: str,
        *,
        ctx: Optional[RequestContext] = None,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        wait: bool = False,
        reason: str = "",
        instruction: str = "",
        build_index: bool = True,
        summarize: bool = False,
        watch_interval: float = 0,
        connector_args: Optional[Dict[str, Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Decide whether a top-level resource path belongs to Connector.

        Returns True to delegate to the Connector, False to route to the
        standard pipeline. A source type only the Connector can import
        (tos://) raises InvalidArgumentError when the Connector is disabled,
        does not allow the type, or cannot honor the request parameters —
        degrading such a request would only fail later with a misleading
        parse error. Source types the standard pipeline can also handle
        degrade to it instead when parameters are unsupported.
        """
        from openviking_cli.utils.config.open_viking_config import get_openviking_config

        if not isinstance(path, str):
            return False
        connector_only = path.startswith(self._CONNECTOR_ONLY_SCHEMES)

        config = get_openviking_config().connector
        # Match full URL schemes, not bare prefixes: "tos" must not capture
        # paths like "tostring://..." or a local file named "tos_notes.md".
        allowed_schemes = tuple(f"{add_type}://" for add_type in config.allowed_add_types)
        if not config.enable or not path.startswith(allowed_schemes):
            if connector_only:
                raise InvalidArgumentError(
                    f"'{path}' can only be imported through the Connector integration, "
                    "which is disabled or does not allow this source type."
                )
            return False

        if ctx is not None and (to or parent):
            target = ContentTargetSpec.from_fields(
                ctx=ctx,
                kind="resource",
                to=to,
                parent=parent,
                create_parent=bool((kwargs or {}).get("create_parent", False)),
            )
            to = target.to
            parent = target.parent

        unsupported = self._unsupported_connector_params(
            wait=wait,
            reason=reason,
            instruction=instruction,
            build_index=build_index,
            summarize=summarize,
            watch_interval=watch_interval,
            connector_args=connector_args or {},
            kwargs=kwargs or {},
            to=to,
            parent=parent,
        )
        if not unsupported:
            return True
        detail = "; ".join(unsupported)
        if connector_only:
            raise InvalidArgumentError(f"Connector import does not support: {detail}")
        logger.info(
            f"[ResourceService] Connector does not support {detail} for path {path}; "
            "falling back to the standard import pipeline"
        )
        return False

    @staticmethod
    def _unsupported_connector_params(
        *,
        wait: bool,
        reason: str,
        instruction: str,
        build_index: bool,
        summarize: bool,
        watch_interval: float,
        connector_args: Dict[str, Any],
        kwargs: Dict[str, Any],
        to: Optional[str] = None,
        parent: Optional[str] = None,
    ) -> List[str]:
        """add_resource params the Connector delegation cannot honor.

        Returns an empty list when the request is fully supported.
        """
        unsupported: List[str] = []
        if to:
            unsupported.append(
                "exact 'to' targets (Connector imports require a parent destination)"
            )
        if (
            parent
            and parent != "viking://resources"
            and not parent.startswith("viking://resources/")
        ):
            unsupported.append("parent outside the public resources root (viking://resources/...)")
        if watch_interval > 0:
            unsupported.append("watch_interval>0 (Connector imports cannot be watched yet)")
        if wait:
            unsupported.append(
                "wait=true (Connector imports run asynchronously; poll the returned task_id)"
            )
        if reason:
            unsupported.append(
                "reason (Connector imports cannot preserve resource-reason semantics)"
            )
        if instruction:
            unsupported.append("instruction")
        if not build_index:
            unsupported.append("build_index=false")
        if summarize:
            unsupported.append("summarize=true")
        if kwargs.get("strict"):
            unsupported.append("strict=true (Connector imports fail per file, not all-or-nothing)")
        for field in ("ignore_dirs", "include", "exclude"):
            if kwargs.get(field):
                unsupported.append(f"{field} (scope TOS imports with the tos:// path prefix)")
        if kwargs.get("preserve_structure") is False:
            unsupported.append(
                "preserve_structure=false (Connector always mirrors the source directory tree)"
            )
        if not kwargs.get("directly_upload_media", True):
            unsupported.append("directly_upload_media=false")
        if kwargs.get("source_name"):
            unsupported.append("source_name")
        if connector_args:
            unsupported.append(
                "args (Connector imports derive path_prefix from parent; "
                "args keys are not forwarded)"
            )
        return unsupported

    @staticmethod
    def _connector_path_prefix(target_uri: Optional[str]) -> Optional[List[str]]:
        """Map the resolved parent target onto the Connector's path_prefix.

        The TOS plugin composes final URIs as
        viking://resources/<path_prefix>/<source path>/<doc name>, so only
        parent targets under the public resources root can be honored.
        """
        if not target_uri:
            return None
        root = "viking://resources"
        if target_uri == root:
            return None
        if not target_uri.startswith(root + "/"):
            raise InvalidArgumentError(
                "Connector imports can only target the public resources root "
                f"(viking://resources/...), got '{target_uri}'."
            )
        segments = [seg for seg in target_uri[len(root) + 1 :].split("/") if seg]
        return segments or None

    async def _add_resource_via_connector(
        self,
        path: str,
        ctx: RequestContext,
        parent: Optional[str],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Route add_resource to the external Connector service."""
        from openviking.service.task_tracker import get_task_tracker
        from openviking_cli.utils.config.open_viking_config import get_openviking_config

        config = get_openviking_config().connector
        if not ctx.api_key:
            raise InvalidArgumentError("Connector import requires an API key in the request.")

        target = ContentTargetSpec.from_fields(
            ctx=ctx,
            kind="resource",
            parent=parent,
            create_parent=bool(kwargs.get("create_parent", False)),
        )
        task_resource_id = target.parent or None
        path_prefix = self._connector_path_prefix(task_resource_id)

        client = ConnectorClient(
            doc_add_url=config.connector,
            task_info_url=config.tracker,
            account_id=ctx.account_id,
        )

        add_type, separator, source_path = path.partition("://")
        source_path = source_path.strip()
        if not separator or not add_type or not source_path:
            raise InvalidArgumentError(
                "Connector import requires path='<add_type>://<source path>'."
            )

        task_tracker = get_task_tracker()
        task = await task_tracker.create(
            "connector_import",
            resource_id=task_resource_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        try:
            result = await client.submit_doc_add(
                add_type=add_type,
                api_key=ctx.api_key,
                tos_path=source_path,
                path_prefix=path_prefix,
                include_child=True,
                extra_params=None,
            )

            connector_task_key = result.get("task_key") or result.get("TaskKey") or ""
            if not connector_task_key:
                raise InternalError(
                    f"Connector accepted the import but returned no task key: {result}"
                )
        except asyncio.CancelledError:
            await task_tracker.fail(
                task.task_id,
                "connector task submission cancelled",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            raise
        except Exception as exc:
            await task_tracker.fail(
                task.task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            raise

        background = asyncio.create_task(
            self._monitor_connector_task(
                client=client,
                connector_task_key=connector_task_key,
                ov_task_id=task.task_id,
                poll_interval_ms=config.poll_interval_ms,
                timeout_seconds=config.timeout_seconds,
                ctx=ctx,
            )
        )
        self._background_tasks.add(background)
        background.add_done_callback(self._background_tasks.discard)

        response = {
            "status": "accepted",
            "task_id": task.task_id,
            "connector_task_key": connector_task_key,
        }
        if task_resource_id:
            response["resource_id"] = task_resource_id
        return response

    async def _monitor_connector_task(
        self,
        client: ConnectorClient,
        connector_task_key: str,
        ov_task_id: str,
        poll_interval_ms: int,
        timeout_seconds: int,
        ctx: RequestContext,
    ) -> None:
        """Poll the Connector task until terminal state, then update OV TaskRecord."""
        from openviking.service.task_tracker import get_task_tracker

        task_tracker = get_task_tracker()
        await task_tracker.start(
            ov_task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )

        poll_interval = poll_interval_ms / 1000.0
        deadline = time.perf_counter() + timeout_seconds
        terminal_statuses = {"succeeded", "failed", "cancelled"}

        try:
            while time.perf_counter() < deadline:
                await asyncio.sleep(poll_interval)
                try:
                    info = await client.get_task_info(connector_task_key, ctx.api_key)
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code not in {408, 429} and status_code < 500:
                        raise
                    logger.warning(
                        "[ResourceService] Transient Connector task polling HTTP error "
                        f"for {connector_task_key}: {status_code}; retrying"
                    )
                    continue
                except httpx.RequestError as exc:
                    logger.warning(
                        "[ResourceService] Transient Connector task polling error "
                        f"for {connector_task_key}: {exc}; retrying"
                    )
                    continue
                status = (info.get("Status") or info.get("status") or "").lower()

                await task_tracker.update_stage(
                    ov_task_id,
                    f"connector:{status}",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )

                if status in terminal_statuses:
                    if status == "succeeded":
                        await task_tracker.complete(
                            ov_task_id,
                            {"connector_status": status, "connector_task_key": connector_task_key},
                            account_id=ctx.account_id,
                            user_id=ctx.user.user_id,
                        )
                    else:
                        error_msg = info.get("ErrorMessage") or info.get("error_message") or status
                        await task_tracker.fail(
                            ov_task_id,
                            f"connector task {status}: {error_msg}",
                            account_id=ctx.account_id,
                            user_id=ctx.user.user_id,
                        )
                    return

            await task_tracker.fail(
                ov_task_id,
                f"connector task timed out after {timeout_seconds}s",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        except asyncio.CancelledError:
            await task_tracker.fail(
                ov_task_id,
                "background connector task monitoring cancelled",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            raise
        except Exception as exc:
            logger.error(f"[ResourceService] Connector task monitor error: {exc}")
            await task_tracker.fail(
                ov_task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )

    async def _handle_watch_task_creation(
        self,
        path: str,
        to_uri: str,
        parent_uri: Optional[str],
        reason: str,
        instruction: str,
        watch_interval: float,
        build_index: bool,
        summarize: bool,
        processor_kwargs: Dict[str, Any],
        auth_state: Optional[Dict[str, Any]],
        ctx: RequestContext,
    ) -> None:
        """Handle creation or update of watch task.

        Args:
            path: Resource path to monitor
            to_uri: Target URI
            parent_uri: Parent URI
            reason: Reason for monitoring
            instruction: Monitoring instruction
            watch_interval: Monitoring interval in minutes
            ctx: Request context with user identity

        Raises:
            ConflictError: If target URI is already used by another active task
        """
        watch_manager = self._get_watch_manager()
        if not watch_manager:
            return

        existing_task = await watch_manager.get_task_by_uri(
            to_uri=to_uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            role=str(ctx.role),
        )
        if existing_task:
            if existing_task.is_active:
                raise ConflictError(
                    f"Target URI '{to_uri}' is already being monitored by task {existing_task.task_id}. "
                    f"Please cancel the existing task first.",
                    resource=to_uri,
                )
            await watch_manager.update_task(
                task_id=existing_task.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                role=str(ctx.role),
                path=path,
                to_uri=to_uri,
                parent_uri=parent_uri,
                reason=reason,
                instruction=instruction,
                watch_interval=watch_interval,
                build_index=build_index,
                summarize=summarize,
                processor_kwargs=processor_kwargs,
                auth_state=auth_state,
                is_active=True,
            )
            logger.info(
                f"[ResourceService] Reactivated and updated watch task {existing_task.task_id} for {to_uri}"
            )
        else:
            task = await watch_manager.create_task(
                path=path,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                original_role=str(ctx.role),
                to_uri=to_uri,
                parent_uri=parent_uri,
                reason=reason,
                instruction=instruction,
                watch_interval=watch_interval,
                build_index=build_index,
                summarize=summarize,
                processor_kwargs=processor_kwargs,
                auth_state=auth_state,
            )
            logger.info(f"[ResourceService] Created watch task {task.task_id} for {to_uri}")

    async def _handle_watch_task_cancellation(self, to_uri: str, ctx: RequestContext) -> None:
        """Handle cancellation of watch task.

        Args:
            to_uri: Target URI to cancel watch for
            ctx: Request context with user identity
        """
        watch_manager = self._get_watch_manager()
        if not watch_manager:
            return

        existing_task = await watch_manager.get_task_by_uri(
            to_uri=to_uri,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            role=str(ctx.role),
        )
        if existing_task:
            await watch_manager.update_task(
                task_id=existing_task.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                role=str(ctx.role),
                is_active=False,
            )
            logger.info(
                f"[ResourceService] Deactivated watch task {existing_task.task_id} for {to_uri}"
            )

    async def add_skill(
        self,
        data: Any,
        ctx: RequestContext,
        wait: bool = False,
        timeout: Optional[float] = None,
        allow_local_path_resolution: bool = True,
        source_path_hint: Optional[str] = None,
        apply_privacy: bool = True,
        privacy_change_reason: str = "auto-extracted from add_skill",
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add skill to OpenViking.

        Args:
            data: Skill data (directory path, file path, string, or dict)
            wait: Whether to wait for vectorization to complete
            timeout: Wait timeout in seconds
            target_uri: Optional root URI override (e.g. ``viking://agent/skills``).

        Returns:
            Processing result
        """
        self._ensure_initialized()
        if not target_uri:
            from openviking.server.dependencies import get_server_config

            target_uri = await effective_skill_add_target(
                viking_fs=self._viking_fs,
                ctx=ctx,
                server_config=get_server_config(),
            )
        telemetry_id = get_current_telemetry().telemetry_id
        request_wait_tracker = get_request_wait_tracker()
        monitor_started = False
        if telemetry_id:
            request_wait_tracker.register_request(telemetry_id)

        try:
            if isinstance(data, SkillProcessingPreparation):
                result = await self._skill_processor.process_prepared_skill(
                    data,
                    viking_fs=self._viking_fs,
                    ctx=ctx,
                    apply_privacy=apply_privacy,
                    privacy_change_reason=privacy_change_reason,
                    target_uri=target_uri,
                )
            else:
                result = await self._skill_processor.process_skill(
                    data=data,
                    viking_fs=self._viking_fs,
                    ctx=ctx,
                    allow_local_path_resolution=allow_local_path_resolution,
                    source_path_hint=source_path_hint,
                    apply_privacy=apply_privacy,
                    privacy_change_reason=privacy_change_reason,
                    target_uri=target_uri,
                )
            if isinstance(result, dict) and "root_uri" not in result and result.get("uri"):
                result["root_uri"] = result["uri"]

            if wait:
                wait_start = time.perf_counter()
                try:
                    if telemetry_id:
                        await request_wait_tracker.wait_for_request(telemetry_id, timeout=timeout)
                        status = request_wait_tracker.build_queue_status(telemetry_id)
                    else:
                        qm = get_queue_manager()
                        status = build_queue_status_payload(await qm.wait_complete(timeout=timeout))
                except TimeoutError as exc:
                    get_current_telemetry().set_error(
                        "resource_service.wait_complete",
                        "DEADLINE_EXCEEDED",
                        str(exc),
                    )
                    raise DeadlineExceededError("queue processing", timeout) from exc
                get_current_telemetry().set(
                    "queue.wait.duration_ms",
                    round((time.perf_counter() - wait_start) * 1000, 3),
                )
                result["queue_status"] = status
            else:
                from openviking.service.task_tracker import get_task_tracker

                task_tracker = get_task_tracker()
                task = await task_tracker.create(
                    "add_skill",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                result["task_id"] = task.task_id
                if telemetry_id:
                    monitor_started = True
                    asyncio.create_task(
                        self._monitor_queue_processing(
                            task.task_id,
                            telemetry_id,
                            ctx.account_id,
                            ctx.user.user_id,
                        )
                    )
                else:
                    await task_tracker.start(
                        task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
                    )
                    await task_tracker.complete(
                        task.task_id,
                        {},
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
                    )

            return result
        finally:
            if wait or not telemetry_id or not monitor_started:
                request_wait_tracker.cleanup(telemetry_id)
                unregister_wait_telemetry(telemetry_id)

    async def build_index(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Manually trigger index building.

        Args:
            resource_uris: List of resource URIs to index.
            ctx: Request context.

        Returns:
            Processing result
        """
        self._ensure_initialized()
        return await self._resource_processor.build_index(resource_uris, ctx, **kwargs)

    async def summarize(
        self, resource_uris: List[str], ctx: RequestContext, **kwargs
    ) -> Dict[str, Any]:
        """Manually trigger summarization.

        Args:
            resource_uris: List of resource URIs to summarize.
            ctx: Request context.

        Returns:
            Processing result
        """
        self._ensure_initialized()
        return await self._resource_processor.summarize(resource_uris, ctx, **kwargs)

    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for all queued processing to complete.

        Args:
            timeout: Wait timeout in seconds

        Returns:
            Queue status
        """
        qm = get_queue_manager()
        try:
            status = await qm.wait_complete(timeout=timeout)
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc
        return {
            name: {
                "processed": s.processed,
                "requeue_count": getattr(s, "requeue_count", 0),
                "error_count": s.error_count,
                "errors": [{"message": e.message} for e in s.errors],
            }
            for name, s in status.items()
        }
