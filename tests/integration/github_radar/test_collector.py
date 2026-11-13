# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.resource.github_radar.collector import collect_prs
from openviking.resource.github_radar.gh_cli import GhCliError
from openviking.resource.github_radar.models import RadarConfig, RadarState
from openviking_cli.exceptions import InvalidArgumentError


def _pr_row(number: int, updated_at: str, *, merged: bool = False, draft: bool = False) -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/o/r/pull/{number}",
        "state": "closed" if merged else "open",
        "draft": draft,
        "user": {"login": f"user{number}", "type": "User"},
        "created_at": updated_at,
        "updated_at": updated_at,
        "closed_at": updated_at if merged else None,
        "labels": [{"name": f"label-{number}"}],
        "body": f"body {number}",
        "pull_request": {"merged_at": updated_at if merged else None},
    }


def _issue_row(number: int, updated_at: str) -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "html_url": f"https://github.com/o/r/issues/{number}",
        "state": "open",
        "user": {"login": "reporter", "type": "User"},
        "created_at": updated_at,
        "updated_at": updated_at,
        "labels": [],
        "body": "",
    }


class FakeGh:
    """Scripted gh client: serves prebuilt pages, optionally failing once."""

    def __init__(self, pages: list[list[dict]], fail_paths: set[str] | None = None) -> None:
        self._pages = pages
        self._fail_paths = fail_paths or set()
        self.calls: list[dict] = []

    async def api_get_json(self, path, params=None):
        self.calls.append({"path": path, "params": dict(params or {})})
        if path in self._fail_paths:
            raise GhCliError("gh exited 1: 502 Bad Gateway")
        page = int((params or {}).get("page", "1")) - 1
        if page < 0 or page >= len(self._pages):
            return []
        return self._pages[page]


@pytest.mark.asyncio
async def test_collects_only_pull_requests_and_advances_watermark():
    pages = [
        [
            _issue_row(1, "2026-11-01T00:00:00Z"),
            _pr_row(2, "2026-11-01T00:01:00Z"),
            _pr_row(3, "2026-11-01T00:02:00Z", merged=True),
        ]
    ]
    gh = FakeGh(pages)
    config = RadarConfig(enabled=True, backfill_days=7, max_prs_per_run=50)
    start = RadarState(issues_since="2026-11-01T00:00:00Z")

    outcome = await collect_prs(gh, "o/r", config, start)

    assert [pr.number for pr in outcome.prs] == [2, 3]
    assert outcome.caught_up is True
    # Watermark follows the newest updated_at, including the issue row.
    assert outcome.state.issues_since == "2026-11-01T00:02:00Z"
    # The boundary PR is remembered to dedupe the inclusive `since` re-emit.
    assert "#3" in outcome.state.issues_seen


@pytest.mark.asyncio
async def test_boundary_pr_not_re_emitted_on_next_pass():
    config = RadarConfig(enabled=True, max_prs_per_run=50)
    first = await collect_prs(
        FakeGh([[_pr_row(7, "2026-11-01T00:05:00Z")]]),
        "o/r",
        config,
        RadarState(issues_since="2026-11-01T00:00:00Z"),
    )
    # GitHub re-emits #7 because `since` is inclusive.
    second = await collect_prs(
        FakeGh([[_pr_row(7, "2026-11-01T00:05:00Z")]]),
        "o/r",
        config,
        first.state,
    )
    assert second.prs == []
    assert second.state.issues_since == "2026-11-01T00:05:00Z"


@pytest.mark.asyncio
async def test_cap_stops_scan_and_marks_not_caught_up():
    pages = [
        [_pr_row(n, f"2026-11-01T00:{n:02d}:00Z") for n in range(1, 3)],
        [_pr_row(n, f"2026-11-01T01:{n:02d}:00Z") for n in range(3, 5)],
    ]
    config = RadarConfig(enabled=True, max_prs_per_run=2)
    start = RadarState(issues_since="2026-11-01T00:00:00Z")
    outcome = await collect_prs(FakeGh(pages), "o/r", config, start)

    assert outcome.caught_up is False
    assert [pr.number for pr in outcome.prs] == [1, 2]
    # Watermark only advanced over the complete first page (safe prefix).
    assert outcome.state.issues_since == "2026-11-01T00:02:00Z"


