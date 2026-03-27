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
        schemas_dir = os.path.join(
            os.path.dirname(__file__), "..", "prompts", "templates", "memory"
        )
        self._registry.load_from_directory(schemas_dir)
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

    def _get_or_create_updater(self) -> MemoryUpdater:
        """Get or create MemoryUpdater instance."""
        if self._memory_updater is not None:
            return self._memory_updater

        self._memory_updater = MemoryUpdater(registry=self._registry, vikingdb=self.vikingdb)
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

        try:
            # Initialize orchestrator
            orchestrator = self._get_or_create_react(ctx=ctx)
            updater = self._get_or_create_updater()

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
