# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openviking.storage.errors import ConnectionError


@dataclass
class UpsertDataResult:
    ids: List[Any] = field(default_factory=list)


def parse_remote_update_response(
    response: Any,
    request_ids: List[Any],
    operation: str,
    *,
    result_key: str = "result",
) -> Any:
    """Keep explicit update results, or acknowledge a validated remote success.

    VikingDB may only return token usage for a successful update. Convert that
    acknowledgement to IDs here, where transport and business errors are still
    distinguishable, rather than guessing from an empty adapter result.
    """
    if response.status_code != 200:
        raise ConnectionError(f"Request to {operation} failed: HTTP {response.status_code}")
    try:
        body = response.json() if hasattr(response, "json") else json.loads(response.text)
    except (ValueError, TypeError) as exc:
        raise ConnectionError(f"Request to {operation} failed: invalid JSON response") from exc
    if not isinstance(body, dict) or result_key not in body:
        raise ConnectionError(f"Request to {operation} failed: malformed update response")

    result = body[result_key]
    for envelope in (body, result):
        if not isinstance(envelope, dict):
            continue
        metadata = envelope.get("ResponseMetadata", {})
        error = metadata.get("Error") if isinstance(metadata, dict) else None
        if (
            error
            or envelope.get("error")
            or envelope.get("Error")
            or envelope.get("success") is False
            or envelope.get("status") in ("error", "failed", "failure")
            or envelope.get("code") not in (None, 0, "0", "Success")
        ):
            raise ConnectionError(f"Request to {operation} failed: {envelope}")
        if envelope.get("ids_not_exist"):
            raise ValueError(f"record not found for primary key(s): {envelope['ids_not_exist']}")

    # VikingDB explicitly permits null for operations without a result. Require
    # its affirmative success code so a missing or malformed result is not an ack.
    if result is None and result_key == "result" and body.get("code") == "Success":
        return UpsertDataResult(ids=list(request_ids))

    if isinstance(result, dict):
        if any(key in result for key in ("primary_keys", "ids", "updated")):
            return result
        # An empty result or token usage is the documented acknowledgement.
        # Unknown structures must not be mistaken for successful writes.
        if not result or (
            set(result) == {"token_usage"} and isinstance(result["token_usage"], dict)
        ):
            return UpsertDataResult(ids=list(request_ids))
    elif isinstance(result, list):
        return result
    raise ConnectionError(f"Request to {operation} failed: malformed update result")


@dataclass
class UpdateResult:
    ok: bool = False
    ids: List[str] = field(default_factory=list)
    updated_count: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None


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
    origin_score: Optional[float] = None
    addition_score: Optional[float] = None


@dataclass
class SearchResult:
    data: List[SearchItemResult] = field(default_factory=list)


def parse_remote_search_result(payload: Dict[str, Any]) -> SearchResult:
    """Parse the common VikingDB search response, including score details."""
    result = SearchResult()
    if not isinstance(payload, dict) or "data" not in payload:
        return result
    result.data = [
        SearchItemResult(
            id=item.get("id"),
            fields=item.get("fields"),
            score=item.get("score"),
            origin_score=item.get("origin_score"),
            addition_score=item.get("addition_score"),
        )
        for item in payload.get("data", [])
    ]
    return result


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
