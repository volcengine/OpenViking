"""Persist-before-send Session sync, under the Bot's existing Session owner.

This module adds no queue, retry loop or second locking mechanism. A persisted
batch is replayed unchanged after a lost response, including when the option is
subsequently disabled. The caller must serialize writes to a Bot Session.
"""

from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4

from vikingbot.openviking_mount.session_state import get_openviking_state, parse_local_index


def _digest(messages):
    data = json.dumps(messages, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


async def sync_pending_append(session, client, session_id, user_id, persist_session):
    if not callable(persist_session):
        raise RuntimeError("Receipt-aware Session sync requires a durable persistence callback")
    state = get_openviking_state(session)
    identity = await client.session_append_identity(user_id)
    pending = state.get("pending_append")
    if "pending_append" not in state:
        start = parse_local_index(state.get("last_synced_local_index", -1)) + 1
        end = len(session.messages) - 1
        if end < start:
            return None
        pending = {
            "version": 1,
            "key": uuid4().hex,
            "identity": identity,
            "session_id": session_id,
            "start": start,
            "end": end,
            "messages": copy.deepcopy(session.messages[start : end + 1]),
            "prefix_digest": _digest(session.messages[: end + 1]),
        }
        state["pending_append"] = pending
    if (
        not isinstance(pending, dict)
        or type(pending.get("version")) is not int
        or pending["version"] != 1
        or pending.get("identity") != identity
        or pending.get("session_id") != session_id
        or not isinstance(pending.get("key"), str)
        or not pending["key"]
        or type(pending.get("start")) is not int
        or type(pending.get("end")) is not int
        or not 0 <= pending["start"] <= pending["end"] < len(session.messages)
        or pending.get("prefix_digest") != _digest(session.messages[: pending["end"] + 1])
        or pending.get("messages") != session.messages[pending["start"] : pending["end"] + 1]
    ):
        raise RuntimeError("Pending OpenViking append changed identity or history; reconcile first")
    # Repeat persistence even for an in-memory retry after a failed local save.
    # No remote write can precede this acknowledgement.
    await persist_session(session)
    result = await client.append_messages(
        session_id=session_id,
        messages=copy.deepcopy(pending["messages"]),
        default_user_peer_id=None,
        session_user_id=user_id,
        idempotency_key=pending["key"],
    )
    prior = copy.deepcopy(state)
    state["last_synced_local_index"] = pending["end"]
    state.pop("pending_append", None)
    try:
        await persist_session(session)
    except BaseException:
        # A retry replays the same key even if the local ACK write was uncertain.
        state.clear()
        state.update(prior)
        raise
    return result
