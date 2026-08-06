# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded, session-aware query expansion.

Short prompts retrieve badly because the raw user sentence carries little
signal. Expansion borrows the session's recent turns to widen the query, under
a hard cap and a timeout, and fails closed to the original query.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Tuple

from openviking.retrieve.context_assembler.params import MAX_PLANNED_QUERIES
from openviking.retrieve.intent_analyzer import IntentAnalyzer
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

RECENT_MESSAGES = 5


async def expand_queries(
    *,
    query: str,
    session: Any,
    mode: str = "auto",
    timeout_s: float | None = None,
) -> Tuple[List[str], str]:
    """Return ``(queries, status)`` with the original query always first."""
    if mode != "auto" or session is None:
        return [query], "off"

    try:
        session_info = await session.get_context_for_search(query)
    except Exception as exc:
        logger.info("Session context unavailable; using original query: %s", exc)
        return [query], "off"

    summary = str(session_info.get("latest_archive_overview") or "")
    messages = session_info.get("current_messages") or []
    if not summary and not messages:
        return [query], "off"

    timeout = timeout_s or get_openviking_config().retrieval.recall_intent_timeout_s
    try:
        analyzer = IntentAnalyzer(max_recent_messages=RECENT_MESSAGES)
        plan = await asyncio.wait_for(
            analyzer.analyze(
                compression_summary=summary,
                messages=messages,
                current_message=query,
            ),
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("Query expansion failed; using original query: %s", exc)
        return [query], "failed"

    queries = [query]
    for typed_query in getattr(plan, "queries", None) or []:
        planned = str(getattr(typed_query, "query", "") or "").strip()
        if planned and planned not in queries:
            queries.append(planned)
        if len(queries) >= MAX_PLANNED_QUERIES:
            break
    return queries, "used"
