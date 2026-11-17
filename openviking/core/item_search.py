# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public Item Search request options."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class SearchAsItemOptions(BaseModel):
    """Options that switch find/search from chunk hits to declared Item roots."""

    model_config = ConfigDict(extra="forbid")

    aggregation: Literal["max", "sum"] = "max"


def validate_item_search_request(
    options: SearchAsItemOptions | dict[str, Any] | None,
    *,
    mode: str = "list",
    read_content: bool = False,
    include_provenance: bool = False,
    events_time_decay_protection: Any = None,
) -> SearchAsItemOptions | None:
    """Normalize Item Search options and enforce its cross-entrypoint constraints."""
    if options is None:
        return None
    normalized = SearchAsItemOptions.model_validate(options)
    if mode != "list":
        raise ValueError("search_as_item requires mode='list'")
    if read_content:
        raise ValueError("read_content is not supported with search_as_item")
    if include_provenance:
        raise ValueError("include_provenance is not supported with search_as_item")
    if events_time_decay_protection is not None:
        raise ValueError("Item Search does not support event time decay")
    return normalized
