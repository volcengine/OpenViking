# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""In-process memory consolidation for `ov compile --skill memory`.

This runs the memory framework (ExtractLoop + MemoryUpdater) directly inside
OpenViking core, without the VikingBot agent path. It borrows the lightweight
`session.commit` task shape: create a tracked task, run one background
``asyncio.Task``, and record running/completed/failed via the task tracker.
There is no QueueFS re-delivery; a crashed run leaves the task failed.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from openviking.core.namespace import classify_uri, uri_parts
from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.server.identity import RequestContext
from openviking.service.fs_service import FSService
from openviking.service.task_tracker import TaskRecord, get_task_tracker
from openviking.service.task_work_index import bind_task_context
from openviking.session.memory.consolidation_context_provider import (
    ConsolidationExtractContextProvider,
    build_consolidation_isolation_handler,
)
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_type_registry import get_default_registry
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.telemetry import tracer
from openviking.telemetry.span_models import create_root_span_attributes
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

try:
    from openviking.observability.context import (
        bind_root_observability_context,
        reset_root_observability_context,
    )
except ImportError:  # pragma: no cover - observability optional
    bind_root_observability_context = None
    reset_root_observability_context = None

logger = get_logger(__name__)


def _peer_id_from_memory_uri(uri: str) -> Optional[str]:
    """Return the peer_id when a memory URI lives under a peer space, else None."""
    parts = uri_parts(uri)
    if len(parts) >= 5 and parts[0] == "user" and parts[2] == "peers":
        return parts[3]
    return None


def _memory_type_from_target(uri: str) -> str:
    classification = classify_uri(uri)
    parts = uri_parts(uri)
    if (
        classification.context_type != "memory"
        or classification.content_index is None
        or len(parts) <= classification.content_index + 1
    ):
        raise InvalidArgumentError(
            "Memory-mode Compile target must be inside a memory type directory"
        )
    return parts[classification.content_index + 1]


