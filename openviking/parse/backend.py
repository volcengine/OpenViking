# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Parser backend selection shared across resource-ingestion stages."""

from enum import Enum
from typing import Optional


class ParserBackend(str, Enum):
    INTERNAL = "internal"
    UNDERSTANDING = "understanding"


def normalize_parser_backend(value: object) -> Optional[ParserBackend]:
    """Normalize an optional parser backend at a transport boundary."""
    if value is None:
        return None
    try:
        return ParserBackend(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unknown parser backend: {value}") from exc
