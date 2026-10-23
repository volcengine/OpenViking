import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import fakeredis.aioredis
import pytest
import pytest_asyncio

from openviking.ttl.adapter import TTLBackfillRequest, TTLRecycleRequest
from openviking.ttl.runtime import DirectoryTTL, next_check_ms
from openviking_cli.utils.config.ttl_config import TTLConfig


@pytest.fixture
def engine(monkeypatch, caplog):
    from openviking.ttl.runtime import logger

    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    e = DirectoryTTL(resource_id="r", timezone_name="UTC", stat_rate=100000, delete_interval=0)
    e.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    e.pair = (TTLConfig(sessions=7, user_events=7, peer_events=7), None)

    async def policies(_):
        return e.pair

    e.policies = policies
    e.fs = SimpleNamespace(
        ls=AsyncMock(return_value=[]),
        stat=AsyncMock(),
        rm=AsyncMock(),
        _uri_to_path=lambda uri, **kw: uri,
        _async_agfs=SimpleNamespace(
            pathlock_acquire_tree=AsyncMock(return_value={}),
            pathlock_release=AsyncMock(),
        ),
    )
    e.account_manager = SimpleNamespace(list_accounts=AsyncMock(return_value=[]))
    return e


@pytest.fixture
def scheduler(engine):
    scheduler = SimpleNamespace(register_handler=Mock(), report=AsyncMock())
    engine.install_expiry(scheduler)
    return scheduler


@pytest.mark.asyncio
async def test_config_reset_intent_precedes_save_and_disable_skips_redis(engine, scheduler):
    e = engine
    e.pair = (TTLConfig(sessions="disabled"), None)
    e.manager = SimpleNamespace(patch_account=AsyncMock())

    async def save(*args):
        state = await e.read_initial("a")
        assert state["reset_required"]["viking://user/_ttl_/sessions"]
        scheduler.report.assert_awaited_once()

    e.manager.patch_account.side_effect = save
    from openviking_cli.exceptions import UnavailableError

    scheduler.report.side_effect = ConnectionError("scheduler unavailable")
    with pytest.raises(UnavailableError):
        await e.patch_configuration({"ttl": {"sessions": 7}}, "a")
    e.manager.patch_account.assert_not_called()
    scheduler.report.reset_mock(side_effect=True)
    await e.patch_configuration({"ttl": {"sessions": 7}}, "a")
    redis = e.redis
    e.client = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
    e.manager.patch_account.reset_mock()
    with pytest.raises(UnavailableError):
        await e.patch_configuration({"ttl": {"sessions": 7}}, "a")
    e.manager.patch_account.assert_not_called()
    e.manager.patch_account.side_effect = None
    await e.patch_configuration({"ttl": {"sessions": "disabled"}}, "a")
    e.manager.patch_account.assert_awaited_once()
    assert await redis.hlen(e.prefix("a") + "backfill:reset_required") > 0
    scheduler.report.assert_awaited_once()


def test_hourly_window_is_stable_and_skips_missed_rounds():
    now = int(datetime(2026, 10, 20, 12, 34, 56, tzinfo=timezone.utc).timestamp() * 1000)
    first = next_check_ms("ov-one", now)
    assert now < first <= now + 3_600_000
    assert next_check_ms("ov-one", first) == first + 3_600_000
    assert next_check_ms("ov-one", now) == first
    assert next_check_ms("ov-two", now) != first


@pytest.mark.asyncio
async def test_incremental_keeps_buffer_until_schedule_is_durable(engine, scheduler):
    e = engine
    uri = "viking://user/u/sessions/s"
    await e.on_post_write(uri + "/messages.jsonl", e.context("a"), 1000)
    scheduler.report.side_effect = ConnectionError("scheduler unavailable")
    with pytest.raises(ConnectionError):
        await e._retry_writes()
    assert e._retry == {("a", "sessions", uri): 1000}
    scheduler.report.side_effect = None
    await e._retry_writes()
    await e.on_post_write(uri + "/messages.jsonl", e.context("a"), 2000)
    await e._retry_writes()
    assert not e._retry and scheduler.report.await_count == 2
    assert await e.redis.zscore(e.prefix("a") + "sessions", uri) == 2000


