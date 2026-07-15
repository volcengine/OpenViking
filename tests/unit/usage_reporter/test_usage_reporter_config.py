# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import sys
from contextlib import asynccontextmanager

import pytest

from openviking.server.app import create_app
from openviking.server.config import ServerConfig, UsageReporterConfig
from openviking.usage_reporter import UsageContext, UsageEvent
from openviking.usage_reporter.config import build_usage_reporter


@pytest.mark.asyncio
async def test_custom_sink_is_loaded_from_class_path(tmp_path, monkeypatch):
    module_dir = tmp_path / "custom_sink_pkg"
    module_dir.mkdir()
    (module_dir / "__init__.py").write_text("", encoding="utf-8")
    output_path = tmp_path / "custom-events.jsonl"
    (module_dir / "sink.py").write_text(
        """
import json

class CustomUsageSink:
    def __init__(self, path):
        self.path = path

    async def write(self, *, events):
        with open(self.path, "a", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\\n")
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("custom_sink_pkg.sink", None)

    reporter = build_usage_reporter(
        UsageReporterConfig(
            enabled=True,
            extractors=["memory_usage"],
            sinks=[
                {
                    "type": "custom",
                    "class_path": "custom_sink_pkg.sink.CustomUsageSink",
                    "config": {"path": str(output_path)},
                }
            ],
        )
    )
    context = UsageContext(
        account_id="new",
        user_id="test",
        session_id="session-1",
        archive_uri="viking://user/test/sessions/session-1/history/archive_001",
        task_id="task-1",
    )
    event = UsageEvent(
        event_type="memory.injected",
        resource_uri="viking://user/test/memories/experiences/a.md",
        resource_type="experience",
        account_id="new",
        user_id="test",
        session_id="session-1",
        task_id="task-1",
        occurred_at="2026-07-09T12:00:00Z",
        evidence={"archive_uri": context.archive_uri},
    )

    await reporter.report(events=[event])

    payload = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["event_type"] == "memory.injected"
    assert payload["resource_uri"] == "viking://user/test/memories/experiences/a.md"
    assert payload["resource_type"] == "experience"
    assert "memory_uri" not in payload
    assert "source" not in payload


async def test_app_reuses_and_closes_usage_reporter(monkeypatch):
    built_reporters = []
    assigned_reporters = []

    class Reporter:
        closed = False

        async def close(self):
            self.closed = True

    reporter = Reporter()

    def build_reporter(config):
        del config
        built_reporters.append(reporter)
        return reporter

    class Sessions:
        def set_tool_output_externalization_config(self, config):
            del config

        def set_usage_reporter(self, value):
            assigned_reporters.append(value)

    class Service:
        sessions = Sessions()

    class TaskTracker:
        def start_cleanup_loop(self):
            return None

        def stop_cleanup_loop(self):
            return None

    async def initialize_runtime_state(app, service, config):
        del app, service, config

    @asynccontextmanager
    async def mcp_lifespan():
        yield

    monkeypatch.setattr(
        "openviking.usage_reporter.config.build_usage_reporter",
        build_reporter,
    )
    monkeypatch.setattr(
        "openviking.server.app._initialize_runtime_state",
        initialize_runtime_state,
    )
    monkeypatch.setattr("openviking.server.app.get_task_tracker", lambda: TaskTracker())
    monkeypatch.setattr("openviking.server.mcp_endpoint.mcp_lifespan", mcp_lifespan)

    app = create_app(config=ServerConfig(), service=Service())
    async with app.router.lifespan_context(app):
        pass

    assert built_reporters == [reporter]
    assert assigned_reporters == [reporter, reporter]
    assert reporter.closed is True
