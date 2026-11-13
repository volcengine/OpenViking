# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource.github_radar.collector import GitHubCollector
from openviking.resource.github_radar.models import QueryRange
from tests.utils.radar_fixtures import ANCHOR, config, node, page, state


async def test_collect_accumulates_80_prs_across_runs(monkeypatch):
    collector = GitHubCollector("unused-test-token")
    search = AsyncMock(return_value=page([node(number) for number in range(1, 81)]))
    monkeypatch.setattr(collector, "search", search)
    progress, queue = state(), []
    save = AsyncMock()
    assert not await collector.collect(progress, queue, config(), ANCHOR, save)
    assert len(queue) == 50
    assert progress.collected_until is None
    assert await collector.collect(progress, queue, config(), ANCHOR, save)
    assert {item.number for item in queue} == set(range(1, 81))
    assert progress.collected_until == ANCHOR
    assert search.call_count == 2
    assert save.await_count > 0


async def test_collect_pagination_failure_preserves_facts(monkeypatch):
    collector = GitHubCollector("unused-test-token")
    search = AsyncMock(
        side_effect=[
            page([node(1)], count=2, cursor="page2"),
            OSError("network failure"),
        ]
    )
    monkeypatch.setattr(collector, "search", search)
    progress, queue = state(), []
    save = AsyncMock()
    with pytest.raises(OSError, match="network"):
        await collector.collect(progress, queue, config(), ANCHOR, save)
    assert [item.number for item in queue] == [1]
    assert progress.collected_until is None
    assert progress.ranges
    search.side_effect = None
    search.return_value = page([node(1), node(2)])
    assert await collector.collect(progress, queue, config(), ANCHOR, save)
    assert [item.number for item in queue] == [1, 2]
    assert search.call_args.args[2] is None


async def test_collect_splits_search_limit(monkeypatch):
    collector = GitHubCollector("unused-test-token")
    calls = []

    async def search(repository, span, cursor):
        calls.append(span)
        if len(calls) == 1:
            return page([], count=1001)
        return page([])

    monkeypatch.setattr(collector, "search", search)
    progress, queue = state(), []
    assert await collector.collect(progress, queue, config(), ANCHOR, AsyncMock())
    assert len(calls) == 3
    assert calls[1].end + timedelta(seconds=1) == calls[2].start
    assert calls[1].start == progress.backfill_start
    assert calls[2].end == ANCHOR


@pytest.mark.parametrize("bad_count,bad_cursor", [(2, None), (1, "")])
async def test_collect_does_not_advance_incomplete_search(monkeypatch, bad_count, bad_cursor):
    collector = GitHubCollector("unused-test-token")
    monkeypatch.setattr(
        collector,
        "search",
        AsyncMock(return_value=page([node()], count=bad_count, cursor=bad_cursor)),
    )
    progress = state()
    with pytest.raises(RuntimeError):
        await collector.collect(progress, [], config(), ANCHOR, AsyncMock())
    assert progress.collected_until is None


@pytest.mark.parametrize(
    "token,gh_token,github_token,expected_token",
    [
        (None, None, None, None),
        ("account-token", "environment-token", None, "account-token"),
        (None, "environment-token", None, "environment-token"),
        (None, None, "github-environment-token", "github-environment-token"),
    ],
)
async def test_search_uses_gh_login_or_explicit_token(
    monkeypatch, token, gh_token, github_token, expected_token
):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    if gh_token:
        monkeypatch.setenv("GH_TOKEN", gh_token)
    if github_token:
        monkeypatch.setenv("GITHUB_TOKEN", github_token)
    response = page([])
    child = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(json.dumps({"data": {"search": response}}).encode(), b"")
        ),
    )
    spawn = AsyncMock(return_value=child)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    collector = GitHubCollector(token)
    span = QueryRange(start=ANCHOR - timedelta(days=1), end=ANCHOR)
    assert await collector.search("a/b", span, None) == response
    spawn.assert_awaited_once()
    assert spawn.call_args.args == (
        "gh",
        "api",
        "--hostname",
        "github.com",
        "graphql",
        "--input",
        "-",
    )
    env = spawn.call_args.kwargs["env"]
    assert env["GH_PROMPT_DISABLED"] == "1"
    if expected_token is None:
        assert "GH_TOKEN" not in env
        assert "GITHUB_TOKEN" not in env
    else:
        assert env["GH_TOKEN"] == expected_token
    child.communicate.assert_awaited_once()
    payload = json.loads(child.communicate.call_args.args[0])
    assert payload["variables"]["query"].startswith("repo:a/b is:pr is:merged ")
