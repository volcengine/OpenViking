"""Identical observation contract against real SQLite and PostgreSQL stores."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit import performance_store as ledger
from openviking_cli.exceptions import ConflictError, InvalidArgumentError

NOW = datetime.now(timezone.utc)
START = (NOW - timedelta(hours=1)).isoformat()
END = (NOW + timedelta(hours=1)).isoformat()


def event(*, account="a", user="u", key="e", **payload):
    return ObservabilityEvent(
        "vlm.call", payload, timestamp=NOW, account_id=account, user_id=user, event_id=key
    )


async def read(store, **kwargs):
    return await store.query_performance(
        account_id="a", user_id="u", start=START, end=END, **kwargs
    )


async def open_store(factory, **kwargs):
    store = factory(performance_enabled=True, **kwargs)
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_duplicate_events_reconcile_legacy_and_new_reads(usage_store_factory):
    store = await open_store(usage_store_factory)
    call = event(
        prompt_tokens=10, completion_tokens=3, prompt_cached_tokens=7, completion_reasoning_tokens=2
    )
    await store.record_batch([call, call])
    await store.record_batch([call])
    result = await read(store)
    assert result["summary"]["observations"] == 1
    assert result["summary"]["token_totals"]["input_tokens"] == {
        "known_tokens": 10,
        "unknown_events": 0,
    }
    assert result["summary"]["token_totals"]["cached_tokens"]["known_tokens"] == 7
    legacy = await store.get_today_tokens(
        account_id="a", user_date=NOW.date().isoformat(), tz=ZoneInfo("UTC"), user_id="u"
    )
    assert legacy["vlm_input"] == 10 and legacy["vlm_output"] == 3
    with pytest.raises(ConflictError):
        await store.record_batch([replace(call, payload={"prompt_tokens": 999})])
    assert (await read(store))["summary"] == result["summary"]


@pytest.mark.asyncio
async def test_claim_rolls_back_with_projection_and_uncertain_commit_replays_once(
    usage_store_factory, monkeypatch
):
    store = await open_store(usage_store_factory)
    call = event(prompt_tokens=11)
    original = store._write_token_rows

    def fail(*args):
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr(store, "_write_token_rows", fail)
    with pytest.raises(RuntimeError):
        await store.record_batch([call])
    assert (await read(store))["summary"]["observations"] == 0
    monkeypatch.setattr(store, "_write_token_rows", original)
    await store.record_batch([call])
    # Caller retries as though the committed reply was lost.
    await store.record_batch([call])
    assert (await read(store))["summary"]["observations"] == 1


@pytest.mark.asyncio
async def test_two_independent_connections_claim_one_event(usage_store_factory):
    first = await open_store(usage_store_factory)
    second = await open_store(usage_store_factory)
    call = event(prompt_tokens=2)
    await asyncio.gather(*[s.record_batch([call]) for s in [first, second] * 10])
    assert (await read(first))["summary"]["observations"] == 1


@pytest.mark.asyncio
async def test_restart_and_late_event_snapshot_pagination(usage_store_factory):
    first = await open_store(usage_store_factory)
    await first.record_batch([event(key=str(i), prompt_tokens=i, task_id="t") for i in range(4)])
    page = await read(first, view="details", page_size=2)
    assert len(page["items"]) == 2 and page["next_cursor"]
    assert all("seq" not in row for row in page["items"])
    await first.record_batch(
        [replace(event(key="late", prompt_tokens=99), timestamp=NOW - timedelta(minutes=10))]
    )
    await first.close()
    second = await open_store(usage_store_factory)
    next_page = await read(second, view="details", page_size=2, cursor=page["next_cursor"])
    assert len(next_page["items"]) == 2 and next_page["next_cursor"] is None
    assert next_page["summary"] == page["summary"]
    assert (await read(second))["summary"]["observations"] == 5
    with pytest.raises(InvalidArgumentError):
        await second.query_performance(
            account_id="a",
            user_id="another",
            start=START,
            end=END,
            view="details",
            cursor=page["next_cursor"],
        )


@pytest.mark.asyncio
async def test_same_ids_do_not_merge_other_accounts_or_users(usage_store_factory):
    store = await open_store(usage_store_factory)
    await store.record_batch(
        [
            event(prompt_tokens=1),
            event(user="v", prompt_tokens=2),
            event(account="b", prompt_tokens=8),
        ]
    )
    assert (await read(store))["summary"]["token_totals"]["input_tokens"]["known_tokens"] == 1
    result = await store.query_performance(
        account_id="a", user_id=None, start=START, end=END, view="workflows"
    )
    assert len(result["items"]) == 2
    await store.delete_data(account_id="a", user_id="u")
    assert (await read(store))["summary"]["observations"] == 0
    assert (await store.query_performance(account_id="b", user_id=None, start=START, end=END))[
        "summary"
    ]["observations"] == 1


@pytest.mark.asyncio
async def test_unknown_zero_subsets_and_overlapping_times(usage_store_factory):
    store = await open_store(usage_store_factory)
    await store.record_batch(
        [
            event(key="unknown"),
            event(key="zero", prompt_tokens=0, completion_tokens=2, prompt_cached_tokens=5),
            event(key="timed-1", started_at=NOW.timestamp(), duration_seconds=2),
            event(key="timed-2", started_at=NOW.timestamp() + 1, duration_seconds=2),
        ]
    )
    summary = (await read(store))["summary"]
    assert summary["token_totals"]["input_tokens"] == {"known_tokens": 0, "unknown_events": 3}
    assert summary["token_totals"]["cached_tokens"]["unknown_events"] == 4
    assert summary["duration_by_kind"]["vlm.call"]["observed_span_seconds"] == 4
    assert summary["observed_interval_union_seconds"] == 3
    assert summary["duration_by_kind"]["vlm.call"]["unknown_duration_observations"] == 2


@pytest.mark.asyncio
async def test_expired_receipt_cannot_resurrect_even_after_clock_rollback(usage_store_factory):
    store = await open_store(
        usage_store_factory,
        performance_retention_days=1,
        usage_retention_days=1,
        audit_retention_days=1,
    )
    call = event(prompt_tokens=4)
    await store.record_batch([call])
    original = ledger.claim

    def advance(conn, events, **kwargs):
        return original(conn, events, **{**kwargs, "now": NOW + timedelta(days=2)})

    with patch.object(ledger, "claim", advance):
        await store.record_batch([replace(event(key="new"), timestamp=NOW + timedelta(days=2))])
    # Real wall clock is now behind the persisted acceptance watermark.
    await store.record_batch([call])
    rows = store._conn.execute(
        "SELECT event_id FROM performance_events WHERE account_id='a'"
    ).fetchall()
    assert {r["event_id"] for r in rows} == {"new"}


@pytest.mark.asyncio
async def test_scoped_collection_health_includes_drops_failures_and_clean_shutdown(
    usage_store_factory,
):
    from openviking.observability.usage_audit.worker import UsageAuditWorker

    store = await open_store(usage_store_factory)
    worker = UsageAuditWorker(store, queue_size=1, batch_size=1)
    await worker.start()
    worker.enqueue(event(prompt_tokens=1))
    worker.enqueue(event(key="dropped"))
    await worker.flush()
    await worker.close(timeout_seconds=0.2)
    result = await read(store)
    producer = result["collection"]["producers"][0]
    assert producer["accepted"] == producer["persisted"] == 1
    assert producer["dropped"] == 1 and producer["pending"] == 0 and producer["clean"] == 1
    other = await store.query_performance(account_id="other", user_id=None, start=START, end=END)
    assert other["collection"]["producers"] == []
    assert result["billing_complete"] is False
    assert result["collection"]["unobserved_producers_possible"]


@pytest.mark.asyncio
async def test_shared_carrier_is_one_observation_without_fabricated_task_allocation(
    usage_store_factory,
):
    store = await open_store(usage_store_factory)
    await store.record_batch(
        [event(prompt_tokens=12, source_task_ids=["one", "two"], task_id="inherited-first")]
    )
    result = await read(store, view="details")
    assert result["summary"]["observed_model_events"] == 1
    assert result["summary"]["token_totals"]["input_tokens"]["known_tokens"] == 12
    assert result["items"][0]["workflow_id"] == "batch:e"
    assert result["items"][0]["task_id"] == ""


def _write_in_process(kind, location, schema, stamp, number):
    import asyncio
    from datetime import datetime

    from openviking.observability.events import ObservabilityEvent
    from openviking.observability.usage_audit.postgres_store import PostgresUsageAuditStore
    from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore

    async def run():
        store = (
            PostgresUsageAuditStore(location, schema=schema, performance_enabled=True)
            if kind == "postgres"
            else SQLiteUsageAuditStore(location, performance_enabled=True)
        )
        await store.initialize()
        try:
            timestamp = datetime.fromisoformat(stamp)
            for index in range(5):
                await store.record_batch(
                    [
                        ObservabilityEvent(
                            "vlm.call",
                            {"prompt_tokens": 7},
                            timestamp=timestamp,
                            account_id="a",
                            user_id="u",
                            event_id="shared-delivery",
                        ),
                        ObservabilityEvent(
                            "vlm.call",
                            {"prompt_tokens": 1},
                            timestamp=timestamp,
                            account_id="a",
                            user_id="u",
                            event_id=f"pod-{number}-{index}",
                        ),
                    ]
                )
        finally:
            await store.close()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_two_processes_reconcile_duplicate_deliveries_and_distinct_work(usage_store_factory):
    import multiprocessing

    store = await open_store(usage_store_factory)
    kind = "postgres" if hasattr(store, "_dsn") else "sqlite"
    location = store._dsn if kind == "postgres" else store._db_path
    schema = getattr(store, "_schema", None)
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_write_in_process, args=(kind, location, schema, NOW.isoformat(), number)
        )
        for number in range(2)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            await asyncio.to_thread(process.join, 20)
            assert process.exitcode == 0
        summary = (await read(store))["summary"]
        assert summary["observations"] == 11
        assert summary["token_totals"]["input_tokens"]["known_tokens"] == 17
        legacy = await store.get_today_tokens(
            account_id="a", user_date=NOW.date().isoformat(), tz=ZoneInfo("UTC"), user_id="u"
        )
        assert legacy["vlm_input"] == 17
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)


@pytest.mark.asyncio
async def test_known_fields_filters_and_subset_coverage_reconcile(usage_store_factory):
    store = await open_store(usage_store_factory)
    await store.record_batch(
        [
            event(
                key="actual",
                prompt_tokens=10,
                completion_tokens=4,
                prompt_cached_tokens=7,
                usage_quality="actual",
                usage_known_fields=["prompt_tokens", "completion_tokens", "prompt_cached_tokens"],
                model_name="m",
                status="success",
            ),
            event(
                key="missing",
                prompt_tokens=0,
                completion_tokens=0,
                usage_known_fields=[],
                model_name="other",
                status="failed",
            ),
        ]
    )
    result = await read(store, view="details", model="m", status="success")
    assert result["summary"]["observations"] == 1
    assert result["summary"]["cache_hit_rate"] == 0.7
    assert result["summary"]["cache_covered_events"] == 1
    all_rows = await read(store)
    assert all_rows["summary"]["token_totals"]["input_tokens"] == {
        "known_tokens": 10,
        "unknown_events": 1,
    }
    assert all_rows["summary"]["token_totals"]["reasoning_tokens"]["unknown_events"] == 2


@pytest.mark.asyncio
async def test_estimates_are_not_counted_as_reported_usage_or_cache_coverage(usage_store_factory):
    store = await open_store(usage_store_factory)
    await store.record_batch(
        [
            event(
                key="actual",
                prompt_tokens=10,
                prompt_cached_tokens=5,
                usage_quality="actual",
                usage_complete=False,
            ),
            event(
                key="estimated",
                prompt_tokens=30,
                prompt_cached_tokens=30,
                usage_quality="estimated",
            ),
            event(key="legacy", prompt_tokens=2),
        ]
    )
    summary = (await read(store))["summary"]
    assert summary["token_totals"]["input_tokens"]["known_tokens"] == 42
    assert summary["reported_token_totals"]["input_tokens"] == 10
    assert summary["estimated_token_totals"]["input_tokens"] == 30
    assert summary["unverified_token_totals"]["input_tokens"] == 2
    assert summary["incomplete_usage_events"] == 1
    assert summary["cache_hit_rate"] == 0.5 and summary["cache_covered_events"] == 1


@pytest.mark.asyncio
async def test_writer_failure_is_loss_visible_and_shutdown_drains_real_store(
    usage_store_factory, monkeypatch
):
    from openviking.observability.usage_audit.worker import UsageAuditWorker

    store = await open_store(usage_store_factory)
    original = store.record_batch
    worker = UsageAuditWorker(store, queue_size=4, batch_size=1)
    calls = 0

    async def fail_once(batch):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic disconnected writer")
        await original(batch)

    monkeypatch.setattr(store, "record_batch", fail_once)
    await worker.start()
    worker.enqueue(event(key="failed-delivery", prompt_tokens=2))
    await worker.flush()
    # Closing must flush the second accepted delivery even if the loop has not
    # yet consumed it. It must not disguise the first failed delivery as usage.
    worker.enqueue(event(key="shutdown-delivery", prompt_tokens=3))
    await worker.close(timeout_seconds=0.2)
    result = await read(store)
    assert result["summary"]["token_totals"]["input_tokens"]["known_tokens"] == 3
    producer = result["collection"]["producers"][0]
    assert producer["accepted"] == 2
    assert producer["failed"] == 1 and producer["persisted"] == 1
    assert producer["pending"] == 0 and producer["clean"] == 1
    assert result["billing_complete"] is False
