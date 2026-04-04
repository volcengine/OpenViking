# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UpsertDataResult:
    ids: List[Any] = field(default_factory=list)


@dataclass
class DataItem:
    id: Any = None
    fields: Optional[Dict[str, Any]] = None


@dataclass
class FetchDataInCollectionResult:
    items: List[DataItem] = field(default_factory=list)
    ids_not_exist: List[Any] = field(default_factory=list)


@dataclass
class SearchItemResult:
    id: Any = None
    fields: Optional[Dict[str, Any]] = None
    score: Optional[float] = None


@dataclass
class SearchResult:
    data: List[SearchItemResult] = field(default_factory=list)


@dataclass
class AggregateResult:
    """Result of aggregation operation.

    Attributes:
        agg: Aggregation result dictionary
             - Total count: {"_total": <count>}
             - Grouped count: {"value1": count1, "value2": count2, ...}
        op: Aggregation operation name (e.g., "count")
        field: Field name used for grouping (None for total count)
    """

    agg: Dict[str, Any] = field(default_factory=dict)
    op: str = "count"
    field: Optional[str] = None
