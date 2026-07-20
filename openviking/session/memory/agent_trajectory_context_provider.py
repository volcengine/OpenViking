# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Agent Trajectory Context Provider - Phase 1 of agent-scope extraction.

Extracts execution trajectories from the conversation and can optionally
co-extract reusable executable skills in the same ReAct pass.
"""

from __future__ import annotations

import json
import re
from html import unescape
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

_EXPERIENCE_REMINDER_RE = re.compile(
    r"<experience_reminder\b[^>]*>(?P<body>.*?)</experience_reminder>",
    re.IGNORECASE | re.DOTALL,
)
_EXPERIENCE_FIELD_RE = re.compile(
    r"<(?P<name>experience_name|experience_uri|triggered_before_tool)>\s*"
    r"(?P<value>.*?)\s*</(?P=name)>",
    re.IGNORECASE | re.DOTALL,
)


class AgentTrajectoryContextProvider(SessionExtractContextProvider):
    """Phase 1 provider: extract trajectories and optional session skills."""

    include_tool_parts_in_conversation = True
    split_long_text_messages_for_extraction = False

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
        evidence_sources: Dict[str, Any] | None = None,
        advisory_signals: Dict[str, Any] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._include_trajectories = include_trajectories
        self._include_session_skills = include_session_skills
        self._evidence_sources = dict(evidence_sources or {})
        self._advisory_signals = dict(advisory_signals or {})
        self._skill_provider = SessionSkillContextProvider(*args, **kwargs)
        self._injected_experience_reminders = extract_injected_experience_reminders(self.messages)
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
            "needed, and output only JSON that matches the schema descriptions. Runtime messages "
            "and evidence sources marked direct are factual evidence. Advisory signals must never "
            "be rewritten as observed causes without supporting direct evidence."
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

    def _build_conversation_message(self) -> Dict[str, Any]:
        message = super()._build_conversation_message()
        injected_context = render_injected_experience_context(self._injected_experience_reminders)
        evidence_context = render_extraction_evidence_context(
            evidence_sources=self._evidence_sources,
            advisory_signals=self._advisory_signals,
        )
        prefixes = [item for item in (evidence_context, injected_context) if item]
        if prefixes:
            message = dict(message)
            prefix_text = "\n\n".join(prefixes)
            message["content"] = f"{prefix_text}\n\n{message.get('content', '')}"
        return message

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


def extract_injected_experience_reminders(messages: Any) -> List[Dict[str, str]]:
    """Deterministically extract pre-tool experience reminders from rollout text."""

    seen: set[tuple[str, str, str]] = set()
    reminders: List[Dict[str, str]] = []
    for text in _iter_message_text(messages):
        for block in _EXPERIENCE_REMINDER_RE.finditer(text or ""):
            fields: Dict[str, str] = {}
            for match in _EXPERIENCE_FIELD_RE.finditer(block.group("body")):
                fields[match.group("name").lower()] = _clean_experience_field(match.group("value"))
            name = fields.get("experience_name", "")
            uri = fields.get("experience_uri", "")
            triggered_before = fields.get("triggered_before_tool", "")
            if not name and not uri:
                continue
            key = (uri, name, triggered_before)
            if key in seen:
                continue
            seen.add(key)
            reminders.append(
                {
                    "id": f"E{len(reminders) + 1}",
                    "experience_name": name,
                    "experience_uri": uri,
                    "triggered_before_tool": triggered_before or "unknown",
                }
            )
    return reminders


def render_injected_experience_context(reminders: List[Dict[str, str]]) -> str:
    """Render a deterministic alias list for the trajectory extraction LLM."""

    if not reminders:
        return ""
    lines = [
        "## Deterministic Injected Experience Reminders",
        "The following experience reminders were extracted deterministically from the rollout.",
        "Use only these IDs in the trajectory `experience_effects` field's "
        "`positive_ids`, `negative_ids`, and `weak_ids` arrays.",
    ]
    for item in reminders:
        lines.append(
            "- "
            f"{item['id']}: {item.get('experience_name') or '<unknown>'}; "
            f"uri={item.get('experience_uri') or '<unknown>'}; "
            f"triggered_before={item.get('triggered_before_tool') or 'unknown'}"
        )
    lines.extend(
        [
            'If this list is present, output only these IDs (for example `"E1"`) in the '
            "`experience_effects` ID lists; do not invent additional experience IDs.",
        ]
    )
    return "\n".join(lines)


def render_extraction_evidence_context(
    *,
    evidence_sources: Dict[str, Any],
    advisory_signals: Dict[str, Any],
) -> str:
    return "\n".join(
        [
            "## Evidence Source Contract",
            "- Conversation messages and tool results are runtime evidence.",
            "- Entries in Evidence Sources with `direct=true` are direct external evidence.",
            "- A `rollout_evaluation` direct source is authoritative for outcome and requirement compliance, including which expected results were missing or incorrect.",
            "- Outcome feedback does not independently prove an unobserved internal cause. Record the decisions and actions that actually occurred; keep the decision basis unknown when the runtime trajectory does not show it.",
            "- Advisory Signals are suggestions for locating evidence, not proof.",
            "- Never turn an advisory signal into an observed decision or root cause. If runtime and direct external evidence do not support a claim, record unknown/unverified.",
            "- In the Execution log, bind material facts to their runtime message, tool result, or direct external source with concise `# =>` comments or nearby factual comments.",
            "",
            "## Evidence Sources",
            json.dumps(
                evidence_sources or {"direct_available": False, "items": []},
                default=str,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "",
            "## Advisory Signals",
            json.dumps(
                advisory_signals or {"available": False, "items": []},
                default=str,
                ensure_ascii=False,
                sort_keys=True,
            ),
        ]
    )


def _iter_message_text(messages: Any) -> List[str]:
    texts: List[str] = []
    for message in messages or []:
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            texts.append(content)
        if isinstance(message, dict):
            dict_content = message.get("content")
            if isinstance(dict_content, str) and dict_content:
                texts.append(dict_content)
            parts = message.get("parts") or []
        else:
            parts = getattr(message, "parts", []) or []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
            else:
                text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                texts.append(text)
    return texts


def _clean_experience_field(value: str) -> str:
    return unescape(re.sub(r"\s+", " ", value or "").strip())