@pytest.mark.asyncio
@pytest.mark.parametrize("done", [False, True])
async def test_expiry_restart_resumes_checkpoint_without_full_scan(
    engine, scheduler, monkeypatch, done
):
    from openviking.scheduling.expiry.api import CallbackRequest, CallbackStatus

    e = engine
    root = "viking://user/u/sessions"
    prefix = e.prefix("a") + "backfill:"
    # Persisted state from a previous writer. Its in-memory queues are gone.
    checkpoint = {"root": 0, "stack": [{"uri": root, "last": root + "/old"}]}
    await e.redis.hset(
        prefix + "initial",
        mapping={
            "roots": json.dumps([root]),
            "checkpoint": json.dumps(checkpoint),
        },
    )
    if done:
        await e.redis.set(prefix + "initial:done", "1")
    else:
        await e.redis.sadd(prefix + "pending", "initial")
    e.fs.ls.return_value = [{"uri": root + "/old", "isDir": True}]
    await e.start()
    request = CallbackRequest(e._identity("a"))
    try:
        scheduler.report.side_effect = ConnectionError("Redis down")
        with pytest.raises(ConnectionError):
            await e.handle(request)
        assert not e._recycle and e._task is None
        scheduler.report.side_effect = None
        # An overdue task at restart only persists the next staggered wake-up.
        result = await e.handle(request)
        assert result.status is CallbackStatus.COMPLETED and e._task is None
        e.fs.ls.assert_not_called()
        e.account_manager.list_accounts.assert_not_called()
        monkeypatch.setattr(
            "openviking.ttl.runtime.time.time_ns", lambda: e._recycle_after_ms * 1_000_000
        )
        await e.handle(request)
        async with asyncio.timeout(2):
            await e._task
        assert (await e.read_initial("a"))["done"]
        assert e.fs.ls.await_count == (0 if done else 1)
        if not done:
            assert e.fs.ls.call_args.args == (root,)
        e.fs.stat.assert_not_called()  # The checkpoint excludes the old child.
        # Disable keeps a cheap durable wake-up, but creates no work or filesystem I/O.
        e.pair = (None, None)
        previous = e._task
        await e.handle(request)
        assert e._task is previous and not e._recycle
    finally:
        await e.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("peer_days", [None, "disabled", 7])
async def test_initial_discovers_roots_then_resumes_without_listing(engine, peer_days):
    e = engine
    explicit = "viking://user/other/peers/special/memories/events"
    e.pair = (TTLConfig(sessions=7, peer_events=peer_days, roots={explicit: 7}), None)
    tree = {
        "viking://user": ["viking://user/u"],
        "viking://user/u/peers": ["viking://user/u/peers/p"],
    }
    e.fs.ls.side_effect = lambda uri, **kw: [{"uri": u, "isDir": True} for u in tree.get(uri, [])]
    await e.register_initial_backfill(TTLBackfillRequest("r", "a"))
    state = await e.read_initial("a")
    roots = json.loads(state["roots"])
    assert explicit in roots and "viking://user/u/sessions" in roots
    assert ("viking://user/u/peers/p/memories/events" in roots) == (peer_days == 7)
    assert e.fs.ls.await_count == (2 if peer_days == 7 else 1)
    assert all(call.kwargs["node_limit"] is None for call in e.fs.ls.call_args_list)
    e.fs.ls.reset_mock()
    await e.register_initial_backfill(TTLBackfillRequest("r", "a"))
    e.fs.ls.assert_not_called()
    await e.redis.set(e.prefix("a") + "backfill:initial:done", "1")
    await e.recycle_due(TTLRecycleRequest("r", "a"))
    e.fs.ls.assert_not_called()


