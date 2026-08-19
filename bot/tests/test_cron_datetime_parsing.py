# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for cron ISO datetime parsing."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.agent.tools.cron import CronTool  # noqa: E402
from vikingbot.cli.commands import cron_add  # noqa: E402
from vikingbot.config.schema import SessionKey  # noqa: E402
from vikingbot.cron.service import CronService  # noqa: E402


def test_cron_cli_accepts_z_datetime(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.config.loader.get_data_dir", lambda: tmp_path)

    cron_add(
        name="release",
        message="ship it",
        every=None,
        cron_expr=None,
        at="2099-08-17T10:00:00Z",
        deliver=False,
    )

    jobs = CronService(tmp_path / "cron" / "jobs.json").list_jobs()
    assert len(jobs) == 1
    assert jobs[0].schedule.at_ms == 4090644000000


def test_cron_cli_reports_invalid_datetime(tmp_path, monkeypatch):
    monkeypatch.setattr("vikingbot.config.loader.get_data_dir", lambda: tmp_path)

    with pytest.raises(typer.Exit):
        cron_add(
            name="release",
            message="ship it",
            every=None,
            cron_expr=None,
            at="not-a-date",
            deliver=False,
        )


@pytest.mark.asyncio
async def test_cron_tool_accepts_z_datetime(tmp_path):
    service = CronService(tmp_path / "jobs.json")
    tool = CronTool(service)
    context = SimpleNamespace(
        session_key=SessionKey(type="cli", channel_id="default", chat_id="default"),
        channel_metadata=None,
    )

    result = await tool.execute(
        context,
        action="add",
        name="release",
        message="ship it",
        at="2099-08-17T10:00:00Z",
    )

    assert result.startswith("Created job")
    assert service.list_jobs()[0].schedule.at_ms == 4090644000000


@pytest.mark.asyncio
async def test_cron_tool_reports_invalid_datetime(tmp_path):
    tool = CronTool(CronService(tmp_path / "jobs.json"))
    context = SimpleNamespace(
        session_key=SessionKey(type="cli", channel_id="default", chat_id="default"),
        channel_metadata=None,
    )

    result = await tool.execute(
        context,
        action="add",
        name="release",
        message="ship it",
        at="not-a-date",
    )

    assert result.startswith("Error: invalid at datetime:")
