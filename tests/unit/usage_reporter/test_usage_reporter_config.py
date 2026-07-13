# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import sys

import pytest

from openviking.server.config import UsageReporterConfig
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

    async def write(self, *, events, context):
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
        memory_uri="viking://user/default/memories/experiences/a.md",
        memory_type="experience",
        account_id="new",
        user_id="test",
        session_id="session-1",
        archive_uri=context.archive_uri,
        task_id="task-1",
        occurred_at="2026-07-09T12:00:00Z",
    )

    await reporter.report(events=[event], context=context)

    payload = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["event_type"] == "memory.injected"
    assert payload["memory_uri"] == "viking://user/default/memories/experiences/a.md"
