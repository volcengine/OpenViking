# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Agent Trajectory Context Provider - Phase 1 of agent-scope extraction.

Extracts execution trajectories from the conversation and can optionally
co-extract reusable executable skills in the same ReAct pass.
"""

from __future__ import annotations

from typing import Any, Dict, List

from openviking.server.identity import RequestContext
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.skill.session_skill_context_provider import (
    SESSION_SKILL_MEMORY_TYPE,
    SessionSkillContextProvider,
    resolve_skill_extract_templates_dir,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

TRAJECTORY_MEMORY_TYPE = "trajectories"


class AgentTrajectoryContextProvider(SessionExtractContextProvider):
    """Phase 1 provider: extract trajectories and optional session skills."""

    _SHARED_SKILL_STATE = {
        "messages",
        "latest_archive_overview",
        "_output_language",
        "_extract_context",
        "_isolation_handler",
        "_read_file_contents",
        "_ctx",
        "_viking_fs",
        "_transaction_handle",
    }

    def __init__(
        self,
        *args,
        include_trajectories: bool = True,
        include_session_skills: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._include_trajectories = include_trajectories
        self._include_session_skills = include_session_skills
        self._skill_provider = SessionSkillContextProvider(*args, **kwargs)
        self._sync_skill_provider_state()

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name not in self._SHARED_SKILL_STATE:
            return
        skill_provider = self.__dict__.get("_skill_provider")
        if skill_provider is not None:
            setattr(skill_provider, name, value)

    def _sync_skill_provider_state(self, *, include_extract_context: bool = False) -> None:
        skill_provider = getattr(self, "_skill_provider", None)
        if skill_provider is None:
            return
        if include_extract_context and self._extract_context is None:
            self.get_extract_context()
        for attr in self._SHARED_SKILL_STATE:
            setattr(skill_provider, attr, getattr(self, attr))

    def instruction(self) -> str:
        return (
            "You are an extraction agent. Analyze the archived conversation, use read when "
            "needed, and output only JSON that matches the schema descriptions."
        )

    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        """Expose trajectory schema and optionally session skill schema."""
        del ctx
        registry = self._get_registry()
        memory_types: List[str] = []
        if self._include_trajectories:
            memory_types.append(TRAJECTORY_MEMORY_TYPE)
        if self._include_session_skills:
            memory_types.append(SESSION_SKILL_MEMORY_TYPE)

        schemas: List[Any] = []
        for memory_type in memory_types:
            schema = registry.get(memory_type)
            if schema is None or not schema.enabled:
                continue
            schemas.append(schema)
        return schemas

    async def prefetch(self) -> List[Dict[str, Any]]:
        if not self._include_session_skills:
            if not isinstance(self.messages, list):
                logger.warning(f"Expected List[Message], got {type(self.messages)}")
                return []
            return [self._build_conversation_message()]
        self._sync_skill_provider_state()
        return await self._skill_provider.prefetch()

    async def execute_tool(self, tool_call) -> Any:
        if not self._include_session_skills:
            return await super().execute_tool(tool_call)
        self._sync_skill_provider_state(include_extract_context=True)
        return await self._skill_provider.execute_tool(tool_call)

    def get_tools(self) -> List[str]:
        return ["read"] if self._include_session_skills else []

    def _get_registry(self) -> MemoryTypeRegistry:
        if self._registry is None:
            registry = MemoryTypeRegistry(load_schemas=self._include_trajectories)
            if self._include_session_skills:
                loaded = registry.load_from_directory(str(resolve_skill_extract_templates_dir()))
                if loaded == 0:
                    raise RuntimeError(
                        "No session skill schemas loaded from skill_extract templates"
                    )
            self._registry = registry
        return self._registry
