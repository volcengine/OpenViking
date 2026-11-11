# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.integrations.github_radar.collector import collect_prs
from openviking.integrations.github_radar.gh_cli import GhCliError
from openviking.integrations.github_radar.models import RadarConfig, RadarState
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
