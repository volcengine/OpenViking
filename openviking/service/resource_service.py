# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Resource Service for OpenViking.

Provides resource management operations: add_resource, add_skill, wait_processed.
"""

import json
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.resource_summary import (
    build_queue_status_payload,
    record_resource_wait_metrics,
    register_wait_telemetry,
    unregister_wait_telemetry,
)
from openviking.utils.resource_processor import ResourceProcessor
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.exceptions import (
    ConflictError,
    DeadlineExceededError,
    InvalidArgumentError,
    NotInitializedError,
)
from openviking_cli.utils import get_logger
from openviking_cli.utils.uri import VikingURI

if TYPE_CHECKING:
    from openviking.resource.watch_manager import WatchManager
    from openviking.resource.watch_scheduler import WatchScheduler

logger = get_logger(__name__)


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
            **kwargs: Extra options forwarded to the parser chain

        Returns:
            Processing result containing 'root_uri' and other metadata

        Raises:
            ConflictError: If the target URI already has an active watch task
            InvalidArgumentError: If the URI scope is not 'resources'
        """
        self._ensure_initialized()
        request_start = time.perf_counter()
        telemetry = get_current_telemetry()
        telemetry_id = register_wait_telemetry(wait)
        watch_manager = self._get_watch_manager()
        watch_enabled = bool(
            watch_manager and to and not skip_watch_management and watch_interval > 0
        )

        telemetry.set("resource.flags.wait", wait)
        telemetry.set("resource.flags.build_index", build_index)
        telemetry.set("resource.flags.summarize", summarize)
        telemetry.set("resource.flags.watch_enabled", watch_enabled)

        try:
            # add_resource only supports resources scope
            if to and to.startswith("viking://"):
                parsed = VikingURI(to)
                if parsed.scope != "resources":
                    raise InvalidArgumentError(
                        f"add_resource only supports resources scope, use dedicated interface to add {parsed.scope} content"
                    )
            if parent and parent.startswith("viking://"):
                parsed = VikingURI(parent)
                if parsed.scope != "resources":
                    raise InvalidArgumentError(
                        f"add_resource only supports resources scope, use dedicated interface to add {parsed.scope} content"
                    )
            if watch_manager and not skip_watch_management and watch_interval > 0 and not to:
                raise InvalidArgumentError(
                    "watch_interval > 0 requires 'to' to be specified (target URI to watch)"
                )

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
                allow_local_path_resolution=allow_local_path_resolution,
                **kwargs,
            )

            if wait:
                qm = get_queue_manager()
                wait_start = time.perf_counter()
                try:
                    with telemetry.measure("resource.wait"):
                        status = await qm.wait_complete(timeout=timeout)
                except TimeoutError as exc:
                    telemetry.set_error(
                        "resource_service.wait_complete",
                        "DEADLINE_EXCEEDED",
                        str(exc),
                    )
                    raise DeadlineExceededError("queue processing", timeout) from exc
                queue_wait_duration_ms = round((time.perf_counter() - wait_start) * 1000, 3)
                result["queue_status"] = build_queue_status_payload(status)
                record_resource_wait_metrics(
                    telemetry_id=telemetry_id,
                    queue_status=status,
                    root_uri=result.get("root_uri"),
                )
                telemetry.set("queue.wait.duration_ms", queue_wait_duration_ms)
            if watch_manager and to and not skip_watch_management:
                with telemetry.measure("resource.watch"):
                    if watch_interval > 0:
                        try:
                            processor_kwargs = self._sanitize_watch_processor_kwargs(kwargs)
                            await self._handle_watch_task_creation(
                                path=path,
                                to_uri=to,
                                parent_uri=parent,
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
                                f"[ResourceService] Failed to create watch task for {to}: {e}"
                            )
                    else:
                        try:
                            await self._handle_watch_task_cancellation(to_uri=to, ctx=ctx)
                        except Exception as e:
                            logger.warning(
                                f"[ResourceService] Failed to cancel watch task for {to}: {e}"
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
            agent_id=ctx.user.agent_id,
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
                agent_id=ctx.user.agent_id,
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
                agent_id=ctx.user.agent_id,
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
            agent_id=ctx.user.agent_id,
        )
        if existing_task:
            await watch_manager.update_task(
                task_id=existing_task.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                role=ctx.role.value,
                agent_id=ctx.user.agent_id,
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

        result = await self._skill_processor.process_skill(
            data=data,
            viking_fs=self._viking_fs,
            ctx=ctx,
            allow_local_path_resolution=allow_local_path_resolution,
        )

        if wait:
            qm = get_queue_manager()
            wait_start = time.perf_counter()
            try:
                status = await qm.wait_complete(timeout=timeout)
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
            result["queue_status"] = build_queue_status_payload(status)

        return result

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
                "error_count": s.error_count,
                "errors": [{"message": e.message} for e in s.errors],
            }
            for name, s in status.items()
        }
