# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Time helpers for Usage/Audit projections."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def resolve_usage_timezone(timezone_name: str) -> tzinfo:
    """Resolve the server-default Usage/Audit timezone with local fallback.

    Used only as the fallback when a request does not specify its own
    `?timezone=` parameter. Writes are always in UTC regardless of this value.
    """
    if not timezone_name or timezone_name == "local":
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown usage_audit timezone %s; falling back to local", timezone_name)
        return datetime.now().astimezone().tzinfo or timezone.utc


def resolve_user_timezone(timezone_name: str | None, *, fallback: tzinfo) -> tzinfo:
    """Resolve a request-supplied IANA tz name with a server-side fallback.

    Accepts e.g. `Asia/Shanghai`, `America/New_York`, or `UTC`. An unknown or
    empty value falls back to the server default and emits a debug log entry.
    """
    if not timezone_name:
        return fallback
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.debug("Unknown request timezone %r; falling back to server default", timezone_name)
        return fallback
