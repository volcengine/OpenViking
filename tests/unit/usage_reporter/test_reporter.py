# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

from openviking.usage_reporter import UsageContext, UsageEvent, UsageReporter


def _context() -> UsageContext:
    return UsageContext(
        account_id="new",
        user_id="test",
        session_id="session-1",
        archive_uri="viking://user/test/sessions/session-1/history/archive_001",
        task_id="task-1",
    )


def _event() -> UsageEvent:
    context = _context()
    return UsageEvent(
        event_type="memory.injected",
        resource_uri="viking://user/test/memories/experiences/a.md",
        resource_type="experience",
        account_id=context.account_id,
        user_id=context.user_id,
        session_id=context.session_id,
        task_id=context.task_id,
        occurred_at="2026-07-10T12:00:00Z",
        evidence={"archive_uri": context.archive_uri},
    )


async def test_extractor_failure_is_ignored():
    class FailingExtractor:
        name = "failing"

        async def extract(self, *, messages, context):
            raise RuntimeError("extract failed")

    reporter = UsageReporter(extractors=[FailingExtractor()])

    assert await reporter.extract(messages=[], context=_context()) == []


async def test_sink_failure_does_not_stop_later_sinks():
    writes = []

    class FailingSink:
        async def write(self, *, events):
            raise RuntimeError("sink failed")

    class RecordingSink:
        async def write(self, *, events):
            writes.extend(events)

    event = _event()
    reporter = UsageReporter(sinks=[FailingSink(), RecordingSink()])

    await reporter.report(events=[event])

    assert writes == [event]


async def test_sink_timeout_does_not_stop_later_sinks():
    writes = []

    class HangingSink:
        async def write(self, *, events):
            await asyncio.sleep(1)

    class RecordingSink:
        async def write(self, *, events):
            writes.extend(events)

    event = _event()
    reporter = UsageReporter(
        sinks=[HangingSink(), RecordingSink()],
        sink_timeout_seconds=0.01,
    )

    await reporter.report(events=[event])

    assert writes == [event]


async def test_close_calls_optional_sink_close():
    closed = []

    class ClosableSink:
        async def write(self, *, events):
            return None

        async def close(self):
            closed.append(True)

    reporter = UsageReporter(sinks=[ClosableSink()])

    await reporter.close()

    assert closed == [True]