@pytest.mark.asyncio
async def test_issue_rows_do_not_consume_pr_budget():
    # A full page of plain issues used to exhaust max_prs_per_run with zero
    # PRs collected; pagination now continues until the PR budget is spent.
    issue_page = [
        _issue_row(100 + i, "2026-11-01T00:00:00Z") for i in range(2)
    ]
    pr_page = [
        _pr_row(1, "2026-11-01T00:01:00Z"),
        _pr_row(2, "2026-11-01T00:02:00Z"),
    ]
    gh = FakeGh([issue_page, pr_page])
    config = RadarConfig(enabled=True, max_prs_per_run=2)
    outcome = await collect_prs(
        gh, "o/r", config, RadarState(issues_since="2026-11-01T00:00:00Z")
    )

    assert [pr.number for pr in outcome.prs] == [1, 2]
    assert outcome.caught_up is False
    # The consumed prefix is replayed before its watermark is committed.
    assert [call["params"]["page"] for call in gh.calls] == ["1", "2", "1", "2"]


@pytest.mark.asyncio
async def test_page_guard_bounds_issue_heavy_scans(monkeypatch):
    from openviking.resource.github_radar import collector as collector_mod

    monkeypatch.setattr(collector_mod, "_MAX_PAGES_PER_RUN", 2)
    # Pages must be as full as the requested per_page (50) so the short-page
    # termination does not fire before the page guard does.
    pages = [
        [_issue_row(100 * page + i, "2026-11-01T00:00:00Z") for i in range(50)]
        for page in range(3)
    ]
    gh = FakeGh(pages)
    config = RadarConfig(enabled=True, max_prs_per_run=50)
    outcome = await collect_prs(
        gh, "o/r", config, RadarState(issues_since="2026-11-01T00:00:00Z")
    )

    assert outcome.prs == []
    assert outcome.caught_up is False
    assert len(gh.calls) == 2


