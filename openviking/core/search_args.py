# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Validation for the public ``args`` map on retrieval requests.

``args`` is the OpenViking-facing advanced-option bag. Only keyword search
exposes engine-level knobs today, so any other search type rejects the map
instead of silently ignoring it.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

KEYWORDS_SEARCH_ARG_KEYS = ("bm25_k1", "bm25_b", "post_process_input_limit")
MAX_POST_PROCESS_INPUT_LIMIT = 2_000_000


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def validate_search_args(
    args: Optional[Mapping[str, Any]],
    *,
    search_type: str,
    limit: int,
) -> Optional[Dict[str, Any]]:
    """Validate and normalize one request's ``args`` map.

    ``limit`` is the effective recall limit of the request (``node_limit`` when
    supplied, otherwise ``limit``); ``post_process_input_limit`` may not be
    smaller, because the engine feeds at least the recall top-k into
    post-processing.
    """
    if args is None:
        return None
    if not isinstance(args, Mapping):
        raise ValueError("args must be an object")
    if search_type != "keywords":
        raise ValueError("args are only supported when search_type='keywords'")

    unknown = sorted(set(args) - set(KEYWORDS_SEARCH_ARG_KEYS))
    if unknown:
        raise ValueError(
            f"unknown args keys: {', '.join(unknown)}; "
            f"allowed: {', '.join(KEYWORDS_SEARCH_ARG_KEYS)}"
        )

    normalized = dict(args)
    if "bm25_k1" in normalized:
        bm25_k1 = _finite_number(normalized["bm25_k1"], "args.bm25_k1")
        if bm25_k1 <= 0:
            raise ValueError("args.bm25_k1 must be greater than 0")
        normalized["bm25_k1"] = bm25_k1

    if "bm25_b" in normalized:
        bm25_b = _finite_number(normalized["bm25_b"], "args.bm25_b")
        if not 0 <= bm25_b <= 1:
            raise ValueError("args.bm25_b must be between 0 and 1")
        normalized["bm25_b"] = bm25_b

    if "post_process_input_limit" in normalized:
        value = normalized["post_process_input_limit"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("args.post_process_input_limit must be an integer")
        if value < limit:
            raise ValueError(
                "args.post_process_input_limit must be greater than or equal to "
                f"the search limit ({limit})"
            )
        if value > MAX_POST_PROCESS_INPUT_LIMIT:
            raise ValueError(
                "args.post_process_input_limit must not be greater than "
                f"{MAX_POST_PROCESS_INPUT_LIMIT}"
            )

    return normalized
