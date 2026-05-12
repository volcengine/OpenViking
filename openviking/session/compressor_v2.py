# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Compressor V2 for OpenViking.

Uses the new Memory Templating System with ReAct orchestrator.
Maintains the same interface as compressor.py for backward compatibility.
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from openviking.core.context import Context
from openviking.core.namespace import (
    agent_space_fragment,
    user_space_fragment,
    to_user_space,
    to_agent_space,
)
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
from openviking.session.memory.utils.uri import render_template
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS, get_viking_fs
from openviking.session.memory.dataclass import ResolvedOperations
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.telemetry import get_current_telemetry, tracer
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


class SessionCompressorV2:
    """Session memory extractor with v2 templating system."""

    def __init__(
        self,
        vikingdb: VikingDBManager,
    ):
        """Initialize session compressor."""
        self.vikingdb = vikingdb
        pass

    def _get_or_create_react(
        self,
        ctx: Optional[RequestContext] = None,
        messages: Optional[List] = None,
        latest_archive_overview: str = "",
        isolation_handler: Optional[MemoryIsolationHandler] = None,
        transaction_handle=None,
    ) -> ExtractLoop:
        """Create new ExtractLoop instance with current ctx.

        Note: Always create new instance to avoid cross-session isolation issues.
        The ctx contains request-scoped state that must not be shared across requests.
        """
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()

        # Create context provider with messages (provider 负责加载 schema)
        from openviking.session.memory.session_extract_context_provider import (
            SessionExtractContextProvider,
        )

        context_provider = SessionExtractContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            isolation_handler=isolation_handler,
            ctx=ctx,
            viking_fs=viking_fs,
            transaction_handle=transaction_handle,
        )

        return ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )

    def _get_or_create_updater(self, registry, transaction_handle=None) -> MemoryUpdater:
        """Create new MemoryUpdater instance for each request.

        Always create new instance to avoid cross-request state pollution.
        """
        return MemoryUpdater(
            registry=registry, vikingdb=self.vikingdb, transaction_handle=transaction_handle
        )

    @tracer()
    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: Optional["UserIdentifier"] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        archive_uri: Optional[str] = None,
    ) -> List[Context]:
        """Extract long-term memories from messages using v2 templating system.

        Note: Returns empty List[Context] because v2 directly writes to storage.
        The list length is used for stats in session.py.

        Args:
            messages: Messages to extract memories from.
            user: User identifier.
            session_id: Session ID.
            ctx: Request context.
            strict_extract_errors: If True, raise exceptions on extraction errors.
            latest_archive_overview: Overview of latest archive for context.
            archive_uri: Archive URI for writing memory_diff.json.
        """

        if not messages:
            return []

        if not ctx:
            logger.warning("No RequestContext provided, skipping memory extraction")
            return []

        tracer.info("Starting v2 memory extraction from conversation")
        tracer.info(f"messages={JsonUtils.dumps(messages)}")
        config = get_openviking_config()

        # Initialize default memory files (soul.md, identity.md) if not exist
        from openviking.session.memory.memory_type_registry import create_default_registry

        registry = create_default_registry()
        await registry.initialize_memory_files(ctx)

        # Initialize telemetry to 0 (matching v1 pattern)
        telemetry = get_current_telemetry()
        telemetry.set("memory.extract.candidates.total", 0)
        telemetry.set("memory.extract.candidates.standard", 0)
        telemetry.set("memory.extract.candidates.tool_skill", 0)
        telemetry.set("memory.extract.created", 0)
        telemetry.set("memory.extract.merged", 0)
        telemetry.set("memory.extract.deleted", 0)
        telemetry.set("memory.extract.skipped", 0)

        from openviking.storage.transaction import get_lock_manager, init_lock_manager
        from openviking.storage.viking_fs import get_viking_fs

        # 初始化锁管理器（仅在有 AGFS 时使用锁机制）
        viking_fs = get_viking_fs()
        lock_manager = None
        transaction_handle = None
        if viking_fs and hasattr(viking_fs, "agfs") and viking_fs.agfs:
            init_lock_manager(viking_fs.agfs)
            lock_manager = get_lock_manager()
            transaction_handle = lock_manager.create_handle()
        else:
            logger.warning("VikingFS or AGFS not available, running without lock mechanism")

        try:
            # Create extract context from messages
            from openviking.session.memory.memory_updater import ExtractContext

            extract_context = ExtractContext(messages)

            # Create MemoryIsolationHandler
            isolation_handler = MemoryIsolationHandler(ctx, extract_context)
            isolation_handler.prepare_messages()
            # 获取所有记忆 schema 目录并加锁（仅在有锁管理器时）
            orchestrator = self._get_or_create_react(
                ctx=ctx,
                messages=messages,
                latest_archive_overview=latest_archive_overview,
                isolation_handler=isolation_handler,
                transaction_handle=transaction_handle,
            )
            read_scope = isolation_handler.get_read_scope()
            if lock_manager:
                # 基于 provider 的 schemas 生成目录列表
                schemas = orchestrator.context_provider.get_memory_schemas(ctx)
                memory_schema_dirs = []
                for schema in schemas:
                    if not schema.directory:
                        continue
                    for user_id in read_scope.user_ids:
                        for agent_id in read_scope.agent_ids:
                            dir_path = render_template(
                                schema.directory,
                                {
                                    "user_space": to_user_space(
                                        ctx.namespace_policy, user_id, agent_id
                                    ),
                                    "agent_space": to_agent_space(
                                        ctx.namespace_policy, user_id, agent_id
                                    ),
                                },
                            )
                            dir_path = viking_fs._uri_to_path(dir_path, ctx)
                            if dir_path not in memory_schema_dirs:
                                memory_schema_dirs.append(dir_path)
                logger.debug(f"Memory schema directories to lock: {memory_schema_dirs}")

                retry_interval = config.memory.v2_lock_retry_interval_seconds
                max_retries = config.memory.v2_lock_max_retries
                retry_count = 0

                # 循环重试获取锁（机制确保不会死锁）
                while True:
                    lock_acquired = await lock_manager.acquire_subtree_batch(
                        transaction_handle,
                        memory_schema_dirs,
                        timeout=None,
                    )
                    if lock_acquired:
                        break
                    retry_count += 1
                    if max_retries > 0 and retry_count >= max_retries:
                        raise TimeoutError(
                            "Failed to acquire memory locks after "
                            f"{retry_count} retries (max={max_retries})"
                        )

                    logger.warning(
                        "Failed to acquire memory locks, retrying "
                        f"(attempt={retry_count}, max={max_retries or 'unlimited'})..."
                    )
                    if retry_interval > 0:
                        await asyncio.sleep(retry_interval)

            orchestrator._transaction_handle = transaction_handle  # 传递给 ExtractLoop

            # Run ReAct orchestrator
            operations, tools_used = await orchestrator.run()

            if operations is None:
                tracer.info("No memory operations generated")
                return []

            updater = self._get_or_create_updater(registry, transaction_handle)

            # Apply operations with isolation_handler
            result = await updater.apply_operations(
                operations,
                ctx,
                extract_context=extract_context,
                isolation_handler=isolation_handler,
            )

            tracer.info(
                f"Applied memory operations: written={len(result.written_uris)}, "
                f"edited={len(result.edited_uris)}, deleted={len(result.deleted_uris)}, "
                f"errors={len(result.errors)}"
            )

            # Write memory_diff.json to archive directory
            if archive_uri and viking_fs:
                memory_diff = await self._build_memory_diff(
                    result=result,
                    operations=operations,
                    viking_fs=viking_fs,
                    ctx=ctx,
                    archive_uri=archive_uri,
                )
                await viking_fs.write_file(
                    uri=f"{archive_uri}/memory_diff.json",
                    content=json.dumps(memory_diff, ensure_ascii=False, indent=4),
                    ctx=ctx,
                )
                logger.info(f"Wrote memory_diff.json to {archive_uri}")

            # Report telemetry stats (matching v1 pattern)
            telemetry = get_current_telemetry()
            telemetry.set(
                "memory.extract.candidates.total",
                len(result.written_uris) + len(result.edited_uris),
            )
            telemetry.set("memory.extract.created", len(result.written_uris))
            telemetry.set("memory.extract.merged", len(result.edited_uris))
            telemetry.set("memory.extract.deleted", len(result.deleted_uris))
            telemetry.set("memory.extract.skipped", len(result.errors))

            # Build Context objects for stats in session.py
            contexts: List[Context] = []

            # Written memories
            for uri in result.written_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_write",
                        context_type="memory",
                    )
                )

            # Edited memories
            for uri in result.edited_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_edit",
                        context_type="memory",
                    )
                )

            # Deleted memories
            for uri in result.deleted_uris:
                contexts.append(
                    Context(
                        uri=uri,
                        category="memory_delete",
                        context_type="memory",
                    )
                )

            return contexts

        except Exception as e:
            logger.error(f"Failed to extract memories with v2: {e}", exc_info=True)
            if strict_extract_errors:
                raise
            return []
        finally:
            # 确保释放所有锁（仅在有锁管理器时）
            if lock_manager and transaction_handle:
                try:
                    await lock_manager.release(transaction_handle)
                except Exception as e:
                    logger.warning(f"Failed to release transaction lock: {e}")

    async def _build_memory_diff(
        self,
        result: MemoryUpdateResult,
        operations: ResolvedOperations,
        viking_fs: VikingFS,
        ctx: RequestContext,
        archive_uri: str = "",
    ) -> Dict[str, Any]:
        """Build memory_diff.json structure from operations and result.

        Args:
            result: Memory update result containing written/edited/deleted URIs.
            operations: Resolved operations containing original content.
            viking_fs: VikingFS instance for reading file contents.
            ctx: Request context.
            archive_uri: The archive URI for this extraction.

        Returns:
            Dictionary containing memory_diff structure.
        """
        adds = []
        updates = []
        deletes = []

        # Build lookup maps for efficient access
        upsert_by_uri = {op.uris[0]: op for op in operations.upsert_operations if op.uris}
        delete_by_uri = {dc.uri: dc for dc in operations.delete_file_contents}

        # Process written_uris - distinguish between add and update
        # Use old_memory_file_content from the operation to determine if this is
        # an update (old content existed) or a new add.
        for uri in result.written_uris:
            op = upsert_by_uri.get(uri)
            memory_type = op.memory_type if op else self._get_memory_type_from_uri(uri)
            old_file = op.old_memory_file_content if op else None

            if old_file:
                # Old content existed, this is an update
                raw_before = old_file.plain_content
                parsed = parse_memory_file_with_fields(raw_before)
                updates.append(
                    {
                        "uri": uri,
                        "memory_type": memory_type,
                        "before": parsed.get("content", raw_before),
                        "after": "",  # Will be filled after
                    }
                )
            else:
                # No old content, this is a new add
                adds.append(
                    {
                        "uri": uri,
                        "memory_type": memory_type,
                        "after": "",  # Will be filled after
                    }
                )

        # Process edited_uris - these are updates
        for uri in result.edited_uris:
            op = upsert_by_uri.get(uri)
            memory_type = op.memory_type if op else self._get_memory_type_from_uri(uri)
            old_content = None
            if op and op.old_memory_file_content:
                old_content = op.old_memory_file_content.plain_content
            raw_before = old_content or ""
            parsed = parse_memory_file_with_fields(raw_before)
            updates.append(
                {
                    "uri": uri,
                    "memory_type": memory_type,
                    "before": parsed.get("content", raw_before) if raw_before else "",
                    "after": "",  # Will be filled after
                }
            )

        # Process deleted_uris - from delete_file_contents
        for uri in result.deleted_uris:
            deleted_content = None
            dc = delete_by_uri.get(uri)
            memory_type = dc.memory_fields.get("memory_type", "unknown") if dc else "unknown"
            if dc:
                deleted_content = dc.plain_content
            raw_deleted = deleted_content or ""
            parsed = parse_memory_file_with_fields(raw_deleted)
            deletes.append(
                {
                    "uri": uri,
                    "memory_type": memory_type,
                    "deleted_content": parsed.get("content", raw_deleted),
                }
            )

        # Read new content for adds and updates
        for item in adds + updates:
            try:
                content = await viking_fs.read_file(uri=item["uri"], ctx=ctx)
                # Strip MEMORY_FIELDS comment from content
                parsed = parse_memory_file_with_fields(content)
                item["after"] = parsed.get("content", content)
            except Exception:
                pass

        return {
            "archive_uri": archive_uri,
            "extracted_at": datetime.utcnow().isoformat() + "Z",
            "operations": {
                "adds": adds,
                "updates": updates,
                "deletes": deletes,
            },
            "summary": {
                "total_adds": len(adds),
                "total_updates": len(updates),
                "total_deletes": len(deletes),
            },
        }

    def _get_memory_type_from_uri(self, uri: str) -> str:
        """Extract memory type from URI.

        Examples:
            memory/user/xxx/identity.md -> identity
            memory/user/xxx/context/project.md -> context

        Args:
            uri: Memory file URI.

        Returns:
            Memory type (filename without extension) or 'unknown'.
        """
        parts = uri.split("/")
        for part in parts:
            if part.endswith(".md"):
                return part.replace(".md", "")
        return "unknown"
