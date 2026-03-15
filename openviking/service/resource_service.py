# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Resource Service for OpenViking.

Provides resource management operations: add_resource, add_skill, wait_processed.
"""

import time
from typing import Any, Dict, List, Optional

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
    DeadlineExceededError,
    InvalidArgumentError,
    NotInitializedError,
)
from openviking_cli.utils import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


class ResourceService:
    """Resource management service."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        resource_processor: Optional[ResourceProcessor] = None,
        skill_processor: Optional[SkillProcessor] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        viking_fs: VikingFS,
        resource_processor: ResourceProcessor,
        skill_processor: SkillProcessor,
    ) -> None:
        """Set dependencies (for deferred initialization)."""
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._resource_processor = resource_processor
        self._skill_processor = skill_processor

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
        **kwargs,
    ) -> Dict[str, Any]:
        """Add resource to OpenViking (only supports resources scope).

        Args:
            path: Resource path (local file or URL)
            target: Target URI
            reason: Reason for adding
            instruction: Processing instruction
            wait: Whether to wait for semantic extraction and vectorization to complete
            timeout: Wait timeout in seconds
            build_index: Whether to build vector index immediately (default: True).
            summarize: Whether to generate summary (default: False).
            **kwargs: Extra options forwarded to the parser chain.

        Returns:
            Processing result
        """
        self._ensure_initialized()
        request_start = time.perf_counter()
        telemetry_id = register_wait_telemetry(wait)

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
                **kwargs,
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
                queue_wait_duration_ms = round((time.perf_counter() - wait_start) * 1000, 3)
                result["queue_status"] = build_queue_status_payload(status)
                record_resource_wait_metrics(
                    telemetry_id=telemetry_id,
                    queue_status=status,
                    root_uri=result.get("root_uri"),
                )
                get_current_telemetry().set("queue.wait.duration_ms", queue_wait_duration_ms)

            get_current_telemetry().set(
                "resource.request.duration_ms",
                round((time.perf_counter() - request_start) * 1000, 3),
            )
            return result
        except Exception as exc:
            get_current_telemetry().set_error(
                "resource_service.add_resource",
                type(exc).__name__,
                str(exc),
            )
            raise
        finally:
            unregister_wait_telemetry(telemetry_id)

    async def add_skill(
        self,
        data: Any,
        ctx: RequestContext,
        wait: bool = False,
        timeout: Optional[float] = None,
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
