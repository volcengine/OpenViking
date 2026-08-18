from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_actor_peer_id: ContextVar[str | None] = ContextVar(
    "openviking_actor_peer_id",
    default=None,
)


def _normalize_actor_peer_id(actor_peer_id: str | None) -> str | None:
    if actor_peer_id is None:
        return None
    if not isinstance(actor_peer_id, str):
        raise TypeError("actor_peer_id must be a string or None")
    normalized = actor_peer_id.strip()
    if not normalized:
        raise ValueError("actor_peer_id must not be empty")
    return normalized


@contextmanager
def use_actor_peer(actor_peer_id: str | None) -> Iterator[None]:
    """Select an actor peer for HTTP calls in the current execution context."""

    token = _actor_peer_id.set(_normalize_actor_peer_id(actor_peer_id))
    try:
        yield
    finally:
        _actor_peer_id.reset(token)


def get_actor_peer_id() -> str | None:
    """Return the actor peer active in the current execution context, if any."""

    return _actor_peer_id.get()


def _request_actor_peer_headers() -> dict[str, str]:
    actor_peer_id = get_actor_peer_id()
    if actor_peer_id is None:
        return {}
    return {"X-OpenViking-Actor-Peer": actor_peer_id}
