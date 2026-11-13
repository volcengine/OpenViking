# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace

import pytest

import openviking.resource.pr_radar_runner as runner_module
from openviking.resource.github_radar.collector import _build_pr
from openviking.resource.github_radar.gh_cli import GhCliError
from openviking.resource.github_radar.models import (
    LATEST_REPORT_NAME,
    SIGNALS_FILENAME,
    STATE_FILENAME,
    RadarConfig,
)
from openviking.resource.pr_radar_runner import (
    PrRadarRunner,
    partition_pending,
    validate_pr_radar_config,
)
from openviking_cli.exceptions import InvalidArgumentError

GITHUB_URL = "https://github.com/volcengine/OpenViking"


class FakeFs:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def write_file(self, uri, content, ctx=None, **kwargs):
        self.files[uri] = content

    async def read_file(self, uri, ctx=None, **kwargs):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]


class FakeGh:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def api_get_json(self, path, params=None):
        return list(self._rows)


def _pr_row(number: int, updated_at: str = "2026-11-10T08:00:00Z") -> dict:
    ts = updated_at
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/volcengine/OpenViking/pull/{number}",
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "type": "User"},
        "created_at": ts,
        "updated_at": ts,
        "labels": [],
        "body": "hello",
        "pull_request": {"merged_at": None},
    }


WATCH_TO_URI = "viking://resources/volcengine/OpenViking"
RADAR_TARGET_URI = "viking://user/acct-1/resources/radar/volcengine/OpenViking"


def _task(processor_kwargs: dict | None = None, path: str = GITHUB_URL):
    from datetime import datetime, timedelta

    return SimpleNamespace(
        task_id="watch-1",
        path=path,
        to_uri=WATCH_TO_URI,
        processor_kwargs=processor_kwargs or {},
        # Anchor two minutes in the past so a 1-minute window is already due.
        created_at=datetime.now() - timedelta(minutes=2),
        watch_interval=1.0,
    )


_ctx = SimpleNamespace(account_id="acct-1")


@pytest.fixture(autouse=True)
def _gh_present(monkeypatch):
    monkeypatch.setattr(runner_module, "have_gh", lambda: True)


@pytest.mark.asyncio
async def test_disabled_config_is_a_noop():
    fs = FakeFs()
    result = await PrRadarRunner(fs, gh=FakeGh([_pr_row(1)])).maybe_run(
        _task({"pr_radar": {"enabled": False}}), _ctx
    )
    assert result.status == "disabled"
    assert fs.files == {}


@pytest.mark.asyncio
async def test_non_github_repo_is_skipped():
    fs = FakeFs()
    result = await PrRadarRunner(fs, gh=FakeGh([])).maybe_run(
        _task({"pr_radar": {"enabled": True}}, path="https://gitlab.example.com/team/repo"),
        _ctx,
    )
    assert result.status == "skipped"
    assert fs.files == {}


@pytest.mark.asyncio
async def test_missing_gh_cli_defers_without_writes(monkeypatch):
    monkeypatch.setattr(runner_module, "have_gh", lambda: False)
    fs = FakeFs()
    result = await PrRadarRunner(fs).maybe_run(
        _task({"pr_radar": {"enabled": True}}), _ctx
    )
    assert result.status == "skipped"
    assert "gh" in result.reason
    assert fs.files == {}


@pytest.mark.asyncio
async def test_empty_window_writes_only_state():
    fs = FakeFs()
    result = await PrRadarRunner(fs, gh=FakeGh([])).maybe_run(
        _task({"pr_radar": {"enabled": True}}), _ctx
    )
    assert result.status == "empty"
    state_uri = next(uri for uri in fs.files if uri.endswith(STATE_FILENAME))
    assert state_uri.startswith(RADAR_TARGET_URI)
    # No report without PRs.
    assert not any(uri.endswith(LATEST_REPORT_NAME) for uri in fs.files)


@pytest.mark.asyncio
async def test_published_writes_latest_archive_and_state():
    fs = FakeFs()
    result = await PrRadarRunner(fs, gh=FakeGh([_pr_row(11), _pr_row(12)])).maybe_run(
        _task({"pr_radar": {"enabled": True}}), _ctx
    )
    assert result.status == "published"
    assert result.pr_count == 2

    uris = set(fs.files)
    assert any(
        uri.endswith(f"radar/volcengine/OpenViking/{LATEST_REPORT_NAME}") for uri in uris
    )
    assert any("/reports/" in uri and uri.endswith(".md") for uri in uris)
    assert any(uri.endswith(STATE_FILENAME) for uri in uris)
    latest = next(uri for uri in uris if uri.endswith(LATEST_REPORT_NAME))
    assert "#11" in fs.files[latest] and "#12" in fs.files[latest]


