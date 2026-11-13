# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from datetime import datetime
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
    authorize_pr_radar_target,
    partition_pending,
    validate_pr_radar_config,
)
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError

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
RADAR_TARGET_URI = "viking://user/alice/resources/radar/volcengine/OpenViking"


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
        user_id="alice",
    )


# Watch runtime identity: account_id (tenant) differs from user_id (namespace owner).
_ctx = SimpleNamespace(
    account_id="acct-1", user=SimpleNamespace(user_id="alice")
)


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
    uri = PrRadarRunner._target_uri(config, "volcengine/OpenViking", "alice")
    assert uri == "viking://user/x/resources/radar/custom"


def test_derived_target_uri_uses_user_id_not_account_id():
    # The owner segment of viking://user/<owner>/... must be user_id, matching
    # namespace.is_accessible; account_id would self-deny non-root identities.
    config = RadarConfig.from_kwargs({"pr_radar": {"enabled": True}})
    uri = PrRadarRunner._target_uri(config, "volcengine/OpenViking", "alice", WATCH_TO_URI)
    assert uri == RADAR_TARGET_URI

    from openviking.core.namespace import is_accessible
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(
        user=UserIdentifier("acct-1", "alice"), role=Role.USER, bypass_acl=True
    )
    assert is_accessible(f"{uri}/{STATE_FILENAME}", ctx) is True


def test_owner_user_id_falls_back_to_task_then_default():
    task = SimpleNamespace(user_id="bob")
    ctx_with_user = SimpleNamespace(user=SimpleNamespace(user_id="alice"))
    assert PrRadarRunner._owner_user_id(task, ctx_with_user) == "alice"
    assert PrRadarRunner._owner_user_id(task, SimpleNamespace()) == "bob"
    assert PrRadarRunner._owner_user_id(SimpleNamespace(), SimpleNamespace()) == "default"


def test_io_context_strips_acl_bypass_from_watch_context():
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    watch_ctx = RequestContext(
        user=UserIdentifier("acme", "alice"), role=Role.USER, bypass_acl=True
    )
    io_ctx = PrRadarRunner._io_context(watch_ctx)
    assert io_ctx.bypass_acl is False
    # Identity is preserved for namespace owner checks.
    assert io_ctx.user.user_id == "alice"

    plain = RequestContext(
        user=UserIdentifier("acme", "alice"), role=Role.USER, bypass_acl=False
    )
    assert PrRadarRunner._io_context(plain) is plain
    # Test doubles (non-dataclass) pass through unchanged.
    assert PrRadarRunner._io_context(_ctx) is _ctx


def test_radar_relative_path_covers_user_namespace_resources():
    project = PrRadarRunner._radar_relative_path(
        "viking://user/alice/resources/projectA"
    )
    other = PrRadarRunner._radar_relative_path(
        "viking://user/alice/resources/projectB"
    )
    assert project == "__user__/alice/projectA"
    assert other == "__user__/alice/projectB"
    assert project != other

    config = RadarConfig(enabled=True)
    target_a = PrRadarRunner._target_uri(config, "volcengine/OpenViking", "alice",
                                         "viking://user/alice/resources/projectA")
    target_b = PrRadarRunner._target_uri(config, "volcengine/OpenViking", "alice",
                                         "viking://user/alice/resources/projectB")
    assert target_a != target_b

    # Account resources keep their original projection and cannot collide
    # with the reserved user-namespace marker.
    assert PrRadarRunner._radar_relative_path(
        "viking://resources/volcengine/OpenViking"
    ) == "volcengine/OpenViking"
    assert PrRadarRunner._radar_relative_path(
        "viking://resources/__user__/forged"
    ) is None
    assert PrRadarRunner._radar_relative_path("viking://temp/x") is None


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


class _AuthProbeFs(FakeFs):
    def __init__(self, deny: set[str] | None = None) -> None:
        super().__init__()
        self.access_checks: list[tuple[str, str]] = []
        self._deny = deny or set()

    async def _ensure_access(self, uri, ctx, action=None):
        self.access_checks.append((uri, str(action)))
        if uri in self._deny:
            raise PermissionDeniedError(f"denied {uri}", resource=uri)


@pytest.mark.asyncio
async def test_explicit_radar_target_requires_caller_write_permission():
    kwargs = {
        "pr_radar": {
            "enabled": True,
            "target_uri": "viking://resources/restricted/",
        }
    }
    fs = _AuthProbeFs(deny={"viking://resources/restricted"})

    with pytest.raises(PermissionDeniedError):
        await authorize_pr_radar_target(fs, kwargs, _ctx)
    # Trailing slash is trimmed to match the runtime write path.
    assert fs.access_checks == [
        ("viking://resources/restricted", "AclAction.WRITE")
    ]


