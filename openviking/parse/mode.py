# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared add-resource parsing modes."""

from enum import Enum
from typing import Literal, TypeAlias

from openviking_cli.exceptions import InvalidArgumentError


class ParseMode(str, Enum):
    """Controls whether parsed Markdown is split into multiple files."""

    DEFAULT = "default"
    NO_SPLIT = "no_split"


ParseModeInput: TypeAlias = ParseMode | Literal["default", "no_split"]


def normalize_parse_mode(value: str | ParseMode) -> ParseMode:
    """Return a validated resource parsing mode."""
    try:
        return ParseMode(value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError("parse_mode must be one of: default, no_split.") from exc