@pytest.mark.asyncio
async def test_second_run_dedupes_boundary_and_publishes_nothing_new():
    fs = FakeFs()
    runner = PrRadarRunner(fs, gh=FakeGh([_pr_row(11)]))
    first = await runner.maybe_run(_task({"pr_radar": {"enabled": True}}), _ctx)
    assert first.status == "published"

    # Same rows re-emitted by the inclusive `since`; no new report content.
    second = await runner.maybe_run(
        _task({"pr_radar": {"enabled": True}}), _ctx
    )
    assert second.status == "empty"


@pytest.mark.asyncio
async def test_gh_failure_holds_watermark_and_writes_nothing():
    class FailingGh:
        async def api_get_json(self, path, params=None):
            raise GhCliError("gh exited 1: 429")

    fs = FakeFs()
    result = await PrRadarRunner(fs, gh=FailingGh()).maybe_run(
        _task({"pr_radar": {"enabled": True}}), _ctx
    )
    # The collector holds the watermark on transport failure; nothing is published.
    assert result.status == "empty"
    assert fs.files == {}


def test_custom_target_uri_is_honored():
    config = RadarConfig.from_kwargs(
        {"pr_radar": {"enabled": True, "target_uri": "viking://user/x/resources/radar/custom"}}
    )
    uri = PrRadarRunner._target_uri(config, "volcengine/OpenViking", "acct-1")
    assert uri == "viking://user/x/resources/radar/custom"


def test_validate_rejects_radar_on_non_github():
    with pytest.raises(InvalidArgumentError):
        validate_pr_radar_config(
            "https://gitlab.example.com/team/repo",
            {"pr_radar": {"enabled": True}},
        )


def test_validate_allows_disabled_radar_anywhere():
    validate_pr_radar_config(
        "https://gitlab.example.com/team/repo",
        {"pr_radar": {"enabled": False}},
    )


class FakeVLM:
    def __init__(self, responses=None, raises: Exception | None = None) -> None:
        self._responses = list(responses or [])
        self._raises = raises
        self.calls = 0

    async def get_completion_async(self, prompt):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return json.dumps(self._responses.pop(0), ensure_ascii=False)


class FakeResolver:
    def __init__(self, handle) -> None:
        self.handle = handle

    async def get_vlm(self, account_id):
        return self.handle


def _summary_response() -> dict:
    return {
        "items": [
            {
                "number": 11,
                "topic": "gateway",
                "severity": "high",
                "summary_zh": "收紧工具访问",
                "impact": "影响网关链路",
                "recommended_action": "评审",
            },
            {
                "number": 12,
                "topic": "gateway",
                "severity": "medium",
                "summary_zh": "默认开放工具",
                "impact": "影响默认配置",
                "recommended_action": "",
            },
        ]
    }


def _synthesis_response() -> dict:
    return {
        "editorial_note": "gateway 集中变更",
        "sections": [
            {
                "topic": "gateway",
                "overview_zh": "两个 PR 共同调整工具访问控制",
                "highlight_numbers": [11],
            }
        ],
    }


@pytest.mark.asyncio
async def test_summary_enabled_publishes_enriched_report():
    fs = FakeFs()
    # Two-stage calls: triage then synthesis, one response per call.
    handle = FakeVLM(responses=[_summary_response(), _synthesis_response()])
    runner = PrRadarRunner(
        fs, gh=FakeGh([_pr_row(11), _pr_row(12)]), vlm_resolver=FakeResolver(handle)
    )
    result = await runner.maybe_run(
        _task({"pr_radar": {"enabled": True, "summary_enabled": True}}), _ctx
    )

    assert result.status == "published"
    assert result.report_mode == "enriched"
    assert handle.calls == 2
    latest = next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))
    report = fs.files[latest]
    assert "主题综述" in report
    assert "收紧工具访问" in report
    # Deterministic facts are still present.
    assert "#11" in report and "#12" in report


@pytest.mark.asyncio
async def test_summary_failure_degrades_to_template():
    fs = FakeFs()
    handle = FakeVLM(raises=RuntimeError("model down"))
    runner = PrRadarRunner(
        fs, gh=FakeGh([_pr_row(11)]), vlm_resolver=FakeResolver(handle)
    )
    result = await runner.maybe_run(
        _task({"pr_radar": {"enabled": True, "summary_enabled": True}}), _ctx
    )

    assert result.status == "published"
    assert result.report_mode == "template"
    latest = next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))
    assert "#11" in fs.files[latest]
    assert "主题综述" not in fs.files[latest]


