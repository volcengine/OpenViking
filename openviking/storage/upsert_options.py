# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared options for vector-record upserts."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class RecordState(str, Enum):
    """Producer knowledge about whether a deterministic vector record exists."""

    NEW = "new"
    EXISTING = "existing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UpsertOptions:
    partial_update: bool = False
    search_tag_mode: str = "replace"
    record_state: RecordState = RecordState.UNKNOWN


def normalize_upsert_options(
    options: UpsertOptions | Mapping[str, Any] | None = None,
) -> UpsertOptions:
    if options is None:
        return UpsertOptions()
    if isinstance(options, UpsertOptions):
        return options
    raw_state = options.get("record_state", RecordState.UNKNOWN)
    try:
        record_state = RecordState(raw_state)
    except (TypeError, ValueError):
        record_state = RecordState.UNKNOWN
    return UpsertOptions(
        partial_update=bool(options.get("partial_update", False)),
        search_tag_mode=str(options.get("search_tag_mode", "replace")),
        record_state=record_state,
    )
