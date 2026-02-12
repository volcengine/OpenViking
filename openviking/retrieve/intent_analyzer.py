# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Intent analyzer for OpenViking retrieval.

Analyzes session context to generate query plans.
"""

from typing import List, Optional

from openviking.message import Message
from openviking.prompts import render_prompt
from openviking_cli.retrieve.types import ContextType, QueryPlan, TypedQuery
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.llm import parse_json_from_response
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class IntentAnalyzer:
    """
    Intent analyzer: generates query plans from session context.

    Responsibilities:
    1. Integrate session context (compression + recent messages + current message)
    2. Call LLM to analyze intent
    3. Generate multiple TypedQueries for memory/resources/skill
    """

    def __init__(self, max_recent_messages: int = 5):
        """Initialize intent analyzer."""
        self.max_recent_messages = max_recent_messages

    async def analyze(
        self,
        compression_summary: str,
        messages: List[Message],
        current_message: Optional[str] = None,
        context_type: Optional[ContextType] = None,
        target_abstract: str = "",
    ) -> QueryPlan:
        """Analyze session context and generate query plan.

        Args:
            compression_summary: Session compression summary
            messages: Session message history
            current_message: Current message (if any)
            context_type: Constrained context type (only generate queries for this type)
            target_abstract: Target directory abstract for more precise queries
        """
        # Build context prompt
        prompt = self._build_context_prompt(
            compression_summary,
            messages,
            current_message,
            context_type,
            target_abstract,
        )

        # Call LLM
        response = await get_openviking_config().vlm.get_completion_async(prompt)

        # Parse result
        parsed = parse_json_from_response(response)
        if not parsed:
            raise ValueError("Failed to parse intent analysis response")

        # Build QueryPlan
        queries = []
        for q in parsed.get("queries", []):
            try:
                context_type = ContextType(q.get("context_type", "resource"))
            except ValueError:
                context_type = ContextType.RESOURCE

            queries.append(
                TypedQuery(
                    query=q.get("query", ""),
                    context_type=context_type,
                    intent=q.get("intent", ""),
                    priority=q.get("priority", 3),
                )
            )

        # Log analysis result
        for i, q in enumerate(queries):
            logger.info(
                f'  [{i + 1}] type={q.context_type.value}, priority={q.priority}, query="{q.query}"'
            )
        logger.debug(f"[IntentAnalyzer] Reasoning: {parsed.get('reasoning', '')[:200]}...")

        return QueryPlan(
            queries=queries,
            session_context=self._summarize_context(compression_summary, current_message),
            reasoning=parsed.get("reasoning", ""),
        )

    def _build_context_prompt(
        self,
        compression_summary: str,
        messages: List[Message],
        current_message: Optional[str],
        context_type: Optional[ContextType] = None,
        target_abstract: str = "",
    ) -> str:
        """Build prompt for intent analysis."""
        # Format compression info
        summary = compression_summary if compression_summary else "None"

        # Format recent messages
        recent = messages[-self.max_recent_messages :] if messages else []
        recent_messages = (
            "\n".join(f"[{m.role}]: {m.content}" for m in recent if m.content) if recent else "None"
        )

        # Current message
        current = current_message if current_message else "None"

        return render_prompt(
            "retrieval.intent_analysis",
            {
                "compression_summary": summary,
                "recent_messages": recent_messages,
                "current_message": current,
                "context_type": context_type.value if context_type else "",
                "target_abstract": target_abstract,
            },
        )

    def _summarize_context(
        self,
        compression_summary: str,
        current_message: Optional[str],
    ) -> str:
        """Generate context summary."""
        parts = []
        if compression_summary:
            parts.append(f"Session summary: {compression_summary}")
        if current_message:
            parts.append(f"Current message: {current_message[:100]}")
        return " | ".join(parts) if parts else "No context"
