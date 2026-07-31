# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared add-resource parsing modes."""

from enum import Enum
from typing import Literal, TypeAlias

from openviking_cli.exceptions import InvalidArgumentError


class ParseMode(str, Enum):
    """Controls whether add_resource runs format parsers."""

    DEFAULT = "default"
    NO_PARSE = "no_parse"


ParseModeInput: TypeAlias = ParseMode | Literal["default", "no_parse"]


def normalize_parse_mode(value: str | ParseMode) -> ParseMode:
    """Return a validated parse mode for HTTP and embedded callers."""
    try:
        return ParseMode(value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError("parse_mode must be one of: default, no_parse.") from exc
