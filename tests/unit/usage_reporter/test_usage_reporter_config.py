# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import sys
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking.server.config import ServerConfig, UsageReporterConfig
from openviking.service.core import OpenVikingService
from openviking.service.session_service import SessionService
from openviking.usage_reporter import FileLogUsageSink, UsageContext, UsageEvent, UsageReporter
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


def test_builtin_file_log_sink_is_built_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OV_RESOURCE_ID", "ov-test")
    log_path = tmp_path / "usage" / "usage.log"

    reporter = build_usage_reporter(
        UsageReporterConfig(
            enabled=True,
            extractors=["memory_usage"],
            sinks=[
                {
                    "type": "file_log",
                    "config": {
                        "path": str(log_path),
                        "rotation_interval_hours": 1,
                    },
                }
            ],
        )
    )

    assert reporter is not None
    assert len(reporter.sinks) == 1
    assert isinstance(reporter.sinks[0], FileLogUsageSink)
    assert reporter.sinks[0].path == log_path
    reporter.sinks[0].close()


async def test_service_configures_and_closes_usage_reporter(monkeypatch):
    reporter = AsyncMock(spec=UsageReporter)
    monkeypatch.setattr(
        "openviking.usage_reporter.config.build_usage_reporter",
        lambda config: reporter,
    )
    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = False
    service._background_prepared = False
    service._server_config = None
    service._directory_initializer = None
    service._session_service = Mock(spec=SessionService)
    service.configure_runtime(ServerConfig.model_validate({}))
    await service.release_runtime()

    reporter.close.assert_awaited_once()
    assert service._session_service.set_usage_reporter.call_args_list == [
        call(reporter),
        call(None),
    ]