@pytest.mark.asyncio
async def test_hourly_trigger_recovers_intent_with_old_done(engine):
    e = engine
    root = "viking://user/u/sessions"
    e.pair = (TTLConfig(sessions=7), None)
    await e.register_initial_backfill(TTLBackfillRequest("r", "a", (root,)))
    prefix = e.prefix("a") + "backfill:"
    await e.redis.set(prefix + "initial:done", "1")
    await e.redis.hset(prefix + "reset_required", root, "saved-token")
    await e.recycle_due(TTLRecycleRequest("r", "a"))
    state = await e.read_initial("a")
    assert not state["done"] and state["pending"] and not state["reset_required"]
    e.fs.ls.reset_mock()
    await e.register_initial_backfill(TTLBackfillRequest("r", "a"))
    e.fs.ls.assert_not_called()


@pytest.mark.asyncio
async def test_write_scores_retry_and_expiry(engine):
    e = engine
    ctx = e.context("a")
    uri = "viking://user/u/sessions/s"
    for now in (2000, 1000, 2000):
        await e.on_post_write(uri + "/messages.jsonl", ctx, now)
    assert await e.redis.zscore(e.prefix("a") + "sessions", uri) is None
    await e._retry_writes()
    assert await e.redis.zscore(e.prefix("a") + "sessions", uri) == 2000
    e.fs.stat.assert_not_called()
    event = "viking://user/u/memories/events/2020/01/01"
    for now in (2000, 99999):
        await e.on_post_write(event + "/file.md", ctx, now)
    await e._retry_writes()
    score = await e.redis.zscore(e.prefix("a") + "user_events", event)
    assert score == 1577836800000
    echo = await e.resolve_expires_at(
        [uri, uri + "/archive/file", event, event + "/a", "viking://resources/a"], ctx
    )
    assert echo[uri] == echo[uri + "/archive/file"] == "1970-01-08T00:00:02Z"
    assert echo[event] == echo[event + "/a"] == "2020-01-08T00:00:00Z"
    assert echo["viking://resources/a"] is None
    register = e.redis.zadd
    e.redis.zadd = AsyncMock(side_effect=ConnectionError("redis down"))
    await e.on_post_write(uri + "/messages.jsonl", ctx, 5000)
    e.redis.zadd.assert_not_called()
    with pytest.raises(ConnectionError):
        await e._retry_writes()
    assert e._retry[("a", "sessions", uri)] == 5000
    e.redis.zadd = register
    await e._retry_writes()
    assert not e._retry
    assert await e.redis.zscore(e.prefix("a") + "sessions", uri) == 5000


@pytest.mark.asyncio
async def test_disabled_account_no_io(engine):
    e = engine
    e.pair = (TTLConfig(sessions=7), TTLConfig(sessions="disabled"))
    e.client = AsyncMock(side_effect=AssertionError("off path touched Redis"))
    e._dispatcher.run = AsyncMock(side_effect=AssertionError("off path dispatched TTL work"))
    uri = "viking://user/u/sessions/s"
    await e.on_post_write(uri + "/messages.jsonl", e.context("a"), 1)
    assert await e.resolve_expires_at([uri], e.context("a")) == {uri: None}
    assert not e.enabled(e.pair)
    await e.recycle_due(TTLRecycleRequest("r", "a", 999))
    async with e.write_guard(uri + "/messages.jsonl", e.context("a")):
        pass
    e.client.assert_not_called()
    e._dispatcher.run.assert_not_called()
    assert not e._retry and not e._wake.is_set()
    e.fs.stat.assert_not_called()
    e.fs._async_agfs.pathlock_acquire_tree.assert_not_called()


def test_event_dates_follow_writer_timezone(monkeypatch):
    import time

    from openviking.ttl.runtime import directory

    item = directory("viking://user/u/memories/events/2026/03/08")
    try:
        with monkeypatch.context() as env:
            env.setenv("TZ", "America/New_York")
            time.tzset()
            local = DirectoryTTL(resource_id="r")
            explicit = DirectoryTTL(resource_id="r", timezone_name="America/New_York")
            assert local.base(item) == explicit.base(item) == 1772946000000
            assert local.expiry(item, local.base(item), 1) == explicit.expiry(
                item, explicit.base(item), 1
            )
            # Calendar days retain local midnight across the DST transition.
            assert local.expiry(item, local.base(item), 1) - local.base(item) == 23 * 3600 * 1000
    finally:
        time.tzset()


