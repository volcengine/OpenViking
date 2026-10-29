# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Recover durable handoffs by acquiring fresh, complete logical coverage."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from openviking.storage.errors import (
    HandoffRecoveryBlocked,
    HandoffRecoveryRequired,
    InvalidPathLockRequestError,
)
from openviking.storage.internal_names import MULTIWRITE_PATH_LOCK_FILE
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)
Requests = list[dict[str, str]]


def _validate_requests(requests: Any, account_id: str | None) -> Requests:
    if not isinstance(requests, list) or not requests:
        raise InvalidPathLockRequestError("handoff recovery requires complete logical coverage")
    validated = []
    for request in requests:
        if not isinstance(request, dict):
            raise InvalidPathLockRequestError("invalid handoff coverage")
        path, kind = request.get("path"), request.get("kind")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or (path != "/" and any(part in {".", "..", ""} for part in path[1:].split("/")))
            or "\x00" in path
            or kind not in {"exact", "tree"}
            or any(
                part == MULTIWRITE_PATH_LOCK_FILE or part.startswith(".exact.ovlock.")
                for part in path.split("/")
            )
        ):
            raise InvalidPathLockRequestError("invalid logical handoff coverage")
        if account_id is not None and not (
            path == f"/local/{account_id}" or path.startswith(f"/local/{account_id}/")
        ):
            raise InvalidPathLockRequestError("handoff coverage belongs to another account")
        validated.append({"path": path, "kind": kind})
    return validated


def _covers(held: dict[str, str], requested: dict[str, str]) -> bool:
    return (
        held["path"] == requested["path"]
        and (held["kind"] == "tree" or requested["kind"] == "exact")
    ) or (
        held["kind"] == "tree"
        and (held["path"] == "/" or requested["path"].startswith(held["path"] + "/"))
    )


async def adopt_or_reacquire(
    agfs: Any,
    handoff: dict[str, Any],
    *,
    ctx: Any = None,
    fallback_requests: Callable[[], Awaitable[Requests]] | None = None,
) -> Any:
    """Recover only typed adoption failures; acquire under a new owner.

    Callers must supply the complete operation scope for historic handles that
    cannot be inverted, and retain the result using a cancellation-safe guard.
    """
    fs_ctx = {"account_id": ctx.account_id} if ctx is not None else None
    kwargs = {"fs_ctx": fs_ctx} if fs_ctx is not None else {}
    try:
        return await agfs.pathlock_adopt(handoff, **kwargs)
    except HandoffRecoveryRequired as error:
        account_id = ctx.account_id if ctx is not None else None
        operation = (
            _validate_requests(await fallback_requests(), account_id)
            if fallback_requests is not None
            else None
        )
        requests = handoff.get("covered_paths")
        if not requests:
            suffix = f"/{MULTIWRITE_PATH_LOCK_FILE}"
            handles = handoff.get("lock_paths") or []
            # Never recover just the invertible subset of a mixed batch.
            if handles and all(isinstance(p, str) and p.endswith(suffix) for p in handles):
                requests = [{"path": p[: -len(suffix)] or "/", "kind": "tree"} for p in handles]
            else:
                requests = operation
        requests = _validate_requests(requests, account_id)
        if operation is not None and any(
            not any(_covers(held, required) for held in requests) for required in operation
        ):
            raise InvalidPathLockRequestError(
                "handoff coverage does not cover the queued operation"
            )
        try:
            lease = await agfs.pathlock_acquire_batch(requests, **kwargs)
        except ValueError:
            raise
        except Exception as blocked:
            raise HandoffRecoveryBlocked(str(blocked)) from blocked
        logger.info(
            "Recovered pathlock handoff owner=%s reason=%s coverage=%s",
            handoff.get("owner_id", handoff.get("handle_id")),
            error.reason,
            requests,
        )
        return lease