@pytest.mark.asyncio
async def test_authorized_explicit_radar_target_passes_and_derived_skips_check():
    allowed_fs = _AuthProbeFs()
    await authorize_pr_radar_target(
        allowed_fs,
        {"pr_radar": {"enabled": True, "target_uri": "viking://resources/ok"}},
        _ctx,
    )
    assert len(allowed_fs.access_checks) == 1

    # A derived target (no explicit target_uri) lives in the caller's own
    # namespace and must not trigger an extra authorization call.
    derived_fs = _AuthProbeFs()
    await authorize_pr_radar_target(
        derived_fs, {"pr_radar": {"enabled": True}}, _ctx
    )
    await authorize_pr_radar_target(derived_fs, None, _ctx)
    assert derived_fs.access_checks == []


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


def test_signal_store_keeps_distinct_versions_and_dedupes_reemits():
    from openviking.resource.github_radar.collector import _build_pr
    from openviking.resource.github_radar.signal_store import RadarSignalStore

    old_open = _build_pr(_pr_row(7, "2026-11-01T00:30:00Z"))
    merged_row = _pr_row(7, "2026-11-01T00:45:00Z")
    merged_row["state"] = "closed"
    merged_row["pull_request"]["merged_at"] = merged_row["updated_at"]
    newer_merged = _build_pr(merged_row)

    # A newer snapshot is a distinct version, not a replacement: the open
    # event still belongs to its own window and the merge event to the next.
    merged = RadarSignalStore.merge([old_open], [newer_merged])
    assert [pr.number for pr in merged] == [7, 7]
    assert merged[0].merged_at == ""
    assert merged[1].merged_at == "2026-11-01T00:45:00Z"

    # Equal-timestamp re-emit (inclusive since duplicate) keeps the first one.
    duplicate = _build_pr(_pr_row(7, "2026-11-01T00:45:00Z"))
    assert RadarSignalStore.merge([newer_merged], [duplicate]) == [newer_merged]


def test_partition_renders_one_version_per_pr_within_a_window():
    from openviking.resource.github_radar.collector import _build_pr
    from openviking.resource.pr_radar_runner import partition_pending

    start, end = "2026-11-01T00:00:00Z", "2026-11-02T00:00:00Z"
    open_v = _build_pr(_pr_row(7, "2026-11-01T00:30:00Z"))
    merged_row = _pr_row(7, "2026-11-01T01:30:00Z")
    merged_row["state"] = "closed"
    merged_row["pull_request"]["merged_at"] = merged_row["updated_at"]
    merged_v = _build_pr(merged_row)
    next_window_v = _build_pr(_pr_row(8, "2026-11-03T00:00:00Z"))

    in_window, deferred = partition_pending(
        [open_v, merged_v, next_window_v], start, end
    )
    assert [pr.number for pr in in_window] == [7]
    # The newest in-window snapshot wins for rendering.
    assert in_window[0].merged_at == "2026-11-01T01:30:00Z"
    assert [pr.number for pr in deferred] == [8]


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


@pytest.mark.asyncio
async def test_first_run_since_matches_labeled_backfill_window():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import fmt_ts

    class RecordingGh:
        def __init__(self) -> None:
            self.since_values: list[str] = []

        async def api_get_json(self, path, params=None):
            self.since_values.append(params["since"])
            return []

    fs = FakeFs()
    gh = RecordingGh()
    # Watch created three days ago; a delayed first trigger must not shrink
    # the queried range relative to the labeled [anchor-backfill, anchor] window.
    anchor = datetime.now(timezone.utc) - timedelta(days=3)
    task = SimpleNamespace(
        task_id="watch-1",
        path=GITHUB_URL,
        to_uri=WATCH_TO_URI,
        processor_kwargs={"pr_radar": {"enabled": True, "backfill_days": 7}},
        created_at=anchor,
        watch_interval=1.0,
        user_id="alice",
    )

    result = await PrRadarRunner(fs, gh=gh).maybe_run(task, _ctx)

    assert result.status == "empty"
    assert gh.since_values[0] == fmt_ts(anchor - timedelta(days=7))


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


