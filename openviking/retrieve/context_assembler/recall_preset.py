# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""``/recall`` as a thin preset over context assembly.

The endpoint keeps working for shipped plugins: it only overlays defaults and
folds the v1 field names onto the context contract. No assembly logic lives
here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from openviking.retrieve.context_assembler.params import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_QUOTAS,
    AssembleParams,
)

# v1 budgeted characters; ~4 chars per token for the ASCII case it was tuned on.
CHARS_PER_TOKEN = 4

# v1 defaults, kept so the deprecated request model still documents its own shape.
DEFAULT_MAX_CHARS = 6500
DEFAULT_MIN_SCORE = 0.1

# Preset overlay: what /recall assumes when the caller stays quiet.
RECALL_PURPOSE = "coding"
RECALL_SCORE_THRESHOLD = DEFAULT_MIN_SCORE
RECALL_DEDUP_TURNS = 5
RECALL_QUERY_EXPANSION = "auto"

V1_ALIASES = ("max_chars", "min_score", "render")


def _provided(values: Mapping[str, Any], provided: Set[str], key: str) -> bool:
    return key in provided and values.get(key) is not None


def fold_recall_request(
    values: Mapping[str, Any],
    provided: Set[str],
) -> Tuple[AssembleParams, List[str]]:
    """Map v1 recall fields onto :class:`AssembleParams`.

    ``provided`` marks fields the caller actually sent, so an omitted field
    takes the preset default while an explicit one always wins.
    """
    aliases: List[str] = [alias for alias in V1_ALIASES if alias in provided]

    if _provided(values, provided, "max_tokens"):
        max_tokens = int(values["max_tokens"])
    elif _provided(values, provided, "max_chars"):
        max_tokens = max(64, round(int(values["max_chars"]) / CHARS_PER_TOKEN))
    else:
        max_tokens = DEFAULT_MAX_TOKENS

    if _provided(values, provided, "score_threshold"):
        score_threshold = float(values["score_threshold"])
    elif _provided(values, provided, "min_score"):
        score_threshold = float(values["min_score"])
    else:
        score_threshold = RECALL_SCORE_THRESHOLD

    # v1 `render` is tri-state: True renders, False returns entries only, and
    # "compact" was the abstract-only prototype mode.
    render_value = values.get("render", True)
    render = render_value is not False
    detail: Any = None
    if _provided(values, provided, "detail"):
        detail = values["detail"]
    elif render_value == "compact":
        detail = "abstract"

    # Omitted quotas keep v1's bucket defaults; an explicit null opts into the
    # purpose preset instead. A partial map overlays the defaults the way v1's
    # normalize_quotas did — {"events": 5} never meant "drop the other buckets".
    quotas: Optional[Mapping[str, int]]
    if "quotas" in provided:
        explicit = values.get("quotas")
        quotas = {**DEFAULT_QUOTAS, **explicit} if isinstance(explicit, Mapping) else explicit
    else:
        quotas = dict(DEFAULT_QUOTAS)

    session_id = values.get("session_id")
    if _provided(values, provided, "dedup_turns"):
        dedup_turns = int(values["dedup_turns"])
    else:
        dedup_turns = RECALL_DEDUP_TURNS if session_id else 0

    query_expansion = values.get("query_expansion") or RECALL_QUERY_EXPANSION

    exclude_uris: Sequence[str] = values.get("exclude_uris") or ()

    params = AssembleParams(
        query=values.get("query", ""),
        limit=int(values.get("limit") or DEFAULT_LIMIT),
        score_threshold=score_threshold,
        filter=values.get("filter"),
        session_id=session_id,
        query_expansion=query_expansion,
        max_tokens=max_tokens,
        quotas=quotas,
        purpose=values.get("purpose") or RECALL_PURPOSE,
        detail=detail,
        dedup_turns=dedup_turns,
        exclude_uris=exclude_uris,
        peer_scope=values.get("peer_scope") or "all",
        other_peer_penalty=values.get("other_peer_penalty"),
        rewrite=values.get("rewrite", False),
        rewrite_max_bullets=int(values.get("rewrite_max_bullets") or 6),
        render=render,
    )
    return params, aliases


def deprecation_stats(aliases: Sequence[str]) -> Dict[str, Any]:
    return {
        "endpoint": "/api/v1/search/recall",
        "successor": "/api/v1/search/search",
        "successor_body": {"mode": "context"},
        "aliases_used": list(aliases),
    }
