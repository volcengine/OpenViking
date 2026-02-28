# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Access-level helpers for OpenViking MCP tools."""

from __future__ import annotations

from enum import IntEnum


class MCPAccessLevel(IntEnum):
    """Ordered access levels for MCP tool authorization."""

    READONLY = 0
    INGEST = 1
    MUTATE = 2
    ADMIN = 3


ACCESS_LEVEL_ORDER = ("readonly", "ingest", "mutate", "admin")

_ACCESS_LEVEL_BY_NAME = {
    "readonly": MCPAccessLevel.READONLY,
    "ingest": MCPAccessLevel.INGEST,
    "mutate": MCPAccessLevel.MUTATE,
    "admin": MCPAccessLevel.ADMIN,
}


def parse_access_level(value: MCPAccessLevel | str) -> MCPAccessLevel:
    """Parse access-level input into MCPAccessLevel."""
    if isinstance(value, MCPAccessLevel):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ACCESS_LEVEL_BY_NAME:
            return _ACCESS_LEVEL_BY_NAME[normalized]
    allowed = ", ".join(ACCESS_LEVEL_ORDER)
    raise ValueError(f"Invalid access level '{value}'. Expected one of: {allowed}")


def access_level_name(value: MCPAccessLevel | str) -> str:
    """Return canonical lowercase name of an access level."""
    level = parse_access_level(value)
    for name, enum_value in _ACCESS_LEVEL_BY_NAME.items():
        if enum_value == level:
            return name
    return "readonly"


def can_access(
    current: MCPAccessLevel | str,
    required: MCPAccessLevel | str,
) -> bool:
    """Return whether current level can access required level."""
    return parse_access_level(current) >= parse_access_level(required)
