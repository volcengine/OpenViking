# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Experience recall events for server-assembled context.

Harness auto-recall injects the assembled block outside any tool call, so the
commit-time transcript extractor never sees it. The server knows what it served,
so it reports those Experience URIs directly.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Optional

from openviking.session.memory.experience_lineage import is_experience_uri_for_user

from .models import UsageEvent, utc_now_iso

CONTEXT_RECALL_TOOL_NAME = "search.context"


def build_context_recall_events(
    *,
    entries: Iterable[Any],
    account_id: str,
    user_id: str,
    session_id: Optional[str],
) -> list[UsageEvent]:
    """One ``memory.recalled`` event per served Experience URI per request.

    The session turn is not a stable key: legacy sessions count it from the live
    message list, which restarts after every commit.
    """
    recall_key = f"req:{uuid.uuid4().hex}"
    occurred_at = utc_now_iso()
    events: list[UsageEvent] = []
    seen: set[str] = set()
    for entry in entries:
        uri = str(getattr(entry, "uri", "") or "")
        if uri in seen or not is_experience_uri_for_user(uri, user_id):
            continue
        seen.add(uri)
        events.append(
            UsageEvent(
                event_type="memory.recalled",
                resource_uri=uri,
                resource_type="experience",
                account_id=account_id,
                user_id=user_id,
                session_id=session_id or "",
                occurred_at=occurred_at,
                evidence={
                    "message_id": recall_key,
                    "tool_call_id": "",
                    "tool_name": CONTEXT_RECALL_TOOL_NAME,
                },
            )
        )
    return events
