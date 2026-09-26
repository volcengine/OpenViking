# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Experience usage counting: reporter mode, auto-recall events, local store."""

import asyncio
from types import SimpleNamespace

import pytest

from openviking.observability.events import register_event_subscriber, unregister_event_subscriber
from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore
from openviking.server.config import UsageReporterConfig
from openviking.server.routers import search as search_router
from openviking.session.session import Session
from openviking.usage_reporter import EventBusUsageSink, UsageEvent, UsageReporter
from openviking.usage_reporter.config import build_usage_reporter
from openviking.usage_reporter.context_recall import build_context_recall_events

EXP = "viking://user/alice/memories/experiences/lock-fix.md"


def _entries(*uris):
    return [SimpleNamespace(uri=uri) for uri in uris]


def _usage_event(event_type="memory.injected", occurred_at="2026-09-20T08:00:00Z", **evidence):
    return UsageEvent(
        event_type=event_type,
        resource_uri=EXP,
        resource_type="experience",
        account_id="acct",
        user_id="alice",
        session_id="s1",
        occurred_at=occurred_at,
        evidence={"message_id": "m1", "tool_call_id": "t1", **evidence},
    )


def test_default_reporter_follows_agent_evolution():
    reporter = build_usage_reporter(UsageReporterConfig())
    assert reporter is not None
    assert reporter.follows_agent_evolution is True
    assert [type(sink) for sink in reporter.sinks] == [EventBusUsageSink]
    assert reporter.reports_for(agent_evolution_enabled=True)
    assert not reporter.reports_for(agent_evolution_enabled=False)

    forced = build_usage_reporter(UsageReporterConfig(enabled=True))
    assert forced.reports_for(agent_evolution_enabled=False)
    assert build_usage_reporter(UsageReporterConfig(enabled=False)) is None


def test_context_recall_events_keep_only_own_experience_files():
    events = build_context_recall_events(
        entries=_entries(
            EXP,
            EXP,
            "viking://user/alice/memories/experiences/.abstract.md",
            "viking://user/bob/memories/experiences/other.md",
            "viking://user/alice/memories/events/2026/09/20/x.md",
        ),
        account_id="acct",
        user_id="alice",
        session_id="s1",
    )
    assert [(e.event_type, e.resource_uri) for e in events] == [("memory.recalled", EXP)]


def test_every_context_recall_request_counts():
    # The session turn restarts after a commit on legacy sessions, so keying on
    # it would merge distinct recalls into one event.
    def event_id():
        (event,) = build_context_recall_events(
            entries=_entries(EXP), account_id="acct", user_id="alice", session_id="s1"
        )
        return event.event_id

    assert event_id() != event_id()


@pytest.mark.asyncio
async def test_bus_sink_feeds_store_and_dedupes_replayed_events(tmp_path):
    captured = []
    register_event_subscriber("test-capture", captured.append)
    try:
        replayed = _usage_event()
        await EventBusUsageSink().write(
            events=[
                replayed,
                replayed,  # a replayed commit re-emits the same deterministic id
                _usage_event("memory.recalled", message_id="m2"),
                _usage_event("memory.recalled", "2026-09-10T08:00:00Z", message_id="m3"),
            ]
        )
    finally:
        unregister_event_subscriber("test-capture")

    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3", usage_retention_days=0)
    await store.initialize()
    try:
        await store.record_batch(captured)
        await store.record_batch(captured[:1])  # a second batch replays it again

        assert await store.get_experience_usage(account_id="acct", resource_uri=EXP) == {
            "recall_count": 2,
            "inject_count": 1,
        }
        assert await store.get_experience_usage(
            account_id="acct", resource_uri=EXP, start_date_utc="2026-09-15"
        ) == {"recall_count": 1, "inject_count": 1}
        assert (await store.get_experience_usage(account_id="other", resource_uri=EXP)) == {
            "recall_count": 0,
            "inject_count": 0,
        }

        await store.delete_data(account_id="acct", user_id="alice")
        assert await store.get_experience_usage(account_id="acct", resource_uri=EXP) == {
            "recall_count": 0,
            "inject_count": 0,
        }
    finally:
        await store.close()


class _RecordingSink:
    def __init__(self):
        self.events = []

    async def write(self, *, events):
        self.events.extend(events)


def _service(reporter, *, evolution_enabled):
    async def get_agent_evolution_enabled(account_id):
        return evolution_enabled

    return SimpleNamespace(
        sessions=SimpleNamespace(
            usage_reporter=reporter,
            get_agent_evolution_enabled=get_agent_evolution_enabled,
        )
    )


async def _report(service, stats):
    ctx = SimpleNamespace(account_id="acct", user=SimpleNamespace(user_id="alice"))
    await search_router._report_context_recall(
        service=service,
        ctx=ctx,
        session_id="s1",
        result=SimpleNamespace(entries=_entries(EXP), stats=stats),
    )
    await asyncio.gather(*search_router._USAGE_REPORT_TASKS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evolution_enabled", "stats", "expected"),
    [
        (True, {}, 1),
        (False, {}, 0),  # auto mode follows Agent Evolution
        (True, {"rewrite": "no_relevant"}, 0),  # blanked block reached nobody
    ],
)
async def test_auto_recall_reports_served_experiences(evolution_enabled, stats, expected):
    sink = _RecordingSink()
    reporter = UsageReporter(sinks=[sink], follows_agent_evolution=True)
    await _report(_service(reporter, evolution_enabled=evolution_enabled), stats)
    assert len(sink.events) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("evolution_enabled", "called"), [(True, True), (False, False)])
async def test_commit_usage_reporting_follows_agent_evolution(evolution_enabled, called):
    calls = []

    class _Reporter(UsageReporter):
        async def extract_and_report(self, *, messages, context):
            calls.append(context)
            return []

    session = SimpleNamespace(
        _usage_reporter=_Reporter(follows_agent_evolution=True),
        ctx=SimpleNamespace(account_id="acct", user=SimpleNamespace(user_id="alice")),
        session_id="s1",
    )
    await Session._run_usage_reporting(
        session,
        task_id="t",
        archive_uri="viking://user/alice/sessions/s1/history/archive_001",
        messages=[],
        agent_evolution_enabled=evolution_enabled,
    )
    assert bool(calls) is called
