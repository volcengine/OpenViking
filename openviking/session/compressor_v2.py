# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Session Compressor V2 for OpenViking.

Uses the new Memory Templating System with ReAct orchestrator.
Maintains the same interface as compressor.py for backward compatibility.
"""

import os
from dataclasses import dataclass
from typing import List, Optional

from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

from openviking.session.memory import MemoryReAct, MemoryUpdater, MemoryTypeRegistry

logger = get_logger(__name__)


@dataclass
class ExtractionStats:
    """Statistics for memory extraction."""

    created: int = 0
    merged: int = 0
    deleted: int = 0
    skipped: int = 0


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

        # Lazy initialize MemoryReAct - we need vlm and ctx
        self._react_orchestrator: Optional[MemoryReAct] = None
        self._memory_updater: Optional[MemoryUpdater] = None

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
        """Get or create MemoryUpdater instance."""
        if self._memory_updater is not None:
            # 更新现有实例的 transaction_handle
            self._memory_updater._transaction_handle = transaction_handle
            return self._memory_updater

        self._memory_updater = MemoryUpdater(
            registry=self._registry,
            vikingdb=self.vikingdb,
            transaction_handle=transaction_handle
        )
        return self._memory_updater

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

        conversation_sections.append(
            "\n".join([f"[{msg.role}]: {msg.content}" for msg in messages])
        )
        conversation_str = "\n\n".join(section for section in conversation_sections if section)

        logger.info("Starting v2 memory extraction from conversation")

        from openviking.storage.transaction import init_lock_manager, get_lock_manager
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

                # 使用 batch 加锁获取所有目录的子树锁，防止死锁
                lock_acquired = await lock_manager.acquire_subtree_batch(
                    transaction_handle,
                    memory_schema_dirs,
                    timeout=None,
                )

                if not lock_acquired:
                    logger.error("Failed to acquire memory schema directory locks")
                    return []

            orchestrator._transaction_handle = transaction_handle  # 传递给 MemoryReAct
            updater = self._get_or_create_updater(transaction_handle)

            # Run ReAct orchestrator
            operations, tools_used = await orchestrator.run(conversation=conversation_str)

            if operations is None:
                logger.info("No memory operations generated")
                return []

            logger.info(
                f"Generated memory operations: write={len(operations.write_uris)}, "
                f"edit={len(operations.edit_uris)}, edit_overview={len(operations.edit_overview_uris)}, "
                f"delete={len(operations.delete_uris)}"
            )

            # Apply operations
            result = await updater.apply_operations(operations, ctx, registry=orchestrator.registry)

            logger.info(
                f"Applied memory operations: written={len(result.written_uris)}, "
                f"edited={len(result.edited_uris)}, deleted={len(result.deleted_uris)}, "
                f"errors={len(result.errors)}"
            )

            # Return list with dummy values to preserve count for stats in session.py
            # v2 directly writes to storage, so we return None objects to maintain len() accuracy
            total_changes = (
                len(result.written_uris) + len(result.edited_uris) + len(result.deleted_uris)
            )
            return [None] * total_changes

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
