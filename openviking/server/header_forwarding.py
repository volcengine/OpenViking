# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for safely forwarding selected inbound HTTP headers."""

from collections.abc import Mapping


def extract_forward_headers(
    headers: Mapping[str, str],
    whitelist: list[str] | tuple[str, ...] | set[str] | None,
) -> dict[str, str]:
    """Return only headers whose names appear in the allowlist.

    Matching is case-insensitive. The returned mapping normalizes names to
    lowercase so downstream callers can treat them consistently.
    """
    if not whitelist:
        return {}

    allowed = {name.strip().lower() for name in whitelist if name and name.strip()}
    if not allowed:
        return {}

    forwarded: dict[str, str] = {}
    for name, value in headers.items():
        normalized = name.lower()
        if normalized in allowed:
            forwarded[normalized] = value
    return forwarded