class MemoryCompileRunner:
    """Own the validation and background execution of memory-mode Compile tasks."""

    task_type = "compile"
    task_id_prefix = "cmp_"

    def __init__(self, fs: FSService, vikingdb: Any = None) -> None:
        self._fs = fs
        self._vikingdb = vikingdb
        # Keep strong references so background tasks are not garbage collected.
        self._running: set[asyncio.Task[None]] = set()

    def set_vikingdb(self, vikingdb: Any) -> None:
        self._vikingdb = vikingdb

    async def create(
        self,
        *,
        target: str,
        instruction: Optional[str],
        ctx: RequestContext,
    ) -> TaskRecord:
        canonical_target, memory_type, peer_id = await self._normalize_target(target, ctx)
        tracker = get_task_tracker()
        task = await tracker.create(
            self.task_type,
            resource_id=canonical_target,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            meta={
                "request": {
                    "to": canonical_target,
                    "skill": "memory",
                    "memory_type": memory_type,
                    "peer_id": peer_id,
                    "instruction": instruction,
                }
            },
        )
        await tracker.update_stage(
            task.task_id,
            "queued",
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        background = asyncio.create_task(
            self._run(
                task_id=task.task_id,
                target=canonical_target,
                memory_type=memory_type,
                peer_id=peer_id,
                instruction=instruction,
                ctx=ctx,
            )
        )
        self._running.add(background)
        background.add_done_callback(self._running.discard)
        current = await tracker.get(
            task.task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        return current or task

    async def _normalize_target(
        self,
        target: str,
        ctx: RequestContext,
    ) -> tuple[str, str, Optional[str]]:
        uri = validate_request_viking_uri(
            resolve_path_variables(target),
            ctx,
            field_name="to",
        ).rstrip("/")
        memory_type = _memory_type_from_target(uri)
        await self._fs.ensure_write_access(uri, ctx)
        stat = await self._fs.stat(uri, ctx)
        if not stat.get("isDir"):
            raise InvalidArgumentError("Memory-mode Compile target must be a directory")
        canonical = str(stat.get("uri") or uri).rstrip("/")
        return canonical, memory_type, _peer_id_from_memory_uri(canonical)

    async def _run(
        self,
        *,
        task_id: str,
        target: str,
        memory_type: str,
        peer_id: Optional[str],
        instruction: Optional[str],
        ctx: RequestContext,
    ) -> None:
        tracker = get_task_tracker()
        tracker.register_running_task(task_id)
        root_token = None
        if bind_root_observability_context is not None:
            root_attrs = create_root_span_attributes(
                http_method="TASK",
                http_route="/compile/memory",
                request_id=task_id,
                url_path=target,
            )
            root_attrs.account_id = ctx.account_id
            root_attrs.user_id = ctx.user.user_id
            root_token = bind_root_observability_context(root_attrs)
        try:
            with tracer.start_as_current_span(name="compile.memory.consolidate"):
                trace_id = tracer.get_trace_id() or ""
                await tracker.start(
                    task_id,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                    stage="consolidating",
                )
                with bind_task_context(task_id, ctx.account_id, ctx.user.user_id):
                    result = await self._consolidate(
                        target=target,
                        memory_type=memory_type,
                        peer_id=peer_id,
                        instruction=instruction,
                        ctx=ctx,
                    )
                await tracker.complete(
                    task_id,
                    {"to": target, "skill": "memory", "trace_id": trace_id, **result},
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - surface any failure as task error
            tracer.error(f"Memory-mode compile failed: {exc}")
            await tracker.fail(
                task_id,
                f"MEMORY_CONSOLIDATION_FAILED: {exc}",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        finally:
            if root_token is not None and reset_root_observability_context is not None:
                reset_root_observability_context(root_token)
            await tracker.unregister_running_task(task_id)

    async def _consolidate(
        self,
        *,
        target: str,
        memory_type: str,
        peer_id: Optional[str],
        instruction: Optional[str],
        ctx: RequestContext,
    ) -> dict[str, Any]:
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        registry = get_default_registry()

        provider = ConsolidationExtractContextProvider(
            memory_type=memory_type,
            target_directory=target,
            instruction=instruction,
            memory_registry=registry,
        )
        provider._ctx = ctx
        provider._viking_fs = self._fs._ensure_initialized()

        extract_context = provider.get_extract_context()
        isolation_handler = build_consolidation_isolation_handler(
            ctx,
            extract_context,
            memory_type=memory_type,
            peer_id=peer_id,
        )
        isolation_handler.prepare_messages()
        provider._isolation_handler = isolation_handler
        await provider.prepare_extraction_messages()

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=provider._viking_fs,
            ctx=ctx,
            context_provider=provider,
            isolation_handler=isolation_handler,
            # Compile is offline and user-initiated; allow a longer agentic loop
            # so the model can ls/search/read across the directory before writing.
            max_iterations=10,
        )
        operations, _tools_used = await orchestrator.run()
        if operations is None:
            return {
                "memory_type": memory_type,
                "adds": [],
                "updates": [],
                "deletes": [],
                "total_adds": 0,
                "total_updates": 0,
                "total_deletes": 0,
                "errors": [],
            }

        updater = MemoryUpdater(registry=registry, vikingdb=self._vikingdb)
        apply_result = await updater.apply_operations(
            operations,
            ctx,
            extract_context=extract_context,
            isolation_handler=isolation_handler,
        )

        # Classify each touched URI as add vs update using the files the model
        # actually read. ExtractLoop's write-before-read guard guarantees any
        # modified pre-existing file is in read_file_contents, so a written URI
        # absent from it is a genuinely new file.
        before_uris = set(provider.read_file_contents.keys())
        adds: list[str] = []
        updates: list[str] = []
        for uri in apply_result.written_uris:
            (updates if uri in before_uris else adds).append(uri)
        for uri in apply_result.edited_uris:
            if uri not in updates:
                updates.append(uri)
        deletes = list(apply_result.deleted_uris)
        return {
            "memory_type": memory_type,
            "adds": adds,
            "updates": updates,
            "deletes": deletes,
            "total_adds": len(adds),
            "total_updates": len(updates),
            "total_deletes": len(deletes),
            "errors": [str(err) for _uri, err in apply_result.errors],
        }


__all__ = ["MemoryCompileRunner"]
