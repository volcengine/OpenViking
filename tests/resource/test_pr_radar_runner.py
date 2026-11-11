# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

import openviking.resource.pr_radar_runner as runner_module
from openviking.integrations.github_radar.gh_cli import GhCliError
from openviking.integrations.github_radar.models import (
    LATEST_REPORT_NAME,
    STATE_FILENAME,
    RadarConfig,
)
from openviking.resource.pr_radar_runner import PrRadarRunner, validate_pr_radar_config
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


def _pr_row(number: int) -> dict:
    ts = "2026-11-10T08:00:00Z"
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


def _task(processor_kwargs: dict | None = None, path: str = GITHUB_URL):
    return SimpleNamespace(
        task_id="watch-1",
        path=path,
        processor_kwargs=processor_kwargs or {},
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
    assert "volcengine__OpenViking" in state_uri
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
    assert any(uri.endswith(f"radar/volcengine__OpenViking/{LATEST_REPORT_NAME}") for uri in uris)
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
