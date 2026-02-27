# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Filter expression AST for vector store queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Union


@dataclass(frozen=True)
class And:
    conds: List["FilterExpr"]


@dataclass(frozen=True)
class Or:
    conds: List["FilterExpr"]


@dataclass(frozen=True)
class Eq:
    field: str
    value: Any


@dataclass(frozen=True)
class In:
    field: str
    values: List[Any]


@dataclass(frozen=True)
class Range:
    field: str
    gte: Any | None = None
    gt: Any | None = None
    lte: Any | None = None
    lt: Any | None = None


@dataclass(frozen=True)
class Contains:
    field: str
    substring: str


@dataclass(frozen=True)
class TimeRange:
    field: str
    start: datetime | str | None = None
    end: datetime | str | None = None


@dataclass(frozen=True)
class RawDSL:
    payload: Dict[str, Any]


FilterExpr = Union[And, Or, Eq, In, Range, Contains, TimeRange, RawDSL]