def test_plan_digest_steady_boundary_waits_for_caught_up():
    from datetime import datetime, timedelta, timezone

    from openviking.resource.github_radar.models import RadarState, fmt_ts
    from openviking.resource.pr_radar_runner import plan_digest

    now = datetime.now(timezone.utc)
    last_digest = now - timedelta(hours=2)
    config = RadarConfig.from_kwargs(
        {"pr_radar": {"enabled": True, "digest_interval_minutes": 60}}
    )
    kwargs = {
        "state": RadarState(last_digest_at=fmt_ts(last_digest)),
        "config": config,
        "pending_count": 1,
        "anchor": now,
        "now": now,
    }

    # Boundary due but collection capped/failed mid-window: hold the boundary.
    incomplete = plan_digest(caught_up=False, **kwargs)
    assert incomplete.action == "accumulate"
    assert incomplete.advance_to == ""

    # Once the full window is read, the same due boundary closes normally.
    complete = plan_digest(caught_up=True, **kwargs)
    assert complete.action == "digest"
    assert complete.advance_to == complete.window_end


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


class _SinceGh:
    """GitHub-like fixture: filters `since` inclusively and paginates by offset."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def api_get_json(self, path, params=None):
        rows = sorted(
            (r for r in self.rows if r["updated_at"] >= params["since"]),
            key=lambda r: r["updated_at"],
        )
        per_page = int(params["per_page"])
        start = (int(params["page"]) - 1) * per_page
        return rows[start : start + per_page]


@pytest.mark.asyncio
async def test_steady_window_waits_for_full_collection_before_closing():
    # Two in-window PRs, cap=1: trigger 1 reads only #1, trigger 2 reads #2
    # but ends on a full page (caught_up still False), trigger 3 reads the
    # empty confirmation page and only then does one report cover both PRs.
    fs = FakeFs()
    base = "2026-11-01T00:00:00Z"
    _seed_state(fs, base)
    gh = _SinceGh(
        [_pr_row(1, "2026-11-01T00:10:00Z"), _pr_row(2, "2026-11-01T00:20:00Z")]
    )
    task = SimpleNamespace(
        task_id="watch-1",
        path=GITHUB_URL,
        to_uri=WATCH_TO_URI,
        user_id="alice",
        processor_kwargs={
            "pr_radar": {
                "enabled": True,
                "target_uri": _RADAR_URI,
                "max_prs_per_run": 1,
                "digest_interval_minutes": 60,
            }
        },
    )
    runner = PrRadarRunner(fs, gh=gh)

    first = await runner.maybe_run(task, _ctx)
    assert first.status == "accumulating"
    second = await runner.maybe_run(task, _ctx)
    assert second.status == "accumulating"
    assert not any(uri.endswith(LATEST_REPORT_NAME) for uri in fs.files)
    # Digest boundary held while collection is incomplete.
    assert _read_state(fs)["last_digest_at"] == base

    third = await runner.maybe_run(task, _ctx)
    assert third.status == "published"
    latest = fs.files[next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))]
    assert "#1" in latest and "#2" in latest
    assert fs.files[_signals_uri(fs)] == ""
    assert _read_state(fs)["last_digest_at"] == "2026-11-01T01:00:00Z"


@pytest.mark.asyncio
async def test_newer_queued_snapshot_reaches_report_after_merge():
    # An open snapshot is already queued; before its window closes the PR
    # merges. The newer merged snapshot must replace the stale one and be
    # published instead of being dropped by number-only dedup.
    fs = FakeFs()
    base = "2026-11-01T00:00:00Z"
    _seed_state(fs, base)
    old = _build_pr(_pr_row(7, "2026-11-01T00:30:00Z"))
    fs.files[f"{_RADAR_URI}/{SIGNALS_FILENAME}"] = json.dumps(old.to_row())

    merged_row = _pr_row(7, "2026-11-01T00:45:00Z")
    merged_row["state"] = "closed"
    merged_row["pull_request"]["merged_at"] = merged_row["updated_at"]
    task = SimpleNamespace(
        task_id="watch-1",
        path=GITHUB_URL,
        to_uri=WATCH_TO_URI,
        user_id="alice",
        processor_kwargs={
            "pr_radar": {
                "enabled": True,
                "target_uri": _RADAR_URI,
                "digest_interval_minutes": 60,
            }
        },
    )
    result = await PrRadarRunner(fs, gh=_SinceGh([merged_row])).maybe_run(task, _ctx)

    assert result.status == "published"
    latest = fs.files[next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))]
    assert "2026-11-01T00:45:00Z" in latest
    assert fs.files[_signals_uri(fs)] == ""


@pytest.mark.asyncio
async def test_partial_digest_is_marked_and_later_report_continues_it():
    from unittest.mock import patch

    fs = FakeFs()
    base = "2026-11-10T00:00:00Z"
    _seed_state(fs, base)
    rows = [
        _pr_row(1, "2026-11-10T00:10:00Z"),
        _pr_row(2, "2026-11-10T00:20:00Z"),
        _pr_row(3, "2026-11-10T00:30:00Z"),
    ]
    task = SimpleNamespace(
        task_id="watch-1",
        path=GITHUB_URL,
        to_uri=WATCH_TO_URI,
        user_id="alice",
        processor_kwargs={
            "pr_radar": {
                "enabled": True,
                "target_uri": _RADAR_URI,
                "max_prs_per_run": 1,
                "max_pending_prs": 2,
                "digest_interval_minutes": 1440,
            }
        },
    )
    runner = PrRadarRunner(fs, gh=_SinceGh(rows))

    async def run_at(ts):
        with patch("openviking.resource.pr_radar_runner.have_gh", return_value=True), \
             patch("openviking.resource.pr_radar_runner.utc_now",
                   return_value=datetime.fromisoformat(ts.replace("Z", "+00:00"))):
            return await runner.maybe_run(task, _ctx)

    partial = None
    partial_stamp = None
    for ts in [
        "2026-11-11T01:00:00Z",
        "2026-11-12T01:00:00Z",
        "2026-11-13T01:00:00Z",
        "2026-11-14T01:00:00Z",
        "2026-11-15T01:00:00Z",
    ]:
        result = await run_at(ts)
        if result.status == "published" and result.warnings:
            partial = result
            # Assert immediately: a later full digest consumes the fragment.
            partial_archive = [
                body
                for uri, body in fs.files.items()
                if "/reports/" in uri and "Partial digest" in body
            ]
            assert len(partial_archive) == 1
            fragments = _read_state(fs)["partial_fragments"]
            assert len(fragments) == 1 and "stamp" in fragments[0]
            partial_stamp = fragments[0]["stamp"]

    assert partial is not None
    # By the end the full digest has closed the boundary and consumed the
    # fragment log, while its continuation banner references the archive.
    final_md = fs.files[
        next(uri for uri in fs.files if uri.endswith(LATEST_REPORT_NAME))
    ]
    assert "Continuation" in final_md
    assert partial_stamp in final_md
    assert _read_state(fs)["partial_fragments"] == []


@pytest.mark.asyncio
async def test_cross_window_versions_are_both_archived():
    from unittest.mock import patch

    fs = FakeFs()
    fs.files[f"{_RADAR_URI}/{STATE_FILENAME}"] = json.dumps({
        "issues_since": "2026-11-10T02:00:00Z",
        "issues_seen": [],
        "last_digest_at": "2026-11-10T00:00:00Z",
    })
    old_open = _build_pr(_pr_row(7, "2026-11-10T01:00:00Z"))
    fs.files[f"{_RADAR_URI}/{SIGNALS_FILENAME}"] = json.dumps(old_open.to_row())

    merged_row = _pr_row(7, "2026-11-11T00:30:00Z")
    merged_row["state"] = "closed"
    merged_row["pull_request"]["merged_at"] = merged_row["updated_at"]
    task = SimpleNamespace(
        task_id="watch-1",
        path=GITHUB_URL,
        to_uri=WATCH_TO_URI,
        user_id="alice",
        processor_kwargs={
            "pr_radar": {"enabled": True, "target_uri": _RADAR_URI,
                         "digest_interval_minutes": 1440}
        },
    )
    runner = PrRadarRunner(fs, gh=_SinceGh([merged_row]))

    async def run_at(ts):
        with patch("openviking.resource.pr_radar_runner.have_gh", return_value=True), \
             patch("openviking.resource.pr_radar_runner.utc_now",
                   return_value=datetime.fromisoformat(ts.replace("Z", "+00:00"))):
            return await runner.maybe_run(task, _ctx)

    await run_at("2026-11-11T01:00:00Z")  # window W1: open snapshot
    await run_at("2026-11-12T01:00:00Z")  # window W2: merged snapshot

    archives = [body for uri, body in fs.files.items() if "/reports/" in uri]
    assert len(archives) == 2
    # The older open observation is archived in its own window rather than
    # being erased by the newer merged version.
    assert any("2026-11-10T01:00:00Z" in body for body in archives)
    assert any("2026-11-11T00:30:00Z" in body for body in archives)
    assert fs.files[_signals_uri(fs)] == ""
