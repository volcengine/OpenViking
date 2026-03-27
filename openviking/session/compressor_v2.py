# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Session Compressor V2 for OpenViking.

Uses the new Memory Templating System with ReAct orchestrator.
Maintains the same interface as compressor.py for backward compatibility.
"""

import os
from typing import List, Optional

from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import MemoryReAct, MemoryTypeRegistry, MemoryUpdater
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry
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
        # Initialize registry once - used by both MemoryReAct and MemoryUpdater
        self._registry = MemoryTypeRegistry()

        # Load built-in templates
        builtin_templates_dir = os.path.join(
            os.path.dirname(__file__), "..", "prompts", "templates", "memory"
        )
        self._registry.load_from_directory(builtin_templates_dir)

        # Load custom templates from config if specified
        config = get_openviking_config()
        custom_templates_dir = config.memory.custom_templates_dir
        if custom_templates_dir:
            custom_dir = os.path.expanduser(custom_templates_dir)
            if os.path.exists(custom_dir):
                loaded = self._registry.load_from_directory(custom_dir)
                logger.info(f"Loaded {loaded} custom memory templates from {custom_dir}")
            else:
                logger.warning(f"Custom templates directory not found: {custom_dir}")

    def _get_or_create_react(self, ctx: Optional[RequestContext] = None) -> MemoryReAct:
        """Create new MemoryReAct instance with current ctx.

        Note: Always create new instance to avoid cross-session isolation issues.
        The ctx contains request-scoped state that must not be shared across requests.
        """
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()

        return MemoryReAct(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            registry=self._registry,
        )

    def _get_or_create_updater(self, transaction_handle=None) -> MemoryUpdater:
        """Create new MemoryUpdater instance for each request.

        Always create new instance to avoid cross-request state pollution.
        """
        return MemoryUpdater(
            registry=self._registry,
            vikingdb=self.vikingdb,
            transaction_handle=transaction_handle
        )

    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: Optional["UserIdentifier"] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
    ) -> List[Context]:
        """Extract long-term memories from messages using v2 templating system.

        Note: Returns empty List[Context] because v2 directly writes to storage.
        The list length is used for stats in session.py.
        """
        if not messages:
            return []

        if not ctx:
            logger.warning("No RequestContext provided, skipping memory extraction")
            return []

        # Provide the latest completed archive overview as non-actionable history context.
        conversation_sections: List[str] = []
        if latest_archive_overview:
            conversation_sections.append(f"## Previous Archive Overview\n{latest_archive_overview}")

        # Format messages including tool calls (if any)
        def format_message_with_parts(msg: Message) -> str:
            """Format message with text and tool parts."""
            import json
            from openviking.message.part import ToolPart

            parts = getattr(msg, "parts", [])
            # Check if there are any tool parts
            has_tool_parts = any(isinstance(p, ToolPart) for p in parts)

            if not has_tool_parts:
                # No tool parts, use simple content
                return msg.content

            # Has tool parts, format with them (tool calls first, then text)
            tool_lines = []
            text_lines = []
            for part in parts:
                if hasattr(part, "text") and part.text:
                    text_lines.append(part.text)
                elif isinstance(part, ToolPart):
                    tool_info = {
                        "type": "tool_call",
                        "tool_name": part.tool_name,
                        "tool_input": part.tool_input,
                        "tool_status": part.tool_status,
                    }
                    if part.skill_uri:
                        tool_info["skill_name"] = part.skill_uri.rstrip("/").split("/")[-1]
                    tool_lines.append(f"[ToolCall] {json.dumps(tool_info, ensure_ascii=False)}")

            # Combine: tool calls first, then text
            all_lines = tool_lines + text_lines
            return "\n".join(all_lines) if all_lines else msg.content

        conversation_sections.append(
            "\n".join([f"[{idx}][{msg.role}]: {format_message_with_parts(msg)}" for idx, msg in enumerate(messages)])
        )
        conversation_str = "\n\n".join(section for section in conversation_sections if section)

        logger.info("Starting v2 memory extraction from conversation")

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
        if viking_fs and hasattr(viking_fs, 'agfs') and viking_fs.agfs:
            init_lock_manager(viking_fs.agfs)
            lock_manager = get_lock_manager()
            transaction_handle = lock_manager.create_handle()
        else:
            logger.warning("VikingFS or AGFS not available, running without lock mechanism")


        try:
            # 获取所有记忆 schema 目录并加锁（仅在有锁管理器时）
            orchestrator = self._get_or_create_react(ctx=ctx)
            if lock_manager:
                memory_schema_dirs = orchestrator._get_all_memory_schema_dirs()
                logger.debug(f"Memory schema directories to lock: {memory_schema_dirs}")

                # 循环等待获取锁（机制确保不会死锁）
                # 由于使用有序加锁法，可以安全地无限等待
                while True:
                    lock_acquired = await lock_manager.acquire_subtree_batch(
                        transaction_handle,
                        memory_schema_dirs,
                        timeout=None,
                    )
                    if lock_acquired:
                        break
                    logger.warning("Failed to acquire memory locks, retrying...")

            orchestrator._transaction_handle = transaction_handle  # 传递给 MemoryReAct
            updater = self._get_or_create_updater(transaction_handle)

            # Run ReAct orchestrator
            operations, tools_used = await orchestrator.run(conversation=conversation_str, messages=messages)

            if operations is None:
                logger.info("No memory operations generated")
                return []

            logger.info(
                f"Generated memory operations: write={len(operations.write_uris)}, "
                f"edit={len(operations.edit_uris)}, edit_overview={len(operations.edit_overview_uris)}, "
                f"delete={len(operations.delete_uris)}"
            )

            # Create extract context from messages
            from openviking.session.memory.memory_updater import ExtractContext
            extract_context = ExtractContext(messages)

            # Apply operations
            result = await updater.apply_operations(operations, ctx, registry=orchestrator.registry, extract_context=extract_context)

            logger.info(
                f"Applied memory operations: written={len(result.written_uris)}, "
                f"edited={len(result.edited_uris)}, deleted={len(result.deleted_uris)}, "
                f"errors={len(result.errors)}"
            )

            # Report telemetry stats (matching v1 pattern)
            telemetry = get_current_telemetry()
            telemetry.set("memory.extract.candidates.total", len(result.written_uris) + len(result.edited_uris))
            telemetry.set("memory.extract.created", len(result.written_uris))
            telemetry.set("memory.extract.merged", len(result.edited_uris))
            telemetry.set("memory.extract.deleted", len(result.deleted_uris))
            telemetry.set("memory.extract.skipped", len(result.errors))

            # Build Context objects for stats in session.py
            contexts: List[Context] = []

            # Written memories
            for uri in result.written_uris:
                contexts.append(Context(
                    uri=uri,
                    category="memory_write",
                    context_type="memory",
                ))

            # Edited memories
            for uri in result.edited_uris:
                contexts.append(Context(
                    uri=uri,
                    category="memory_edit",
                    context_type="memory",
                ))

            # Deleted memories
            for uri in result.deleted_uris:
                contexts.append(Context(
                    uri=uri,
                    category="memory_delete",
                    context_type="memory",
                ))

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
