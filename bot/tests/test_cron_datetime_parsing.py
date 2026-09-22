# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for cron ISO datetime parsing."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from vikingbot.agent.tools.cron import CronTool
from vikingbot.cli.commands import cron_add
from vikingbot.config.schema import SessionKey
from vikingbot.cron.service import CronService, _compute_next_run
from vikingbot.cron.types import CronSchedule


@pytest.mark.parametrize("entrypoint", ["cli", "tool"])
@pytest.mark.parametrize(
    "options, expected_kind",
    [
        pytest.param({"at": "2099-08-17T10:00:00Z"}, "at", id="one-shot-utc"),
        pytest.param(
            {"cron_expr": "0 9 * * *", "timezone": "Asia/Shanghai"}, "cron", id="recurring-timezone"
        ),
    ],
)
async def test_cron_creation_persists_schedule(
    tmp_path, monkeypatch, entrypoint, options, expected_kind
):
    store_path = tmp_path / "cron" / "jobs.json"
    if entrypoint == "cli":
        monkeypatch.setattr("vikingbot.config.loader.get_data_dir", lambda: tmp_path)
        params = {"every": None, "cron_expr": None, "at": None, "timezone": None, **options}
        cron_add(name="release", message="ship it", deliver=False, **params)
    else:
        tool = CronTool(CronService(store_path))
        context = SimpleNamespace(
            session_key=SessionKey(type="cli", channel_id="default", chat_id="default"),
            channel_metadata=None,
        )
        result = await tool.execute(
            context, action="add", name="release", message="ship it", **options
        )
        assert result.startswith("Created job")
    jobs = CronService(store_path).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].schedule.kind == expected_kind
    if expected_kind == "at":
        assert jobs[0].schedule.at_ms == 4090644000000
    else:
        assert jobs[0].schedule.tz == "Asia/Shanghai"


def test_cron_schedule_uses_explicit_timezone():
    now_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    shanghai = _compute_next_run(
        CronSchedule(kind="cron", expr="0 9 * * *", tz="Asia/Shanghai"),
        now_ms,
    )
    los_angeles = _compute_next_run(
        CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Los_Angeles"),
        now_ms,
    )

    assert shanghai == int(datetime(2026, 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert los_angeles == int(datetime(2026, 1, 1, 17, tzinfo=timezone.utc).timestamp() * 1000)
