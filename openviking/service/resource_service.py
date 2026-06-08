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

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_optional_viking_uri
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import (
    is_remote_resource_source,
    require_remote_resource_source,
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
    summarize_queue_errors,
    unregister_wait_telemetry,
)
from openviking.utils import is_git_repo_url, parse_code_hosting_url
from openviking.utils.media_processor import _smart_stem
from openviking.utils.network_guard import ensure_public_remote_target
from openviking.utils.resource_processor import ResourceProcessor
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.exceptions import (
    ConflictError,
    DeadlineExceededError,
    InvalidArgumentError,
    NotInitializedError,
)
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.resource.watch_manager import WatchManager
    from openviking.resource.watch_scheduler import WatchScheduler

logger = get_logger(__name__)


@dataclass
class _ResourceSourceInfo:
    source_name: Optional[str] = None
    source_path: Optional[str] = None
    source_format: Optional[str] = None


class ResourceService:
    """Resource management service."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        resource_processor: Optional[ResourceProcessor] = None,
        skill_processor: Optional[SkillProcessor] = None,
        watch_scheduler: Optional["WatchScheduler"] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor
        self._watch_scheduler = watch_scheduler
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        viking_fs: VikingFS,
        resource_processor: ResourceProcessor,
        skill_processor: SkillProcessor,
        watch_scheduler: Optional["WatchScheduler"] = None,
    ) -> None:
        """Set dependencies (for deferred initialization)."""
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor
        self._watch_scheduler = watch_scheduler

    def _get_watch_manager(self) -> Optional["WatchManager"]:
        if not self._watch_scheduler:
            return None
        return self._watch_scheduler.watch_manager

    def _sanitize_watch_processor_kwargs(self, processor_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        for key, value in processor_kwargs.items():
            try:
                json.dumps(value, ensure_ascii=False)
            except TypeError:
                continue
            sanitized[key] = value
        return sanitized

    def _ensure_initialized(self) -> None:
        """Ensure all dependencies are initialized."""
        if not self._resource_processor:
            raise NotInitializedError("ResourceProcessor")
        if not self._skill_processor:
            raise NotInitializedError("SkillProcessor")
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")

    async def close_background_tasks(self) -> None:
        """Cancel in-flight background resource ingestion tasks during service shutdown."""
        if not self._background_tasks:
            return
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

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
        **kwargs,
    ) -> Dict[str, Any]:
        """Start background ingestion for Git repositories while reserving the target URI."""
        self._ensure_initialized()

        if to:
            to = resolve_path_variables(to)
        if parent:
            parent = resolve_path_variables(parent)
        to = validate_optional_viking_uri(
            to,
            field_name="to",
            allowed_scopes={"resources"},
        )
        parent = validate_optional_viking_uri(
            parent,
            field_name="parent",
            allowed_scopes={"resources"},
        )

        from openviking.service.task_tracker import get_task_tracker

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
                to=to,
                parent=parent,
                source_name=source_name,
                source_info=source_info,
                create_parent=bool(kwargs.get("create_parent", False)),
            )

            task_tracker = get_task_tracker()
            task = await task_tracker.create(
                "add_resource",
                resource_id=root_uri,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            await task_tracker.update_stage(
                task.task_id,
                "queued",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )

            add_kwargs = dict(
                kwargs,
                path=path,
                ctx=ctx,
                to=root_uri,
                parent=None,
                reason=reason,
                instruction=instruction,
                timeout=timeout,
                build_index=build_index,
                summarize=summarize,
                watch_interval=watch_interval,
                skip_watch_management=skip_watch_management,
                allow_local_path_resolution=allow_local_path_resolution,
                enforce_public_remote_targets=enforce_public_remote_targets,
            )
            background = asyncio.create_task(
                self._run_add_resource_task(
                    task.task_id,
                    ctx=ctx,
                    add_kwargs=add_kwargs,
                    resource_lock=resource_lock,
                )
            )
            resource_lock = NO_LOCK
            self._background_tasks.add(background)
            background.add_done_callback(self._background_tasks.discard)
            return {
                "status": "success",
                "root_uri": root_uri,
                "task_id": task.task_id,
            }
        except Exception:
            await resource_lock.close()
            raise

    async def _run_add_resource_task(
        self,
        task_id: str,
        *,
        ctx: RequestContext,
        add_kwargs: Dict[str, Any],
        resource_lock: LockLease,
    ) -> None:
        from openviking.service.task_tracker import get_task_tracker

        task_tracker = get_task_tracker()

        async def _set_stage(stage: str) -> None:
            await task_tracker.update_stage(
                task_id,
                stage,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )

        try:
            await task_tracker.start(
                task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                stage="queued",
            )
            result = await self.add_resource(
                wait=True,
                resource_lock=resource_lock,
                stage_callback=_set_stage,
                **add_kwargs,
            )
            if result.get("status") == "error":
                errors = result.get("errors") or ["resource processing failed"]
                await task_tracker.fail(
                    task_id,
                    "; ".join(str(error) for error in errors),
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                return
            queue_errors = summarize_queue_errors(result.get("queue_status"))
            if queue_errors:
                await task_tracker.fail(
                    task_id,
                    "queue processing failed: " + "; ".join(queue_errors),
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                return
            await task_tracker.complete(
                task_id,
                result,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        except asyncio.CancelledError:
            await task_tracker.fail(
                task_id,
                "background resource ingestion cancelled",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            raise
        except Exception as exc:
            await task_tracker.fail(
                task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        finally:
            await resource_lock.close()

    async def _plan_resource_target(
        self,
        *,
        path: str,
        ctx: RequestContext,
        to: Optional[str],
        parent: Optional[str],
        source_name: Optional[str],
        source_info: _ResourceSourceInfo,
        create_parent: bool,
    ) -> tuple[str, LockLease]:
        if not self._resource_processor or not self._viking_fs:
            raise NotInitializedError("ResourceProcessor")

        doc_name = self._target_doc_name(path, source_name, source_info)
        source_path = source_info.source_path or source_name or path
        root_uri, candidate_uri = await self._resource_processor.tree_builder.resolve_target_uri(
            ctx=ctx,
            doc_name=doc_name,
            scope="resources",
            to_uri=to,
            parent_uri=parent,
            source_path=source_path,
            source_format=source_info.source_format,
            create_parent=create_parent,
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
            **kwargs: Extra options forwarded to the parser chain

        Returns:
            Processing result containing 'root_uri' and other metadata

        Raises:
            ConflictError: If the target URI already has an active watch task
            InvalidArgumentError: If the URI scope is not 'resources'
        """
        self._ensure_initialized()
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
        monitor_started = False
        if telemetry_id:
            request_wait_tracker.register_request(telemetry_id)
        watch_manager = self._get_watch_manager()
        watch_enabled = bool(watch_manager and not skip_watch_management and watch_interval > 0)

        telemetry.set("resource.flags.wait", wait)
        telemetry.set("resource.flags.build_index", build_index)
        telemetry.set("resource.flags.summarize", summarize)
        telemetry.set("resource.flags.watch_enabled", watch_enabled)

        try:
            # Resolve path variables before validation
            if to:
                to = resolve_path_variables(to)
            if parent:
                parent = resolve_path_variables(parent)

            to = validate_optional_viking_uri(
                to,
                field_name="to",
                allowed_scopes={"resources"},
            )
            parent = validate_optional_viking_uri(
                parent,
                field_name="parent",
                allowed_scopes={"resources"},
            )
            if enforce_public_remote_targets and is_remote_resource_source(path):
                path = require_remote_resource_source(path)
                kwargs.setdefault("request_validator", ensure_public_remote_target)
            if resource_lock is not None:
                kwargs["resource_lock"] = resource_lock

            result = await self._resource_processor.process_resource(
                path=path,
                ctx=ctx,
                reason=reason,
                instruction=instruction,
                scope="resources",
                to=to,
                parent=parent,
                build_index=build_index,
                summarize=summarize,
                stage_callback=stage_callback,
                allow_local_path_resolution=allow_local_path_resolution,
                **kwargs,
            )

            if result.get("status") == "error":
                return result
            elif wait:
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
            if watch_manager and not skip_watch_management:
                with telemetry.measure("resource.watch"):
                    if watch_interval > 0:
                        watch_to = to
                        parent_uri = parent
                        if not watch_to:
                            watch_to = validate_optional_viking_uri(
                                result.get("root_uri"),
                                field_name="root_uri",
                                allowed_scopes={"resources"},
                            )
                            parent_uri = None
                        if not watch_to:
                            raise InvalidArgumentError(
                                "watch_interval > 0 requires a stable target URI. "
                                "Pass 'to' explicitly, or add a resource type that returns root_uri."
                            )
                        try:
                            processor_kwargs = self._sanitize_watch_processor_kwargs(kwargs)
                            await self._handle_watch_task_creation(
                                path=path,
                                to_uri=watch_to,
                                parent_uri=parent_uri,
                                reason=reason,
                                instruction=instruction,
                                watch_interval=watch_interval,
                                build_index=build_index,
                                summarize=summarize,
                                processor_kwargs=processor_kwargs,
                                ctx=ctx,
                            )
                        except ConflictError:
                            raise
                        except Exception as e:
                            logger.warning(
                                f"[ResourceService] Failed to create watch task for {watch_to}: {e}"
                            )
                    elif to:
                        try:
                            await self._handle_watch_task_cancellation(to_uri=to, ctx=ctx)
                        except Exception as e:
                            logger.warning(
                                f"[ResourceService] Failed to cancel watch task for {to}: {e}"
                            )
            if not wait:
                from openviking.service.task_tracker import get_task_tracker

                task_tracker = get_task_tracker()
                root_uri = result.get("root_uri", "")
                task = await task_tracker.create(
                    "add_resource",
                    resource_id=root_uri,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                result["task_id"] = task.task_id
                if telemetry_id:
                    monitor_started = True
                    background = asyncio.create_task(
                        self._monitor_queue_processing(
                            task.task_id,
                            telemetry_id,
                            ctx.account_id,
                            ctx.user.user_id,
                        )
                    )
                    self._background_tasks.add(background)
                    background.add_done_callback(self._background_tasks.discard)
                else:
                    await task_tracker.start(
                        task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
                    )
                    await task_tracker.complete(
                        task.task_id,
                        {"root_uri": root_uri},
                        account_id=ctx.account_id,
                        user_id=ctx.user.user_id,
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
            if wait or not telemetry_id or not monitor_started:
                get_request_wait_tracker().cleanup(telemetry_id)
                unregister_wait_telemetry(telemetry_id)

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
            role=ctx.role.value,
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
                role=ctx.role.value,
                path=path,
                to_uri=to_uri,
                parent_uri=parent_uri,
                reason=reason,
                instruction=instruction,
                watch_interval=watch_interval,
                build_index=build_index,
                summarize=summarize,
                processor_kwargs=processor_kwargs,
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
                original_role=ctx.role.value,
                to_uri=to_uri,
                parent_uri=parent_uri,
                reason=reason,
                instruction=instruction,
                watch_interval=watch_interval,
                build_index=build_index,
                summarize=summarize,
                processor_kwargs=processor_kwargs,
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
            role=ctx.role.value,
        )
        if existing_task:
            await watch_manager.update_task(
                task_id=existing_task.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                role=ctx.role.value,
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
    ) -> Dict[str, Any]:
        """Add skill to OpenViking.

        Args:
            data: Skill data (directory path, file path, string, or dict)
            wait: Whether to wait for vectorization to complete
            timeout: Wait timeout in seconds

        Returns:
            Processing result
        """
        self._ensure_initialized()
        telemetry_id = get_current_telemetry().telemetry_id
        request_wait_tracker = get_request_wait_tracker()
        monitor_started = False
        if telemetry_id:
            request_wait_tracker.register_request(telemetry_id)

        try:
            result = await self._skill_processor.process_skill(
                data=data,
                viking_fs=self._viking_fs,
                ctx=ctx,
                allow_local_path_resolution=allow_local_path_resolution,
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
