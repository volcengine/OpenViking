# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Intent analyzer for OpenViking retrieval.

Analyzes session context to generate query plans.
"""

from typing import Any, List, Optional

from openviking.message import Message
from openviking.prompts import render_prompt
from openviking_cli.retrieve.types import ContextType, QueryPlan, TypedQuery
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.llm import parse_json_from_response
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_INTENT_ANALYSIS_PROMPT = "retrieval.intent_analysis"
MAX_INTENT_ANALYSIS_QUERIES = 5
MAX_SFT_V4_INTENT_ANALYSIS_QUERIES = 15
DEFAULT_QUERY_PRIORITY = 3

# Model-specific query-planner prompts. Models not listed here keep using the
# default intent-analysis prompt for backward compatibility.
QUERY_PLANNER_PROMPT_BY_MODEL: dict[str, str] = {
    "ollama/guoxuter/ov_intent_analysis_sft:v7_q8": "retrieval.ov_intent_analysis_sft_v7",
    "ollama/guoxuter/ov_intent_analysis_sft:v4_q8": "retrieval.ov_intent_analysis_sft_v4",
}


def resolve_intent_analysis_prompt_id(query_planner: Any) -> str:
    """Return the prompt id expected by the configured query-planner model."""
    model = getattr(query_planner, "model", None)
    if not isinstance(model, str):
        return DEFAULT_INTENT_ANALYSIS_PROMPT
    return QUERY_PLANNER_PROMPT_BY_MODEL.get(model.strip(), DEFAULT_INTENT_ANALYSIS_PROMPT)


def _max_queries_for_prompt(prompt_id: str) -> int:
    """Return the largest query plan allowed by the selected prompt contract."""
    if prompt_id == "retrieval.ov_intent_analysis_sft_v4":
        # v4 permits up to five queries for each of the three context types.
        return MAX_SFT_V4_INTENT_ANALYSIS_QUERIES
    return MAX_INTENT_ANALYSIS_QUERIES


def _normalize_query_plan_response(parsed: Any) -> tuple[str, list[Any]]:
    """Normalize recoverable planner response shapes and reject ambiguous ones."""
    if parsed is None:
        raise ValueError("Failed to parse intent analysis response")

    if isinstance(parsed, list):
        parsed = {"reasoning": "", "queries": parsed}
    elif not isinstance(parsed, dict):
        raise ValueError(
            f"Intent analysis response must be a JSON object or array, got {type(parsed).__name__}"
        )

    if not parsed:
        raise ValueError("Failed to parse intent analysis response")

    # Some planners omit the wrapper even when they emit only one query.
    if "queries" not in parsed and "query" in parsed:
        parsed = {"reasoning": "", "queries": [parsed]}
    elif "queries" not in parsed and "reasoning" not in parsed:
        raise ValueError("Intent analysis response must contain 'queries' or 'query'")

    reasoning = parsed.get("reasoning", "")
    if reasoning is None:
        reasoning = ""
    elif not isinstance(reasoning, str):
        reasoning = str(reasoning)

    raw_queries = parsed.get("queries", [])
    if raw_queries is None:
        raw_queries = []
    elif isinstance(raw_queries, dict):
        raw_queries = [raw_queries]
    elif not isinstance(raw_queries, list):
        raise ValueError(
            "Intent analysis response field 'queries' must be a JSON array or object, got "
            f"{type(raw_queries).__name__}"
        )

    return reasoning, raw_queries


def _normalize_query_priority(value: Any) -> int:
    """Return a planner priority in the documented 1-5 range."""
    if isinstance(value, bool):
        return DEFAULT_QUERY_PRIORITY

    if isinstance(value, float):
        if not value.is_integer():
            return DEFAULT_QUERY_PRIORITY
        value = int(value)
    elif isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            return DEFAULT_QUERY_PRIORITY

    if isinstance(value, int) and 1 <= value <= 5:
        return value
    return DEFAULT_QUERY_PRIORITY


def _build_typed_queries(
    raw_queries: list[Any],
    max_queries: int = MAX_INTENT_ANALYSIS_QUERIES,
) -> list[TypedQuery]:
    """Build a bounded query list while preserving valid items from mixed output."""
    queries: list[TypedQuery] = []
    skipped_items = 0

    for index, raw_query in enumerate(raw_queries):
        if len(queries) >= max_queries:
            logger.warning(
                "[IntentAnalyzer] Ignoring "
                f"{len(raw_queries) - index} query item(s) beyond the "
                f"{max_queries}-query limit"
            )
            break

        if not isinstance(raw_query, dict):
            skipped_items += 1
            continue

        query_text = raw_query.get("query")
        if not isinstance(query_text, str) or not query_text.strip():
            skipped_items += 1
            continue
        query_text = query_text.strip()

        raw_context_type = raw_query.get("context_type", "resource")
        if isinstance(raw_context_type, str):
            raw_context_type = raw_context_type.strip().lower()
        try:
            query_context_type = ContextType(raw_context_type)
        except (TypeError, ValueError):
            query_context_type = ContextType.RESOURCE

        intent = raw_query.get("intent", "")
        if intent is None:
            intent = ""
        elif not isinstance(intent, str):
            intent = str(intent)

        queries.append(
            TypedQuery(
                query=query_text,
                context_type=query_context_type,
                intent=intent,
                priority=_normalize_query_priority(raw_query.get("priority", 3)),
            )
        )

    if skipped_items:
        logger.warning(f"[IntentAnalyzer] Skipped {skipped_items} malformed query item(s)")
    if raw_queries and not queries:
        raise ValueError("Intent analysis response does not contain any valid query objects")

    return queries


class IntentAnalyzer:
    """
    Intent analyzer: generates query plans from session context.

    Responsibilities:
    1. Integrate session context (compression + recent messages + current message)
    2. Call LLM to analyze intent
    3. Generate multiple TypedQueries for memory/resources/skill
    """

    # Limit content length (about 10000 tokens)
    MAX_COMPRESSION_SUMMARY_CHARS = 30000

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
        # Call the lightweight query planner when configured; otherwise keep using VLM.
        config = get_openviking_config()
        query_planner = config.get_query_planner()

        # Build context prompt. Some fine-tuned planner models expect a compact
        # prompt/output contract, selected by exact model mapping above.
        prompt_id = resolve_intent_analysis_prompt_id(query_planner)
        prompt = self._build_context_prompt(
            compression_summary,
            messages,
            current_message,
            context_type,
            target_abstract,
            prompt_id=prompt_id,
        )

        response = await query_planner.get_completion_async(prompt)

        # Parse result
        parsed = parse_json_from_response(response)
        reasoning, raw_queries = _normalize_query_plan_response(parsed)
        queries = _build_typed_queries(
            raw_queries,
            max_queries=_max_queries_for_prompt(prompt_id),
        )

        # Log analysis result
        for i, q in enumerate(queries):
            logger.info(
                f'  [{i + 1}] type={q.context_type.value}, priority={q.priority}, query="{q.query}"'
            )
        logger.debug(f"[IntentAnalyzer] Reasoning: {reasoning[:200]}...")

        return QueryPlan(
            queries=queries,
            session_context=self._summarize_context(compression_summary, current_message),
            reasoning=reasoning,
        )

    def _build_context_prompt(
        self,
        compression_summary: str,
        messages: List[Message],
        current_message: Optional[str],
        context_type: Optional[ContextType] = None,
        target_abstract: str = "",
        prompt_id: str = DEFAULT_INTENT_ANALYSIS_PROMPT,
    ) -> str:
        """Build prompt for intent analysis."""
        # Format compression info
        summary = self._truncate_text(compression_summary, self.MAX_COMPRESSION_SUMMARY_CHARS)
        summary = summary if summary else "None"

        # Format recent messages
        recent = messages[-self.max_recent_messages :] if messages else []
        recent_messages = (
            "\n".join(f"[{m.role}]: {m.content}" for m in recent if m.content) if recent else "None"
        )

        # Current message
        current = current_message if current_message else "None"

        return render_prompt(
            prompt_id,
            {
                "compression_summary": summary,
                "recent_messages": recent_messages,
                "current_message": current,
                "context_type": context_type.value if context_type else "",
                "target_abstract": target_abstract,
            },
        )

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        """Truncate text to avoid oversized prompt context."""
        if not text or len(text) <= max_chars:
            return text
        return text[: max_chars - 15] + "\n...(truncated)"

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
