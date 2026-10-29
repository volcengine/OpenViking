# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility bridge for request-scoped actor peers in openviking-sdk."""

from __future__ import annotations

from contextlib import AbstractContextManager
from importlib import import_module
from typing import Callable, cast

_openviking_sdk = import_module("openviking_sdk")
_actor_peer_getter = getattr(_openviking_sdk, "get_actor_peer_id", None)
_actor_peer_scope = getattr(_openviking_sdk, "use_actor_peer", None)


def has_request_actor_peer_support() -> bool:
    """Return whether the installed SDK supports request-scoped actor peers."""

    return _actor_peer_getter is not None and _actor_peer_scope is not None


def require_request_actor_peer_support() -> None:
    """Raise an actionable error when request-scoped actor peers are unavailable."""

    if not has_request_actor_peer_support():
        raise ImportError(
            "Runtime actor peers require an openviking-sdk release with "
            "request-scoped actor-peer support. Upgrade openviking-sdk and retry."
        )


def get_actor_peer_id() -> str | None:
    """Return the active SDK actor peer, tolerating older SDKs when unused."""

    if _actor_peer_getter is None:
        return None
    return cast(Callable[[], str | None], _actor_peer_getter)()


def use_actor_peer(actor_peer_id: str | None) -> AbstractContextManager[None]:
    """Apply an SDK actor-peer scope after confirming SDK support."""

    require_request_actor_peer_support()
    scope_factory = cast(
        Callable[[str | None], AbstractContextManager[None]],
        _actor_peer_scope,
    )
    return scope_factory(actor_peer_id)
