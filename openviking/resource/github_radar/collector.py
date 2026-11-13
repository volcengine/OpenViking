# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Pull-request collector over the GitHub issues REST endpoint.

The ``repos/{r}/issues`` endpoint returns both issues and pull requests; the
ONLY reliable discriminator is the presence of a ``pull_request`` key, so the
collector never guesses from URLs or number ranges.

Scans are strictly ASCENDING by ``updated_at``. A descending scan that bails
early (cap, throttle) leaves a permanent hole under the newest items, whereas
an ascending scan always holds a complete prefix and the watermark can advance
safely after any number of pages. Unfinished backfill simply catches up on the
next watch trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from openviking.resource.github_radar.gh_cli import GhCli, GhCliError
from openviking.resource.github_radar.models import (
    PrSignal,
    RadarConfig,
    RadarState,
    norm_ts,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_PER_PAGE = 100
# Issue rows share the endpoint budget but must not count against
# ``max_prs_per_run``; bound pages instead so an issue-heavy repository cannot
# turn one trigger into an unbounded pagination loop.
_MAX_PAGES_PER_RUN = 10
_SEEN_LIMIT = 200


@dataclass
class CollectOutcome:
    """Result of one collection pass."""

    prs: List[PrSignal]
    state: RadarState
    caught_up: bool


def _build_pr(row: Dict[str, Any]) -> Optional[PrSignal]:
    number = row.get("number")
    if not isinstance(number, int):
        return None
    user = row.get("user") or {}
    return PrSignal(
        number=number,
        title=str(row.get("title") or f"#{number}"),
        url=str(row.get("html_url") or ""),
        state=str(row.get("state") or ""),
        draft=bool(row.get("draft")),
        author=str(user.get("login") or ""),
        created_at=norm_ts(row.get("created_at")),
        updated_at=norm_ts(row.get("updated_at")),
        merged_at=norm_ts((row.get("pull_request") or {}).get("merged_at")),
        closed_at=norm_ts(row.get("closed_at")),
        labels=[
            str(item["name"])
            for item in (row.get("labels") or [])
            if isinstance(item, dict) and item.get("name")
        ],
        body=PrSignal.clip_body(row.get("body")),
    )


async def collect_prs(
    gh: GhCli,
    repo: str,
    config: RadarConfig,
    state: RadarState,
) -> CollectOutcome:
    """Collect PRs updated after the stored watermark.

    On transport failure the previous state is returned untouched so the next
    watch trigger retries the same window. On a bounded stop the watermark only
    advances over the complete ascending prefix already read.
    """
    since = state.issues_since or config.first_run_floor()
    watermark = since
    previously_seen: Set[str] = set(state.issues_seen)
    boundary_refs: Set[str] = set()
    collected: List[PrSignal] = []
    page = 1
    # Counts newly collected PRs only: issue rows advance the watermark but
    # never consume the PR budget, so the cap matches its name.
    prs_collected = 0
    caught_up = False
    # per_page must stay constant across pages within one pass: GitHub slices
    # each request at offset=(page-1)*per_page, so shrinking per_page mid-scan
    # moves the offset backwards and re-emits the previous page's rows. The PR
    # cap is enforced between pages by the while condition instead.
    per_page = min(_PER_PAGE, config.max_prs_per_run)

    while prs_collected < config.max_prs_per_run and page <= _MAX_PAGES_PER_RUN:
        try:
            rows = await gh.api_get_json(
                f"repos/{repo}/issues",
                {
                    "state": "all",
                    "since": since,
                    "sort": "updated",
                    "direction": "asc",
                    "per_page": str(per_page),
                    "page": str(page),
                },
            )
        except GhCliError as exc:
            # Hold the watermark exactly: a transport failure must not advance
            # state or drop evidence, so the next trigger retries the same window.
            logger.warning("[PrRadar] collection interrupted for %s: %s", repo, exc)
            return CollectOutcome(prs=collected, state=state, caught_up=False)

        if not isinstance(rows, list):
            logger.warning("[PrRadar] issues endpoint returned %s, holding watermark", type(rows))
            return CollectOutcome(prs=collected, state=state, caught_up=False)

        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp = norm_ts(row.get("updated_at"))
            if stamp > watermark:
                watermark = stamp
            if "pull_request" not in row:
                continue  # Issue, not a PR -- it still advanced the watermark above.
            signal = _build_pr(row)
            if signal is None:
                continue
            if stamp == watermark:
                boundary_refs.add(signal.source_ref)
            # The inclusive `since` re-emits boundary rows; dedupe by PR number.
            if signal.source_ref in previously_seen:
                continue
            collected.append(signal)
            prs_collected += 1

        if len(rows) < per_page:
            caught_up = True
            break
        page += 1

    new_state = _advance_state(state, watermark, boundary_refs, previously_seen, since)
    if not caught_up:
        logger.info(
            "[PrRadar] %s: per-run bound reached (%s PRs or %s pages), "
            "backfill continues next trigger",
            repo,
            config.max_prs_per_run,
            _MAX_PAGES_PER_RUN,
        )
    return CollectOutcome(prs=collected, state=new_state, caught_up=caught_up)


def _advance_state(
    previous: RadarState,
    watermark: str,
    boundary_refs: Set[str],
    previously_seen: Set[str],
    since: str,
) -> RadarState:
    """Build the next watermark state.

    Rows landing exactly on the watermark are remembered so the inclusive
    ``since`` does not re-emit them next pass. When nothing landed on the final
    watermark (usually an empty page), keep the previous memory rather than
    clearing it and re-emitting an old boundary item.
    """
    next_seen = sorted(boundary_refs) if boundary_refs else sorted(previously_seen)
    return RadarState(
        issues_since=watermark or since or previous.issues_since,
        issues_seen=next_seen[-_SEEN_LIMIT:],
    )
