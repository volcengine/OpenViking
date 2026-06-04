from __future__ import annotations

import uuid
from typing import Any

from vikingbot.session.manager import Session


def get_openviking_state(session: Session) -> dict[str, Any]:
    state = session.metadata.get("openviking")
    if not isinstance(state, dict):
        state = {}
        session.metadata["openviking"] = state
    return state


def parse_local_index(value: Any, default: int = -1) -> int:
    try:
        index = int(value)
    except (TypeError, ValueError):
        index = default
    return max(index, -1)


def get_openviking_session_id(
    session: Session,
    *,
    rotate: bool = False,
    default_session_id: str | None = None,
) -> str:
    state = get_openviking_state(session)
    current_session_id = str(state.get("session_id") or "").strip()
    if current_session_id and not rotate:
        return current_session_id

    if default_session_id:
        base_session_id = default_session_id
    else:
        base_session_id = session.key.safe_name()
    session_id = f"{base_session_id}-{uuid.uuid4().hex[:8]}" if rotate else base_session_id
    state["session_id"] = session_id
    return session_id


def reset_openviking_state(session: Session, *, rotate_session_id: bool = False) -> None:
    session_id = get_openviking_session_id(session, rotate=rotate_session_id)
    session.metadata["openviking"] = {
        "session_id": session_id,
        "last_synced_local_index": -1,
        "last_sender_synced_local_indexes": {},
        "last_pending_tokens": 0,
        "last_commit_local_index": -1,
        "last_sync_status": "reset",
    }


def get_unsynced_messages(
    session: Session, *, cursor_key: str = "last_synced_local_index"
) -> list[dict[str, Any]]:
    state = get_openviking_state(session)
    last_synced_local_index = parse_local_index(state.get(cursor_key, -1))
    if last_synced_local_index >= len(session.messages):
        return []
    start = last_synced_local_index + 1
    return list(session.messages[start:]) if start < len(session.messages) else []


def get_sender_synced_local_indexes(session: Session) -> dict[str, int]:
    state = get_openviking_state(session)
    raw_indexes = state.get("last_sender_synced_local_indexes")
    if not isinstance(raw_indexes, dict):
        raw_indexes = {}
        state["last_sender_synced_local_indexes"] = raw_indexes
    return {str(sender_id): parse_local_index(index) for sender_id, index in raw_indexes.items()}


def set_sender_synced_local_index(session: Session, sender_id: str, index: int) -> None:
    state = get_openviking_state(session)
    raw_indexes = state.get("last_sender_synced_local_indexes")
    if not isinstance(raw_indexes, dict):
        raw_indexes = {}
        state["last_sender_synced_local_indexes"] = raw_indexes
    raw_indexes[str(sender_id)] = parse_local_index(index)


def get_unsynced_messages_for_sender(
    session: Session,
    sender_id: str,
    *,
    admin_user_id: str | None = None,
) -> list[dict[str, Any]]:
    sender_indexes = get_sender_synced_local_indexes(session)
    state = get_openviking_state(session)
    last_synced_local_index = sender_indexes.get(
        str(sender_id),
        parse_local_index(state.get("last_synced_local_index", -1)),
    )
    if last_synced_local_index >= len(session.messages):
        return []
    start = last_synced_local_index + 1
    return [
        message
        for message in session.messages[start:]
        if message.get("sender_id") == sender_id and sender_id != admin_user_id
    ]
