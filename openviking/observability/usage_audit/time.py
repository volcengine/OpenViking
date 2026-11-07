# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Time helpers for Usage/Audit projections."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# `ZoneInfo()` rejects a bad key in two different ways: an unknown-but-well-formed
# name raises `ZoneInfoNotFoundError`, while a malformed one — absolute (`/UTC`),
# non-normalized (`Asia/`), or traversing (`..`) — raises `ValueError` before any
# lookup happens. Both mean "this is not a usable timezone", and both must fall
# back rather than propagate: the value reaches here straight from an untrusted
# `?timezone=` query parameter.
_INVALID_TIMEZONE = (ZoneInfoNotFoundError, ValueError)


def resolve_usage_timezone(timezone_name: str) -> tzinfo:
    """Resolve the server-default Usage/Audit timezone with local fallback.

    Used only as the fallback when a request does not specify its own
    `?timezone=` parameter. Writes are always in UTC regardless of this value.
    """
    if not timezone_name or timezone_name == "local":
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except (*_INVALID_TIMEZONE, TypeError):
        # TypeError as well here: this value comes from the config file, where a
        # bare `timezone: 8` parses as an int and would otherwise abort startup.
        logger.warning("Unknown usage_audit timezone %r; falling back to local", timezone_name)
        return datetime.now().astimezone().tzinfo or timezone.utc


def resolve_user_timezone(timezone_name: str | None, *, fallback: tzinfo) -> tzinfo:
    """Resolve a request-supplied IANA tz name with a server-side fallback.

    Accepts e.g. `Asia/Shanghai`, `America/New_York`, or `UTC`. An unknown,
    malformed, or empty value falls back to the server default and emits a debug
    log entry — the caller passes an untrusted query parameter straight through.
    """
    if not timezone_name:
        return fallback
    try:
        return ZoneInfo(timezone_name)
    except _INVALID_TIMEZONE:
        logger.debug("Unknown request timezone %r; falling back to server default", timezone_name)
        return fallback