@pytest.mark.asyncio
async def test_initial_partial_failure_reset_and_no_rescan(engine):
    e = engine
    root = "viking://user/u/sessions"
    uris = [root + "/a", root + "/b"]
    e.fs.ls.return_value = [{"uri": u, "isDir": True} for u in uris]
    e.fs.stat.return_value = {"modTime": "2020-01-01T00:00:00Z"}
    await e.redis.zadd(e.prefix("a") + "sessions", {uris[0]: 123})
    request = TTLBackfillRequest("r", "a", (root,))
    await e.register_initial_backfill(request)
    original = e.redis.zadd
    e.redis.zadd = AsyncMock(side_effect=ConnectionError("injected"))
    prefix = e.prefix("a") + "backfill:"
    before = await e.redis.hget(prefix + "initial", "checkpoint")
    with pytest.raises(ConnectionError):
        await e._scan("a")
    assert await e.redis.hget(prefix + "initial", "checkpoint") == before
    assert await e.redis.get(prefix + "initial:done") is None
    e.redis.zadd = original
    await e._scan("a")
    assert await e.redis.get(prefix + "initial:done") == "1"
    assert await e.redis.zscore(e.prefix("a") + "sessions", uris[0]) == 123
    assert all(call.args[0] == uris[1] + "/messages.jsonl" for call in e.fs.stat.call_args_list)
    e.fs.ls.reset_mock()
    await e.register_initial_backfill(request)
    await e._scan("a")
    e.fs.ls.assert_not_called()
    await e.redis.zrem(e.prefix("a") + "sessions", uris[1])
    await e.register_initial_backfill(TTLBackfillRequest("r", "a", (root,), reset=True))
    assert await e.redis.get(prefix + "initial:done") is None
    await e._scan("a")
    assert await e.redis.get(prefix + "initial:done") == "1"
    assert await e.redis.zscore(e.prefix("a") + "sessions", uris[1]) == 1577836800000
    assert all([await e.redis.ttl(key) == -1 for key in await e.redis.keys("*")])


@pytest.mark.asyncio
async def test_session_renewal_and_delete_retry(engine):
    e = engine
    uri = "viking://user/u/sessions/s"
    key = e.prefix("a") + "sessions"
    await e.redis.set(e.prefix("a") + "backfill:initial:done", "1")
    await e.redis.zadd(key, {uri: 1})
    now = datetime.now(timezone.utc)
    e.fs.stat.return_value = {"modTime": now.isoformat()}
    result = await e.recycle_due(TTLRecycleRequest("r", "a", 2**53 - 1))
    assert result.skipped >= 1
    e.fs.rm.assert_not_called()
    assert await e.redis.zscore(key, uri) > 1
    await e.redis.zadd(key, {uri: 1})
    e.fs.stat.return_value = {"modTime": "2000-01-01T00:00:00Z"}
    e.fs.rm.side_effect = OSError("delete failed")
    result = await e.recycle_due(TTLRecycleRequest("r", "a", 2**53 - 1))
    assert result.failed == 1 and await e.redis.zscore(key, uri) == 1
    e.fs.rm.side_effect = None
    result = await e.recycle_due(TTLRecycleRequest("r", "a", 2**53 - 1))
    assert result.deleted == 1 and await e.redis.zscore(key, uri) is None
    await e.redis.zadd(key, {uri: 1})
    e.fs.stat.side_effect = FileNotFoundError(uri)
    e.fs.rm.side_effect = FileNotFoundError(uri)
    result = await e.recycle_due(TTLRecycleRequest("r", "a", 2**53 - 1))
    assert result.deleted == 1 and await e.redis.zscore(key, uri) is None