class PagedGh:
    """Serves a fixed full page first (cap hit), then a short final page."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self._pages = pages
        self.calls = 0

    async def api_get_json(self, path, params=None):
        page = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        return list(page)


def _signals_uri(fs: FakeFs) -> str:
    return next(uri for uri in fs.files if uri.endswith(".radar-signals.jsonl"))


@pytest.mark.asyncio
async def test_capped_round_accumulates_without_publishing():
    fs = FakeFs()
    first_page = [_pr_row(n) for n in range(1, 51)]  # 50 rows == per_page -> cap
    second_page = [_pr_row(n) for n in range(51, 54)]
    gh = PagedGh([first_page, second_page])
    runner = PrRadarRunner(fs, gh=gh)
    task = _task({"pr_radar": {"enabled": True}})

    first = await runner.maybe_run(task, _ctx)
    assert first.status == "accumulating"
    assert first.pr_count == 50
    # No fragment report is published; the raw queue holds the full first page.
    assert not any(uri.endswith(LATEST_REPORT_NAME) for uri in fs.files)
    queue = fs.files[_signals_uri(fs)]
    assert len([line for line in queue.splitlines() if line.strip()]) == 50

    second = await runner.maybe_run(task, _ctx)
    assert second.status == "published"
    assert second.pr_count == 53
    latest = fs.files[next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))]
    # One digest covers PRs from BOTH collection rounds.
    assert "#1]" in latest and "#50]" in latest and "#53]" in latest
    # Queue is drained after the successful digest.
    assert fs.files[_signals_uri(fs)] == ""


@pytest.mark.asyncio
async def test_force_publish_when_pending_hits_safety_bound():
    fs = FakeFs()
    full_page = [_pr_row(n) for n in range(1, 51)]
    runner = PrRadarRunner(fs, gh=PagedGh([full_page]))
    result = await runner.maybe_run(
        _task({"pr_radar": {"enabled": True, "max_pending_prs": 2}}), _ctx
    )

    assert result.status == "published"
    assert result.pr_count == 50
    assert result.warnings  # forced before caught-up
    assert any("max_pending_prs" in w for w in result.warnings)
    # A partial digest never moves the window boundary.
    assert _read_state(fs)["last_digest_at"] == ""


@pytest.mark.asyncio
async def test_publish_failure_keeps_pending_queue_intact():
    class FailPublishFs(FakeFs):
        async def write_file(self, uri, content, ctx=None, **kwargs):
            if uri.endswith(LATEST_REPORT_NAME):
                raise RuntimeError("disk full")
            await super().write_file(uri, content, ctx=ctx, **kwargs)

    fs = FailPublishFs()
    runner = PrRadarRunner(fs, gh=FakeGh([_pr_row(11)]))
    result = await runner.maybe_run(_task({"pr_radar": {"enabled": True}}), _ctx)

    assert result.status == "failed"
    assert not any(uri.endswith(LATEST_REPORT_NAME) for uri in fs.files)
    # The digest watermark must not advance before a successful publish:
    # no state file exists, so the next trigger retries the same window.
    assert not any(uri.endswith(STATE_FILENAME) for uri in fs.files)
    # The raw material survives so the next trigger can retry the digest.
    queue = fs.files[_signals_uri(fs)]
    assert '"number": 11' in queue


@pytest.mark.asyncio
async def test_queue_clear_failure_keeps_state_watermark_unadvanced():
    class FailQueueClearFs(FakeFs):
        async def write_file(self, uri, content, ctx=None, **kwargs):
            # The initial queue save (non-empty) succeeds; only the drain
            # after publishing fails.
            if uri.endswith(SIGNALS_FILENAME) and not content:
                raise RuntimeError("queue clear failed")
            await super().write_file(uri, content, ctx=ctx, **kwargs)

    fs = FailQueueClearFs()
    runner = PrRadarRunner(fs, gh=FakeGh([_pr_row(11)]))
    result = await runner.maybe_run(
        _task({"pr_radar": {"enabled": True}}), _ctx
    )

    assert result.status == "failed"
    # The report was published, but the watermark must not advance before the
    # queue is drained: no state file and the signal survives for retry.
    assert any(uri.endswith(LATEST_REPORT_NAME) for uri in fs.files)
    assert not any(uri.endswith(STATE_FILENAME) for uri in fs.files)
    queue = fs.files[_signals_uri(fs)]
    assert '"number": 11' in queue


def test_signal_store_merge_dedupes_by_number():
    from openviking.resource.github_radar.collector import _build_pr
    from openviking.resource.github_radar.signal_store import RadarSignalStore

    signals = [_build_pr(_pr_row(11)), _build_pr(_pr_row(12))]
    merged = RadarSignalStore.merge([signals[0]], signals)  # #11 again + #12

    assert [pr.number for pr in merged] == [11, 12]


_RADAR_URI = RADAR_TARGET_URI


def _state_uri(fs: FakeFs) -> str:
    return next(uri for uri in fs.files if uri.endswith(STATE_FILENAME))


def _read_state(fs: FakeFs) -> dict:
    return json.loads(fs.files[_state_uri(fs)])


def _seed_state(fs: FakeFs, last_digest_at: str) -> None:
    fs.files[f"{_RADAR_URI}/{STATE_FILENAME}"] = json.dumps(
        {
            "issues_since": "2026-01-08T00:00:00Z",
            "issues_seen": [],
            "last_digest_at": last_digest_at,
        }
    )


def _task_at(created_at, interval: float, processor_kwargs: dict | None = None):
    return SimpleNamespace(
        task_id="watch-1",
        path=GITHUB_URL,
        to_uri=WATCH_TO_URI,
        processor_kwargs=processor_kwargs or {"pr_radar": {"enabled": True}},
        created_at=created_at,
        watch_interval=interval,
    )


@pytest.mark.asyncio
async def test_backfill_report_carries_window_and_sets_digest_watermark():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import fmt_ts

    fs = FakeFs()
    anchor = datetime.now() - timedelta(minutes=2)
    task = _task_at(anchor, 1.0)
    result = await PrRadarRunner(fs, gh=FakeGh([_pr_row(11)])).maybe_run(task, _ctx)

    assert result.status == "published"
    expected_end = fmt_ts(anchor.astimezone(timezone.utc))
    assert result.window_end == expected_end
    latest = fs.files[next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))]
    assert "Window:" in latest and expected_end in latest
    # Digest watermark now anchors all later windows.
    assert _read_state(fs)["last_digest_at"] == expected_end


@pytest.mark.asyncio
async def test_steady_window_advances_by_watch_interval():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import fmt_ts, parse_ts

    fs = FakeFs()
    last_digest = datetime.now(timezone.utc) - timedelta(minutes=2)
    last_digest_ts = fmt_ts(last_digest)
    _seed_state(fs, last_digest_ts)

    # The PR must carry an updated_at inside the due window, otherwise the
    # window filter defers it to the next digest instead of publishing.
    in_window_ts = fmt_ts(datetime.now(timezone.utc) - timedelta(seconds=90))

    # Digest cadence is owned by RadarConfig, independent of the watch interval.
    task = _task_at(
        datetime.now(),
        1.0,
        {"pr_radar": {"enabled": True, "digest_interval_minutes": 1}},
    )
    result = await PrRadarRunner(
        fs, gh=FakeGh([_pr_row(22, in_window_ts)])
    ).maybe_run(task, _ctx)

    assert result.status == "published"
    expected_next = fmt_ts(parse_ts(last_digest_ts) + timedelta(minutes=1))
    assert result.window_start == last_digest_ts
    assert result.window_end == expected_next
    assert _read_state(fs)["last_digest_at"] == expected_next


@pytest.mark.asyncio
async def test_trigger_inside_window_accumulates_without_publishing():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import fmt_ts

    fs = FakeFs()
    # Boundary 30s ago, 60-minute window -> next digest is far in the future.
    _seed_state(fs, fmt_ts(datetime.now(timezone.utc) - timedelta(seconds=30)))
    task = _task_at(datetime.now(), 60.0)

    result = await PrRadarRunner(fs, gh=FakeGh([_pr_row(33)])).maybe_run(task, _ctx)

    assert result.status == "accumulating"
    assert not any(uri.endswith(LATEST_REPORT_NAME) for uri in fs.files)
    # Digest watermark is untouched.
    state = _read_state(fs)
    assert state["last_digest_at"] and state["issues_since"] != state["last_digest_at"]


@pytest.mark.asyncio
async def test_empty_due_window_advances_boundary_without_report():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import fmt_ts, parse_ts

    fs = FakeFs()
    last_digest_ts = fmt_ts(datetime.now(timezone.utc) - timedelta(minutes=2))
    _seed_state(fs, last_digest_ts)
    task = _task_at(
        datetime.now(),
        1.0,
        {"pr_radar": {"enabled": True, "digest_interval_minutes": 1}},
    )

    result = await PrRadarRunner(fs, gh=FakeGh([])).maybe_run(task, _ctx)

    assert result.status == "empty"
    assert not any(uri.endswith(LATEST_REPORT_NAME) for uri in fs.files)
    expected_next = fmt_ts(parse_ts(last_digest_ts) + timedelta(minutes=1))
    assert _read_state(fs)["last_digest_at"] == expected_next


def test_plan_digest_partial_safety_valve_never_advances_boundary():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import RadarConfig, RadarState, fmt_ts
    from openviking.resource.pr_radar_runner import plan_digest

    now = datetime.now(timezone.utc)
    anchor = now - timedelta(days=1)
    config = RadarConfig.from_kwargs({"pr_radar": {"enabled": True, "max_pending_prs": 2}})

    backfill_partial = plan_digest(
        state=RadarState(),
        config=config,
        pending_count=50,
        caught_up=False,
        anchor=anchor,
        now=now,
    )
    assert backfill_partial.partial is True
    assert backfill_partial.advance_to == ""

    steady_state = RadarState(last_digest_at=fmt_ts(now - timedelta(hours=1)))
    steady_partial = plan_digest(
        state=steady_state,
        config=config,
        pending_count=50,
        caught_up=True,
        anchor=anchor,
        now=now,
    )
    assert steady_partial.partial is True
    assert steady_partial.advance_to == ""
    assert steady_partial.window_start == steady_state.last_digest_at


def test_plan_digest_uses_independent_digest_interval_not_watch_interval():
    """Hourly collection with a daily digest: window advances by digest interval."""
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import RadarConfig, RadarState, fmt_ts
    from openviking.resource.pr_radar_runner import plan_digest

    now = datetime.now(timezone.utc)
    # Watch runs hourly (60 min), but the digest cadence is daily (1440 min).
    config = RadarConfig.from_kwargs(
        {"pr_radar": {"enabled": True, "digest_interval_minutes": 1440}}
    )
    assert config.digest_interval_minutes == 1440

    last_digest = now - timedelta(hours=1)
    # One hour since last digest: still inside the daily window -> accumulate.
    inside = plan_digest(
        state=RadarState(last_digest_at=fmt_ts(last_digest)),
        config=config,
        pending_count=1,
        caught_up=True,
        anchor=now,
        now=now,
    )
    assert inside.action == "accumulate"

    # 24 hours later: boundary reached, window end is exactly +1440 minutes.
    due_now = last_digest + timedelta(minutes=1440)
    due = plan_digest(
        state=RadarState(last_digest_at=fmt_ts(last_digest)),
        config=config,
        pending_count=1,
        caught_up=True,
        anchor=now,
        now=due_now,
    )
    assert due.action == "digest"
    assert due.advance_to == fmt_ts(due_now)


def test_plan_digest_rejects_non_positive_digest_interval():
    from openviking_cli.exceptions import InvalidArgumentError

    with pytest.raises(InvalidArgumentError):
        RadarConfig.from_kwargs(
            {"pr_radar": {"enabled": True, "digest_interval_minutes": 0}}
        )


def test_target_uri_must_be_viking_scheme():
    from openviking_cli.exceptions import InvalidArgumentError

    # A non-viking scheme must fail at config parse (watch creation), not at
    # every later radar trigger.
    with pytest.raises(InvalidArgumentError):
        RadarConfig.from_kwargs(
            {"pr_radar": {"enabled": True, "target_uri": "https://evil.example/x"}}
        )


def test_target_uri_accepts_viking_scheme():
    # Validation only checks the scheme; trailing-slash trimming stays the
    # runner's job (_target_uri calls rstrip before use).
    config = RadarConfig.from_kwargs(
        {"pr_radar": {"enabled": True, "target_uri": "viking://user/a/resources/radar/x/"}}
    )
    assert config.target_uri == "viking://user/a/resources/radar/x/"


def test_empty_target_uri_falls_back_to_derived_path():
    # Empty/absent target_uri stays None so the runner derives it from the watch.
    config = RadarConfig.from_kwargs({"pr_radar": {"enabled": True, "target_uri": "  "}})
    assert config.target_uri is None


@pytest.mark.asyncio
async def test_summary_disabled_makes_no_llm_call():
    fs = FakeFs()
    handle = FakeVLM(responses=[_summary_response()])
    runner = PrRadarRunner(
        fs, gh=FakeGh([_pr_row(11)]), vlm_resolver=FakeResolver(handle)
    )
    result = await runner.maybe_run(_task({"pr_radar": {"enabled": True}}), _ctx)

    assert result.status == "published"
    assert result.report_mode == "template"
    assert handle.calls == 0


def test_partition_pending_splits_on_window_bounds():
    from dataclasses import replace

    start = "2026-11-10T00:00:00Z"
    end = "2026-11-10T01:00:00Z"
    inside_low = _build_pr(_pr_row(1, start))
    inside_high = _build_pr(_pr_row(2, end))
    deferred = _build_pr(_pr_row(3, "2026-11-10T01:00:01Z"))
    timestampless = replace(_build_pr(_pr_row(4, start)), updated_at="")

    in_window, outside = partition_pending(
        [inside_low, inside_high, deferred, timestampless], start, end
    )

    # Bounds are inclusive; a row without a parseable timestamp cannot be
    # placed and must not starve in the queue.
    assert [pr.number for pr in in_window] == [1, 2, 4]
    assert [pr.number for pr in outside] == [3]


@pytest.mark.asyncio
async def test_digest_defers_signals_newer_than_window_end():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import fmt_ts

    fs = FakeFs()
    last_digest_ts = fmt_ts(datetime.now(timezone.utc) - timedelta(minutes=2))
    _seed_state(fs, last_digest_ts)
    in_window_ts = fmt_ts(datetime.now(timezone.utc) - timedelta(seconds=90))
    future_ts = fmt_ts(datetime.now(timezone.utc) + timedelta(hours=1))
    task = _task_at(
        datetime.now(),
        1.0,
        {"pr_radar": {"enabled": True, "digest_interval_minutes": 1}},
    )

    result = await PrRadarRunner(
        fs, gh=FakeGh([_pr_row(31, in_window_ts), _pr_row(32, future_ts)])
    ).maybe_run(task, _ctx)

    assert result.status == "published"
    assert result.pr_count == 1
    latest = fs.files[next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))]
    assert "#31" in latest and "#32" not in latest
    # The future PR stays queued for its own window instead of being cleared.
    queue = fs.files[_signals_uri(fs)]
    assert '"number": 32' in queue and '"number": 31' not in queue


@pytest.mark.asyncio
async def test_lagged_watch_catches_up_one_window_per_digest():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import fmt_ts

    fs = FakeFs()
    now = datetime.now(timezone.utc)
    _seed_state(fs, fmt_ts(now - timedelta(minutes=3)))
    window1_ts = fmt_ts(now - timedelta(seconds=150))
    window2_ts = fmt_ts(now - timedelta(seconds=90))
    # Pre-seed the pending queue; gh returns nothing new during both triggers.
    signals = [_build_pr(_pr_row(41, window1_ts)), _build_pr(_pr_row(42, window2_ts))]
    fs.files[f"{_RADAR_URI}/{SIGNALS_FILENAME}"] = "\n".join(
        json.dumps(signal.to_row()) for signal in signals
    )
    task = _task_at(
        datetime.now(),
        1.0,
        {"pr_radar": {"enabled": True, "digest_interval_minutes": 1}},
    )
    runner = PrRadarRunner(fs, gh=FakeGh([]))

    first = await runner.maybe_run(task, _ctx)
    assert first.status == "published"
    assert first.pr_count == 1
    # Snapshot the first report: latest.md is overwritten by the second
    # digest, while the unique archive stamps keep both archived reports.
    first_report = fs.files[
        next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))
    ]
    assert "#41" in first_report and "#42" not in first_report
    queue_after_first = fs.files[_signals_uri(fs)]
    assert '"number": 42' in queue_after_first
    assert '"number": 41' not in queue_after_first

    second = await runner.maybe_run(task, _ctx)
    assert second.status == "published"
    assert second.pr_count == 1
    second_report = fs.files[
        next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))
    ]
    assert "#42" in second_report and "#41" not in second_report
    assert fs.files[_signals_uri(fs)] == ""
    # The windows were digested separately, not merged into one mega-report.
    assert first.window_end != second.window_end
    # Unique archive stamps retain both reports instead of overwriting.
    archives = [uri for uri in fs.files if "/reports/" in uri and uri.endswith(".md")]
    assert len(archives) == 2
