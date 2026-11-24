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

from dataclasses import dataclass, replace
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
# ``max_prs_per_run``. Budget ten fresh pages in addition to the prefix that
# must be replayed; a live positional offset cannot safely skip that prefix.
_MAX_PAGES_PER_RUN = 10


@dataclass
class CollectOutcome:
    """Result of one collection pass."""

    prs: List[PrSignal]
    state: RadarState
    caught_up: bool


def _build_pr(row: Dict[str, Any]) -> Optional[PrSignal]:
    number = row.get("number")
    if type(number) is not int:
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
    first_floor: Optional[str] = None,
) -> CollectOutcome:
    """Collect PRs updated after the stored watermark.

    ``first_floor`` pins the first-run query floor (the backfill window start
    derived from the watch anchor); without it the floor is computed at call
    time, which drifts forward when early triggers are skipped and would query
    a narrower range than the report window claims to cover.

    On transport failure the query floor is held, while observed identities
    survive so a partial publication cannot replay its drained rows. On a
    bounded stop the watermark advances only after replay verifies the prefix.
    Every trigger
    starts at page 1: updating/deleting a consumed issue moves unread rows to
    earlier positions. Previously read rows increase the replay budget rather
    than supplying an unsafe offset. Identity-based deduplication still allows
    progress through an arbitrarily long unchanged timestamp tie.
    """
    since = state.issues_since or first_floor or config.first_run_floor()
    watermark = since
    previously_seen: Set[str] = set(state.issues_seen)
    boundary_refs: Set[str] = set()
    scanned_refs: Set[str] = set(state.scan_seen)
    fingerprints = dict(state.pr_fingerprints)
    prefix_snapshot: List[str] = []
    collected: List[PrSignal] = []
    # Counts newly collected PRs only: issue rows advance the watermark but
    # never consume the PR budget, so the cap matches its name.
    prs_collected = 0
    caught_up = False
    rows_consumed = 0
    # per_page must stay constant across pages within one pass: GitHub slices
    # each request at offset=(page-1)*per_page, so shrinking per_page mid-scan
    # moves the offset backwards and re-emits the previous page's rows. The PR
    # cap is enforced between pages by the while condition instead.
    per_page = min(_PER_PAGE, config.max_prs_per_run)
    replay_rows = max(state.scan_offset, len(previously_seen), len(state.scan_seen))
    page_budget = _MAX_PAGES_PER_RUN + (replay_rows + per_page - 1) // per_page
    page = 1
    pages_fetched = 0

    def held_state() -> RadarState:
        if not rows_consumed:
            return state
        return replace(
            state,
            issues_since=since,
            scan_offset=max(state.scan_offset, rows_consumed),
            scan_seen=sorted(scanned_refs),
            pr_fingerprints=dict(fingerprints),
        )

    async def fetch(page_number: int) -> list:
        rows = await gh.api_get_json(
            f"repos/{repo}/issues",
            {
                "state": "all",
                "since": since,
                "sort": "updated",
                "direction": "asc",
                "per_page": str(per_page),
                "page": str(page_number),
            },
        )
        if not isinstance(rows, list):
            raise GhCliError(f"issues endpoint returned {type(rows).__name__}")
        return rows

    while prs_collected < config.max_prs_per_run and pages_fetched < page_budget:
        try:
            rows = await fetch(page)
        except GhCliError as exc:
            # Hold the floor but retain emitted versions: the runner may
            # publish a partial digest and drain them before retrying.
            logger.warning("[PrRadar] collection interrupted for %s: %s", repo, exc)
            return CollectOutcome(prs=collected, state=held_state(), caught_up=False)

        pages_fetched += 1
        # Endpoint position includes issue and malformed rows, not just PRs.
        rows_consumed += len(rows)

        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp = norm_ts(row.get("updated_at"))
            if stamp > watermark:
                watermark = stamp
                # Keep only refs sharing the FINAL watermark: they are the
                # rows the inclusive `since` re-emits next pass. Refs from
                # earlier running maxima must not be remembered, or the
                # boundary set would grow with every PR ever read.
                boundary_refs.clear()
            number = row.get("number")
            ref = f"#{number}"
            row_key = f"{ref}@{stamp}"
            already_read = row_key in scanned_refs
            signal = _build_pr(row) if "pull_request" in row else None
            if type(number) is int:
                prefix_snapshot.append(
                    f"{row_key}:{signal.fingerprint}" if signal else row_key
                )
                scanned_refs.add(row_key)
                if stamp and stamp == watermark:
                    # Ordinary issues also identify the replayed tie prefix.
                    boundary_refs.add(ref)
            if "pull_request" not in row:
                continue  # Issue, not a PR -- it still advanced the watermark above.
            if signal is None:
                continue
            if stamp and stamp == watermark:
                boundary_refs.add(signal.source_ref)
            fingerprint = signal.fingerprint
            previous_fingerprint = fingerprints.get(row_key)
            fingerprints[row_key] = fingerprint
            if previous_fingerprint == fingerprint:
                continue
            # Legacy states have only number/time identities. Re-observe their
            # pending window once, but baseline already published older rows.
            legacy_seen = already_read or (
                signal.source_ref in previously_seen and stamp <= since
            )
            if (
                previous_fingerprint is None and legacy_seen
                and stamp and state.last_digest_at and stamp < state.last_digest_at
            ):
                continue
            collected.append(signal)
            prs_collected += 1

        if len(rows) < per_page:
            caught_up = True
            break
        page += 1

    # Pagination can shift even within one trigger. Re-read the consumed
    # prefix before committing its maximum: an unseen row below that maximum
    # means the apparent prefix had a hole. Keep the old floor and observed
    # identities so the next replay collects the missing row, without emitting
    # the already collected versions again.
    verified = True
    if pages_fetched > 1 and (watermark > since or caught_up):
        try:
            replay_snapshot: List[str] = []
            for check_page in range(1, pages_fetched + 1):
                for row in await fetch(check_page):
                    if not isinstance(row, dict) or type(row.get("number")) is not int:
                        continue
                    stamp = norm_ts(row.get("updated_at"))
                    key = f"#{row['number']}@{stamp}"
                    snapshot = _build_pr(row) if "pull_request" in row else None
                    replay_snapshot.append(
                        f"{key}:{snapshot.fingerprint}" if snapshot else key
                    )
            # Matching identities alone is insufficient: two shifting scans
            # could skip the same unread row. Require the ordered observed
            # prefix to agree, including updates of already known issues.
            if replay_snapshot != prefix_snapshot:
                verified = False
                caught_up = False
        except GhCliError as exc:
            logger.warning("[PrRadar] prefix verification interrupted for %s: %s", repo, exc)
            return CollectOutcome(prs=collected, state=held_state(), caught_up=False)

    new_state = _advance_state(
        state,
        watermark,
        boundary_refs,
        previously_seen,
        since,
        rows_consumed,
        caught_up,
        scanned_refs,
        verified,
        fingerprints,
    )
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
    rows_consumed: int,
    caught_up: bool,
    scanned_refs: Set[str],
    verified: bool,
    fingerprints: Dict[str, str],
) -> RadarState:
    """Build the next watermark state.

    ``boundary_refs`` holds every PR ref sharing the final watermark timestamp,
    so the inclusive ``since`` does not re-emit them next pass. The set must be
    stored losslessly: truncating it would let tied boundary rows survive as
    "new" PRs forever. When nothing landed on the final watermark (usually an
    empty page), keep the previous memory rather than clearing it and
    re-emitting an old boundary.

    Retain observed identities whenever the floor must stay put. The prefix
    size budgets the next replay; it is never used as an endpoint cursor.
    """
    if not verified:
        return replace(
            previous,
            issues_since=since,
            scan_offset=max(previous.scan_offset, rows_consumed),
            scan_seen=sorted(scanned_refs),
            pr_fingerprints=dict(fingerprints),
        )
    watermark_moved = watermark > since
    if watermark_moved:
        next_seen = sorted(boundary_refs) if boundary_refs else sorted(previously_seen)
        next_offset = 0
    else:
        next_seen = sorted(previously_seen | boundary_refs)
        next_offset = 0 if caught_up else max(previous.scan_offset, rows_consumed)
    # A held scan may already have emitted versions above this verified
    # prefix. Keep them until the floor passes them or the scan catches up.
    next_scan_seen = [] if caught_up else sorted(scanned_refs)
    if watermark_moved and not caught_up:
        next_scan_seen = [
            key for key in next_scan_seen if key.rsplit("@", 1)[-1] >= watermark
        ]
    return replace(
        previous,
        issues_since=watermark or since or previous.issues_since,
        issues_seen=next_seen,
        scan_offset=next_offset,
        scan_seen=next_scan_seen,
        pr_fingerprints={
            key: value for key, value in fingerprints.items()
            if key.rsplit("@", 1)[-1] >= (watermark or since)
        },
    )