@pytest.mark.asyncio
async def test_shutdown_finishes_inflight_delete_before_closing_redis(engine):
    e = engine
    uri = "viking://user/u/sessions/s"
    await e.redis.set(e.prefix("a") + "backfill:initial:done", "1")
    await e.redis.zadd(e.prefix("a") + "sessions", {uri: 1})
    e.fs.stat.return_value = {"modTime": "2000-01-01T00:00:00Z"}
    entered, release = asyncio.Event(), asyncio.Event()

    async def deleting(*args, **kwargs):
        entered.set()
        await release.wait()

    e.fs.rm.side_effect = deleting
    redis = e.redis
    redis.aclose = AsyncMock(wraps=redis.aclose)
    recycle = asyncio.create_task(e.recycle_due(TTLRecycleRequest("r", "a", 2**53 - 1)))
    await asyncio.wait_for(entered.wait(), timeout=1)
    closing = asyncio.create_task(e.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    redis.aclose.assert_not_called()
    release.set()
    assert (await recycle).deleted == 1
    await closing
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_waits_inflight_scan(engine):
    e = engine
    root = "viking://user/u/sessions"
    entered, release = asyncio.Event(), asyncio.Event()

    async def listing(*args, **kwargs):
        entered.set()
        await release.wait()
        return []

    e.fs.ls.side_effect = listing
    await e.register_initial_backfill(TTLBackfillRequest("r", "a", (root,)))

    async def scan():
        async with e._locks["a"]:
            await e._scan("a")

    old = asyncio.create_task(scan())
    await entered.wait()
    resetting = asyncio.create_task(
        e.register_initial_backfill(TTLBackfillRequest("r", "a", (root,), True))
    )
    await asyncio.sleep(0)
    assert not resetting.done()
    release.set()
    await old
    await resetting
    assert await e.redis.get(e.prefix("a") + "backfill:initial:done") is None
    assert await e.redis.sismember(e.prefix("a") + "backfill:pending", "initial")
    await e._scan("a")
    assert await e.redis.get(e.prefix("a") + "backfill:initial:done") == "1"


@pytest.mark.asyncio
async def test_missing_session_echo_does_not_stat(engine):
    e = engine
    uri = "viking://user/u/sessions/missing"
    assert await e.resolve_expires_at([uri], e.context("a")) == {uri: None}
    e.fs.stat.assert_not_called()


@pytest.mark.asyncio
async def test_date_scan_never_lists_day_or_session_archive(engine):
    e = engine
    root = "viking://user/u/memories/events"
    date = root + "/2020/01/01"
    directories = {
        root: [root + "/2020"],
        root + "/2020": [root + "/2020/01"],
        root + "/2020/01": [date],
    }

    async def listing(uri, **kw):
        assert uri in directories
        return [{"uri": value, "isDir": True} for value in directories[uri]]

    e.fs.ls.side_effect = listing
    await e.register_initial_backfill(TTLBackfillRequest("r", "a", (root,)))
    await e._scan("a")
    assert await e.redis.zscore(e.prefix("a") + "user_events", date) == 1577836800000
    e.fs.stat.assert_not_called()


@pytest_asyncio.fixture
async def reset_case(engine):
    root = "viking://user/u/sessions"
    prefix = engine.prefix("a") + "backfill:"
    await engine.redis.set(prefix + "initial:done", "1")
    await engine.redis.hset(prefix + "reset_required", root, "token")
    request = TTLBackfillRequest("r", "a", (root,), reset=True, reset_intents={root: "token"})
    return engine, root, prefix, request


@pytest.mark.asyncio
async def test_reset_intent_replay_keeps_new_checkpoint_and_newer_intent(reset_case):
    e, root, prefix, request = reset_case
    await e.redis.zadd(e.prefix("a") + "sessions", {root + "/kept": 123})
    await e.register_initial_backfill(request)
    assert await e.redis.get(prefix + "initial:done") is None
    assert await e.redis.hget(prefix + "reset_required", root) is None
    assert await e.redis.sismember(prefix + "pending", "initial")
    assert await e.redis.zscore(e.prefix("a") + "sessions", root + "/kept") == 123
    # The acknowledgement was lost. Neither a replay nor a late old request
    # may erase progress or consume a newer configuration's intent.
    await e.redis.hset(prefix + "initial", "checkpoint", "new-progress")
    await e.redis.set(prefix + "initial:done", "1")
    await e.register_initial_backfill(request)
    await e.redis.hset(prefix + "reset_required", root, "second")
    await e.register_initial_backfill(request)
    assert await e.redis.hget(prefix + "initial", "checkpoint") == "new-progress"
    assert await e.redis.get(prefix + "initial:done") == "1"
    assert await e.redis.hget(prefix + "reset_required", root) == "second"
    e.fs.ls.assert_not_called()


@pytest.mark.asyncio
async def test_reset_intent_redis_failure_then_retry(reset_case):
    e, root, prefix, request = reset_case
    original = e.redis.eval
    e.redis.eval = AsyncMock(side_effect=ConnectionError("before commit"))
    from openviking_cli.exceptions import UnavailableError

    with pytest.raises(UnavailableError):
        await e.register_initial_backfill(request)
    assert await e.redis.hget(prefix + "reset_required", root) == "token"
    assert await e.redis.get(prefix + "initial:done") == "1"
    e.redis.eval = original
    await e.register_initial_backfill(request)
    assert await e.redis.hget(prefix + "reset_required", root) is None
    assert await e.redis.get(prefix + "initial:done") is None
    e.fs.ls.return_value = [{"uri": root + "/missing", "isDir": True}]
    e.fs.stat.return_value = {"modTime": "2020-01-01T00:00:00Z"}
    await e._scan("a")
    assert await e.redis.zscore(e.prefix("a") + "sessions", root + "/missing") == 1577836800000
    assert await e.redis.get(prefix + "initial:done") == "1"


@pytest.mark.asyncio
async def test_disabled_reset_intent_does_not_scan_or_acknowledge(reset_case):
    e, root, prefix, request = reset_case
    e.pair = (TTLConfig(user_events=7, sessions="disabled"), None)
    await e.register_initial_backfill(request)
    assert await e.redis.hget(prefix + "reset_required", root) == "token"
    assert await e.redis.get(prefix + "initial:done") == "1"
    e.fs.ls.assert_not_called()


@pytest.mark.asyncio
async def test_async_batch_grouping_partial_failure_and_concurrent_update(engine):
    e = engine
    session = "viking://user/u/sessions/s"
    event = "viking://user/u/memories/events/2020/01/01"
    for uri, now in (
        (session + "/messages.jsonl", 1),
        (event + "/a", 1),
        (event + "/b", 2),
    ):
        await e.on_post_write(uri, e.context("a"), now)
    await e.on_post_write(session + "/messages.jsonl", e.context("b"), 1)
    original = e.redis.zadd

    async def register(key, entries, **options):
        if key == e.prefix("a") + "sessions":
            await e.on_post_write(session + "/messages.jsonl", e.context("a"), 3)
        if key == e.prefix("b") + "sessions":
            raise ConnectionError("one group failed")
        return await original(key, entries, **options)

    e.redis.zadd = AsyncMock(side_effect=register)
    with pytest.raises(ConnectionError):
        await e._retry_writes()
    assert e.redis.zadd.await_count == 3
    assert e.redis.zadd.call_args_list[1].kwargs == {"nx": True}
    assert len(e.redis.zadd.call_args_list[1].args[1]) == 1
    assert e._retry == {("a", "sessions", session): 3, ("b", "sessions", session): 1}
    e.redis.zadd = original
    await e._retry_writes()
    assert not e._retry
    assert await e.redis.zscore(e.prefix("a") + "sessions", session) == 3


@pytest.mark.asyncio
async def test_failed_head_batch_does_not_starve_later_registration(engine):
    e = engine
    e.batch_size = 1
    bad, good = ["viking://user/u/sessions/" + name for name in ("bad", "good")]
    await e.on_post_write(bad + "/messages.jsonl", e.context("a"), 1)
    await e.on_post_write(good + "/messages.jsonl", e.context("a"), 2)
    original = e.redis.zadd

    async def register(key, entries, **options):
        if bad in entries:
            raise ConnectionError("first directory failed")
        return await original(key, entries, **options)

    e.redis.zadd = AsyncMock(side_effect=register)
    with pytest.raises(ConnectionError):
        await e._retry_writes()
    await e._retry_writes()
    assert list(e._retry) == [("a", "sessions", bad)]
    assert await e.redis.zscore(e.prefix("a") + "sessions", good) == 2


@pytest.mark.asyncio
async def test_async_buffer_full_merges_existing_and_reports_drop(engine, caplog):
    e = engine
    e.retry_limit = 1
    first, second = ["viking://user/u/sessions/" + name for name in ("first", "second")]
    for uri, now in ((first, 1), (second, 2), (first, 3), (first, 2)):
        await e.on_post_write(uri + "/messages.jsonl", e.context("a"), now)
    assert e._retry == {("a", "sessions", first): 3}
    assert e.registration_dropped_total == 1
    assert "event=registration_dropped" in caplog.text and "dropped_total=1" in caplog.text
    e._registration_wake.clear()
    await e.on_post_write(first + "/messages.jsonl", e.context("a"), 4)
    assert not e._registration_wake.is_set()  # Updates do not defeat Redis failure backoff.


@pytest.mark.asyncio
async def test_async_worker_drains_bounded_batches(engine):
    e = engine
    e.batch_size = 512  # Incremental flush still caps the whole round at 256.
    for i in range(257):
        await e.on_post_write(f"viking://user/u/sessions/{i}/messages.jsonl", e.context("a"), i)
    original = e.redis.zadd

    async def register(*args, **kwargs):
        return await original(*args, **kwargs)

    e.redis.zadd = AsyncMock(side_effect=register)
    await e.start()
    try:
        async with asyncio.timeout(2):
            while e._retry:
                await asyncio.sleep(0.01)
        assert [len(call.args[1]) for call in e.redis.zadd.call_args_list] == [256, 1]
        assert all(call.kwargs == {"gt": True} for call in e.redis.zadd.call_args_list)
    finally:
        await e.aclose()


@pytest.mark.asyncio
async def test_shutdown_drains_registration_before_redis_close(engine):
    e = engine
    uri = "viking://user/u/sessions/s"
    await e.on_post_write(uri + "/messages.jsonl", e.context("a"), 1)
    redis = e.redis
    original = redis.zadd

    async def register(*args, **kwargs):
        return await original(*args, **kwargs)

    redis.zadd = AsyncMock(side_effect=register)
    redis.aclose = AsyncMock(wraps=redis.aclose)
    await e.aclose()
    redis.zadd.assert_awaited_once_with(e.prefix("a") + "sessions", {uri: 1}, gt=True)
    redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_registration_failure_is_retained_and_logged(engine, caplog):
    e = engine
    uri = "viking://user/u/sessions/s"
    e.redis.zadd = AsyncMock(side_effect=ConnectionError("redis down"))
    await e.on_post_write(uri + "/messages.jsonl", e.context("a"), 2)
    await e.aclose()
    assert e._retry == {("a", "sessions", uri): 2}
    assert "shutdown left volatile registrations pending=1" in caplog.text


@pytest.mark.asyncio
async def test_backfill_does_not_block_incremental_registration(engine):
    e = engine
    e.batch_size = 1
    root = "viking://user/u/sessions"
    event = "viking://user/u/memories/events/2020/01/01"
    e.fs.ls.return_value = [{"uri": root + "/" + name, "isDir": True} for name in ("a", "b")]

    async def stat(uri, **kwargs):
        if uri == root + "/a/messages.jsonl":
            await e.on_post_write(event + "/file", e.context("a"), 1)
        else:
            async with asyncio.timeout(2):
                while await e.redis.zscore(e.prefix("a") + "user_events", event) is None:
                    await asyncio.sleep(0)
        return {"modTime": "2020-01-01T00:00:00Z"}

    e.fs.stat.side_effect = stat
    await e.register_initial_backfill(TTLBackfillRequest("r", "a", (root,)))
    await e.start()
    try:
        async with asyncio.timeout(2):
            await e._task
        assert not e._retry and e.fs.stat.await_count == 2
    finally:
        await e.aclose()


@pytest.mark.asyncio
async def test_slow_delete_does_not_block_incremental_registration(engine):
    e = engine
    e.retry_limit = 16
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_rm(*args, **kwargs):
        entered.set()
        await release.wait()

    e.fs.rm.side_effect = slow_rm
    prefix = e.prefix("a")
    await e.redis.hset(
        prefix + "backfill:initial", mapping={"roots": "[]", "checkpoint": '{"root":0,"stack":[]}'}
    )
    await e.redis.set(prefix + "backfill:initial:done", "1")
    await e.redis.zadd(prefix + "user_events", {"viking://user/u/memories/events/2020/01/01": 1})
    await e.start()
    e._recycle.add("a")
    e._wake_worker()
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            for i in range(64):
                uri = f"viking://user/u/sessions/s{i}"
                await e.on_post_write(uri + "/messages.jsonl", e.context("a"), i)
                while await e.redis.zscore(prefix + "sessions", uri) is None:
                    await asyncio.sleep(0)
        assert await e.redis.zcard(prefix + "sessions") == 64
        assert e.registration_dropped_total == 0
        assert not release.is_set() and not e._task.done()
    finally:
        release.set()
        await e.aclose()


@pytest.mark.asyncio
async def test_initial_sorted_batches_resume_after_missing_cursor(engine):
    import json

    e = engine
    e.batch_size = 2
    root = "viking://user/u/sessions"
    uris = [root + "/" + name for name in ("a", "c", "d", "e", "f")]
    e.fs.ls.return_value = [{"uri": u, "isDir": True} for u in reversed(uris)]
    e.fs.stat.return_value = {"modTime": "2020-01-01T00:00:00Z"}
    await e.register_initial_backfill(TTLBackfillRequest("r", "a", (root,)))
    prefix = e.prefix("a") + "backfill:"
    await e.redis.hset(
        prefix + "initial",
        "checkpoint",
        json.dumps({"root": 0, "stack": [{"uri": root, "last": root + "/b"}]}),
    )
    await e._scan("a")
    assert await e.redis.zrange(e.prefix("a") + "sessions", 0, -1) == uris[1:]
    assert await e.redis.get(prefix + "initial:done") == "1"
    e.fs.ls.assert_awaited_once()
    assert e.fs.stat.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "root",
    [
        "viking://user/u/memories/events",
        "viking://user/other/peers/special/memories/events",
    ],
)
async def test_initial_event_parent_listed_once(engine, root):
    from collections import Counter

    e = engine
    e.pair = (TTLConfig(roots={root: 7}), None)
    tree = {root: [root + "/2020"]}
    year = root + "/2020"
    tree[year] = [year + f"/{m:02d}" for m in range(1, 13)]
    for month in tree[year]:
        tree[month] = [month + "/01"]
    e.fs.ls.side_effect = lambda uri, **kw: [{"uri": child, "isDir": True} for child in tree[uri]]
    await e.register_initial_backfill(TTLBackfillRequest("r", "a", (root,)))
    await e._scan("a")
    assert Counter(c.args[0] for c in e.fs.ls.call_args_list) == Counter(dict.fromkeys(tree, 1))
    kind = "peer_events" if "/peers/" in root else "user_events"
    assert await e.redis.zcard(e.prefix("a") + kind) == 12
    assert await e.redis.get(e.prefix("a") + "backfill:initial:done") == "1"
