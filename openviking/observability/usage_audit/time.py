# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Time helpers for Usage/Audit projections."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def resolve_usage_timezone(timezone_name: str) -> tzinfo:
    """Resolve configured Usage/Audit timezone with local fallback."""
    if not timezone_name or timezone_name == "local":
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown usage_audit timezone %s; falling back to local", timezone_name)
        return datetime.now().astimezone().tzinfo or timezone.utc
