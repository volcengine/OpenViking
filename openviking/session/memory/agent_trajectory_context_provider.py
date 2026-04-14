# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Agent Trajectory Context Provider - Phase 1 of agent-scope memory extraction.

Extracts execution trajectory summaries from the conversation. Only the
`trajectory` schema participates; no existing memories are prefetched because
trajectories are add_only.
"""

from typing import Any, Dict, List

from openviking.server.identity import RequestContext
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


TRAJECTORY_MEMORY_TYPE = "trajectory"


class AgentTrajectoryContextProvider(SessionExtractContextProvider):
    """Phase 1 provider: extract trajectory summaries from conversation."""

    def instruction(self) -> str:
        output_language = self._output_language
        return f"""You extract trajectory memories from agent conversations.

Follow field descriptions in the schema. Output JSON only.

All memory content must be written in {output_language}.
"""

    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        """Only expose the trajectory schema."""
        registry = self._get_registry()
        schema = registry.get(TRAJECTORY_MEMORY_TYPE)
        if schema is None or not schema.enabled:
            return []
        return [schema]

    async def prefetch(
        self,
        ctx: RequestContext,
        viking_fs: VikingFS,
        transaction_handle,
        vlm,
    ) -> List[Dict]:
        """Only inject the conversation. Trajectory is add_only so no ls/search."""
        if not isinstance(self.messages, list):
            logger.warning(f"Expected List[Message], got {type(self.messages)}")
            return []
        return [self._build_conversation_message()]

    def get_tools(self) -> List[str]:
        return ["read"]