class GithubOffsetGh:
    """Models real GitHub REST pagination: offset=(page-1)*per_page over a
    fixed result set. A per_page that shrinks between requests moves the
    offset backwards and re-serves the previous page's rows."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[dict] = []

    async def api_get_json(self, path, params=None):
        per_page = int(params["per_page"])
        page = int(params["page"])
        self.calls.append({"page": page, "per_page": per_page})
        start = (page - 1) * per_page
        return self._rows[start : start + per_page]


class MutableUpdatedGh(GithubOffsetGh):
    """Live endpoint: changes updated_at ordering between requests."""

    async def api_get_json(self, path, params=None):
        rows = sorted(
            (row for row in self._rows if row["updated_at"] >= params["since"]),
            key=lambda row: row["updated_at"],
        )
        per_page = int(params["per_page"])
        start = (int(params["page"]) - 1) * per_page
        return rows[start : start + per_page]


@pytest.mark.asyncio
@pytest.mark.parametrize("last_stamp", ["2026-11-10T00:00:01Z", "2026-11-10T00:00:02Z"])
async def test_bounded_recovery_retains_versions_above_advanced_floor(last_stamp):
    base = "2026-11-10T00:00:00Z"
    rows = [_issue_row(100, base), _issue_row(101, base)]
    rows += [_pr_row(7, "2026-11-10T00:00:01Z"), _pr_row(8, last_stamp)]

    class ChangingGh(MutableUpdatedGh):
        changed = False

        async def api_get_json(self, path, params=None):
            if int(params["page"]) == 3 and not self.changed:
                rows[0]["updated_at"] = "2026-11-10T00:00:03Z"
                self.changed = True
            return await super().api_get_json(path, params)

    gh = ChangingGh(rows)
    config = RadarConfig(enabled=True, max_prs_per_run=1)
    state = RadarState(issues_since=base)
    emitted = []
    for _ in range(3):
        outcome = await collect_prs(gh, "o/r", config, state)
        emitted.append([pr.number for pr in outcome.prs])
        state = outcome.state
    assert emitted == [[8], [7], []]
    assert outcome.caught_up
    assert state.scan_seen == []
    # A genuine later version of the same PR must still be collected.
    rows[-1]["updated_at"] = "2026-11-10T00:00:04Z"
    newer = await collect_prs(gh, "o/r", config, state)
    assert [(pr.number, pr.updated_at) for pr in newer.prs] == [
        (8, "2026-11-10T00:00:04Z")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["update", "delete"])
async def test_resumption_reconciles_shifted_issue_prefix(change):
    tie = "2026-11-10T00:00:00Z"
    rows = [_issue_row(100 + i, tie) for i in range(20)]
    rows += [_pr_row(7, "2026-11-10T00:00:01Z"), _pr_row(8, "2026-11-10T00:00:02Z")]
    gh = MutableUpdatedGh(rows)
    config = RadarConfig(enabled=True, max_prs_per_run=2)
    first = await collect_prs(gh, "o/r", config, RadarState(issues_since=tie))
    assert first.prs == [] and not first.caught_up

    if change == "update":
        rows[0]["updated_at"] = "2026-11-10T00:00:03Z"
    else:
        rows.pop(0)
    emitted = []
    state = first.state
    for _ in range(4):
        outcome = await collect_prs(gh, "o/r", config, state)
        emitted.extend(pr.number for pr in outcome.prs)
        state = outcome.state
        if outcome.caught_up:
            break
    assert set(emitted) == {7, 8}
    assert outcome.caught_up


@pytest.mark.asyncio
async def test_in_pass_page_shift_does_not_advance_past_unread_pr():
    tie = "2026-11-10T00:00:00Z"
    rows = [_issue_row(100, tie), _issue_row(101, tie)]
    rows += [_pr_row(7, "2026-11-10T00:00:01Z"), _pr_row(8, "2026-11-10T00:00:02Z")]

    class ChangingGh(MutableUpdatedGh):
        changed = False

        async def api_get_json(self, path, params=None):
            if int(params["page"]) == 2 and not self.changed:
                rows[0]["updated_at"] = "2026-11-10T00:00:03Z"
                self.changed = True
            return await super().api_get_json(path, params)

    gh = ChangingGh(rows)
    config = RadarConfig(enabled=True, max_prs_per_run=2)
    first = await collect_prs(gh, "o/r", config, RadarState(issues_since=tie))
    second = await collect_prs(gh, "o/r", config, first.state)
    assert {pr.number for pr in first.prs + second.prs} == {7, 8}


@pytest.mark.asyncio
async def test_constant_per_page_keeps_github_offsets_from_overlapping():
    # An issue-heavy first page (40 issues + 10 PRs), then PR-only pages.
    rows = [
        _issue_row(1000 + i, f"2026-11-01T00:{i:02d}:00Z") for i in range(40)
    ]
    rows += [
        _pr_row(i, f"2026-11-01T{(i // 60) + 1:02d}:{i % 60:02d}:00Z")
        for i in range(1, 111)
    ]
    gh = GithubOffsetGh(rows)
    config = RadarConfig(enabled=True, max_prs_per_run=50)

    outcome = await collect_prs(
        gh, "o/r", config, RadarState(issues_since="2026-11-01T00:00:00Z")
    )

    numbers = [pr.number for pr in outcome.prs]
    # No PR is double-counted: page 1 yields 10 PRs and the PR-only page 2
    # is consumed whole (the cap gates between pages, not inside a page).
    assert len(numbers) == len(set(numbers)) == 60
    assert numbers == list(range(1, 61))
    # per_page stays fixed; both collection and verification scan sequentially.
    assert {call["per_page"] for call in gh.calls} == {50}
    assert [call["page"] for call in gh.calls] == [1, 2, 1, 2]
    assert outcome.caught_up is False


@pytest.mark.asyncio
async def test_first_floor_parameter_pins_since_for_empty_state():
    captured: list[dict] = []

    class RecordingGh:
        async def api_get_json(self, path, params=None):
            captured.append(dict(params))
            return []

    floor = "2026-10-01T00:00:00Z"
    outcome = await collect_prs(
        RecordingGh(),
        "o/r",
        RadarConfig(enabled=True, backfill_days=7),
        RadarState(),
        first_floor=floor,
    )

    # The labeled backfill window start reaches GitHub verbatim instead of a
    # floor recomputed at trigger time.
    assert captured[0]["since"] == floor
    assert outcome.state.issues_since == floor


@pytest.mark.asyncio
async def test_final_watermark_ref_survives_large_scan_and_blocks_reemit():
    # 300 unique-timestamp PRs in one pass (per_page=100, 3 pages). The old
    # lexicographic top-200 truncation dropped the final-watermark PR #300 and
    # the next pass re-collected it through the inclusive `since`.
    rows = [
        _pr_row(n, f"2026-11-01T00:{n // 60:02d}:{n % 60:02d}Z")
        for n in range(1, 301)
    ]
    config = RadarConfig(enabled=True, max_prs_per_run=500)
    outcome = await collect_prs(
        GithubOffsetGh(rows), "o/r", config,
        RadarState(issues_since="2026-11-01T00:00:00Z"),
    )

    assert len(outcome.prs) == 300
    assert "#300" in outcome.state.issues_seen
    # Distinct timestamps: only the single final-watermark ref is remembered.
    assert outcome.state.issues_seen == ["#300"]

    class BoundaryReemitGh:
        async def api_get_json(self, path, params=None):
            # GitHub re-emits the boundary row because `since` is inclusive.
            return [rows[-1]] if int(params["page"]) == 1 else []

    second = await collect_prs(BoundaryReemitGh(), "o/r", config, outcome.state)
    assert second.prs == []


@pytest.mark.asyncio
async def test_newer_snapshot_of_remembered_pr_is_emitted():
    # #7 collected at T0 and remembered on the boundary; when it merges at T1
    # the inclusive `since` re-emits it with a newer timestamp, which must be
    # collected as an update instead of being skipped by number-only dedup.
    config = RadarConfig(enabled=True, max_prs_per_run=50)
    first = await collect_prs(
        GithubOffsetGh([_pr_row(7, "2026-11-10T01:00:00Z")]),
        "o/r",
        config,
        RadarState(issues_since="2026-11-10T00:00:00Z"),
    )
    merged = _pr_row(7, "2026-11-10T02:00:00Z", merged=True)

    second = await collect_prs(GithubOffsetGh([merged]), "o/r", config, first.state)

    assert [pr.number for pr in second.prs] == [7]
    assert second.prs[0].merged_at == "2026-11-10T02:00:00Z"
    # The watermark advances while the update itself is not lost.
    assert second.state.issues_since == "2026-11-10T02:00:00Z"

    # A same-timestamp boundary re-emit is still suppressed.
    third = await collect_prs(GithubOffsetGh([merged]), "o/r", config, second.state)
    assert third.prs == []


@pytest.mark.asyncio
async def test_same_timestamp_issue_prefix_resumes_across_triggers():
    # 500 plain issues sharing timestamp T fill every page of the first pass;
    # the PR at T+1 must be reached on the next pass by budgeting replay of
    # the persisted prefix in addition to ten fresh pages.
    tie = "2026-11-10T00:00:00Z"
    later = "2026-11-10T00:00:01Z"
    rows = [_issue_row(1000 + i, tie) for i in range(500)]
    rows.append(_pr_row(7, later))
    config = RadarConfig(enabled=True, max_prs_per_run=50)
    gh = GithubOffsetGh(rows)

    first = await collect_prs(
        gh, "o/r", config, RadarState(issues_since=tie)
    )
    assert first.prs == []
    assert first.caught_up is False
    # Replay budget persisted while the watermark stays on T.
    assert first.state.issues_since == tie
    assert first.state.scan_offset == 500

    second = await collect_prs(gh, "o/r", config, first.state)
    assert [pr.number for pr in second.prs] == [7]
    assert second.caught_up is True
    # Watermark movement consumes the tie prefix and resets the offset.
    assert second.state.issues_since == later
    assert second.state.scan_offset == 0


@pytest.mark.asyncio
async def test_scan_offset_resumes_after_page_guard():
    # A tie prefix longer than the 10-page budget (10 x per_page) requires
    # several passes; each replays the known prefix before reading further
    # until the PR past the prefix is finally reached.
    tie = "2026-11-10T00:00:00Z"
    later = "2026-11-10T00:00:01Z"
    rows = [_issue_row(1000 + i, tie) for i in range(23)]
    rows.append(_pr_row(7, later))
    config = RadarConfig(enabled=True, max_prs_per_run=2)
    gh = GithubOffsetGh(rows)

    first = await collect_prs(gh, "o/r", config, RadarState(issues_since=tie))
    assert first.prs == []
    assert first.state.scan_offset == 20  # 10 full pages consumed

    second = await collect_prs(gh, "o/r", config, first.state)
    assert [pr.number for pr in second.prs] == [7]
    assert second.state.scan_offset == 0


@pytest.mark.asyncio
async def test_tied_boundary_refs_are_kept_losslessly_beyond_200():
    # 300 PRs sharing ONE timestamp: all 300 boundary refs must be remembered
    # so the inclusive `since` re-emit on the next pass collects nothing,
    # instead of truncating to 200 and re-collecting the other 100 forever.
    tie = "2026-11-10T00:00:00Z"
    rows = [_pr_row(n, tie) for n in range(1, 301)]
    config = RadarConfig(enabled=True, max_prs_per_run=500)

    first = await collect_prs(
        GithubOffsetGh(rows), "o/r", config, RadarState(issues_since=tie)
    )
    assert len(first.prs) == 300
    assert len(first.state.issues_seen) == 300

    second = await collect_prs(GithubOffsetGh(rows), "o/r", config, first.state)
    assert second.prs == []


def test_config_rejects_values_above_upper_bounds():
    oversized = {
        "backfill_days": 3651,
        "max_prs_per_run": 1001,
        "max_pending_prs": 10_001,
        "summary_batch_bytes": 10_000_001,
        "digest_interval_minutes": 525_601,
    }
    for name, value in oversized.items():
        with pytest.raises(InvalidArgumentError):
            RadarConfig.from_kwargs(
                {"pr_radar": {"enabled": True, name: value}}
            )
    # Values exactly at the bound remain valid.
    cfg = RadarConfig.from_kwargs(
        {"pr_radar": {"enabled": True, "max_prs_per_run": 1000}}
    )
    assert cfg.max_prs_per_run == 1000


@pytest.mark.asyncio
async def test_transport_failure_holds_earned_prefix_without_raising():
    gh = FakeGh(
        [[_pr_row(1, "2026-11-01T00:01:00Z")]],
        fail_paths={"repos/o/r/issues"},
    )
    config = RadarConfig(enabled=True)
    outcome = await collect_prs(gh, "o/r", config, RadarState())

    assert outcome.prs == []
    assert outcome.caught_up is False
    # No crash; state stays usable for the next trigger.
    assert isinstance(outcome.state, RadarState)


def test_config_rejects_non_positive_integers():
    with pytest.raises(InvalidArgumentError):
        RadarConfig.from_kwargs(
            {"pr_radar": {"enabled": True, "max_prs_per_run": 0}}
        )
    with pytest.raises(InvalidArgumentError):
        RadarConfig.from_kwargs(
            {"pr_radar": {"enabled": True, "backfill_days": "7"}}
        )


def test_config_disabled_by_default():
    assert RadarConfig.from_kwargs({}).enabled is False
    assert RadarConfig.from_kwargs(None).enabled is False
