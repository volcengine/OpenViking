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
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


TRAJECTORY_MEMORY_TYPE = "trajectories"


class AgentTrajectoryContextProvider(SessionExtractContextProvider):
    """Phase 1 provider: extract reusable trajectory-view memories."""

    def instruction(self) -> str:
        output_language = self._output_language
        return f"""You are a memory extraction agent. Convert this session into reusable trajectory-view memories.

Each memory is a compact operation contract for a future agent, not a transcript.
Capture trigger, prerequisites, verified boundaries, procedure, provenance,
anti-patterns, and applicability. Split separate intents, pivots, enabling writes,
and final writes into separate records; omit low-value side work. Use only schema
operation-family enum values, not compound labels. Generalize away raw identifiers,
names, exact dates, amounts, routes, and tool payloads. Do not let a later
operation in the session rename, narrow, or extend an earlier operation record.
Stop each record at its family boundary; do not include a second lifecycle-
changing write in the same operation contract.

Output JSON only: {{"trajectories": [...]}}. Include items only for reusable
operation contracts supported by the session. Follow field descriptions in the
schema. All content fields must be written in {output_language}.
"""

    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        """Only expose the trajectory schema."""
        registry = self._get_registry()
        schema = registry.get(TRAJECTORY_MEMORY_TYPE)
        if schema is None or not schema.enabled:
            return []
        return [schema]

    async def prefetch(self) -> List[Dict]:
        """Only inject the conversation. Trajectory is add_only so no ls/search."""
        if not isinstance(self.messages, list):
            logger.warning(f"Expected List[Message], got {type(self.messages)}")
            return []
        return [self._build_conversation_message()]

    def get_tools(self) -> List[str]:
        return []
