# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Root configuration changes applied to actual persisted directory lifetimes."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking.config.binding import manager_over_source
from openviking.config.source import MemoryConfigSource
from openviking.message import TextPart
from openviking.service.ttl_policy import patch_ttl_configuration
from openviking.session.session import Session
from openviking.storage.directory_ttl import read_directory_fields
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.exceptions import FailedPreconditionError, NotFoundError
from openviking_cli.utils.config import get_openviking_config, set_openviking_config
from openviking_cli.utils.config.ttl_config import TTLConfig
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.service.test_ttl_cleanup import _cleanup_once
from tests.unit.storage.ttl_test_storage import read_record


@pytest_asyncio.fixture
async def configured_fs(binding_fs):
    original = get_openviking_config()
    manager = manager_over_source(
        MemoryConfigSource(), base_config=original.model_copy(update={"ttl": TTLConfig()})
    )
    await manager.initialize()
    binding_fs.runtime_config_manager = manager
    try:
        yield binding_fs, manager, root_ctx()
    finally:
        set_openviking_config(original)


async def patch(fs, manager, ctx, value):
    return await patch_ttl_configuration(fs, manager, {"ttl": value}, account_id=ctx.account_id)


@pytest.mark.asyncio
async def test_startup_applies_saved_policy_to_history_and_is_idempotent(configured_fs):
    from openviking.service.ttl_policy import apply_startup_ttl

    fs, manager, ctx = configured_fs
    owner = "viking://user/default/peers/peer1/memories/events/2020/01/01"
    await fs.write_file(owner + "/body.md", "historical peer event", ctx=ctx)
    original = await read_directory_fields(fs, owner, ctx=ctx)
    # The process stopped after configuration persistence, before application.
    await manager.patch_account(
        ctx.account_id, {"ttl": {"global": {"mode": "days", "ttl_days": 7}}}
    )
    await apply_startup_ttl(fs)
    fields = await read_directory_fields(fs, owner, ctx=ctx)
    assert fields["received_at"] == original["received_at"]
    assert parse_iso_datetime(fields["expires_at"]) == parse_iso_datetime(
        original["received_at"]
    ) + timedelta(days=7)
    await apply_startup_ttl(fs)
    assert await read_directory_fields(fs, owner, ctx=ctx) == fields


@pytest.mark.asyncio
async def test_enable_extend_disable_and_reenable_history_from_original_time(configured_fs):
    fs, manager, ctx = configured_fs
    root = "viking://user/default/memories/events"
    owner = root + "/2020/01/01"
    await fs.write_file(owner + "/a.md", "historical date, received recently", ctx=ctx)
    before = await read_directory_fields(fs, owner, ctx=ctx)
    base = format_iso8601(datetime.now(timezone.utc) - timedelta(days=2))
    await fs.write_file(owner + "/.meta.json", json.dumps({**before, "received_at": base}), ctx=ctx)
    await patch(fs, manager, ctx, {"global": {"mode": "days", "ttl_days": 7}})
    fields = await read_directory_fields(fs, owner, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) == parse_iso_datetime(base) + timedelta(days=7)
    await patch(fs, manager, ctx, {"global": {"ttl_days": 30}})
    fields = await read_directory_fields(fs, owner, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) == parse_iso_datetime(base) + timedelta(days=30)
    assert fields["received_at"] == base
    await patch(fs, manager, ctx, {"directories": {root: {"mode": "disabled"}}})
    fields = await read_directory_fields(fs, owner, ctx=ctx)
    assert not fields.get("expires_at") and fields["received_at"] == base
    assert await read_record(fs, ctx.account_id, owner) is None
    await patch(fs, manager, ctx, {"directories": {root: None}})
    assert parse_iso_datetime(
        (await read_directory_fields(fs, owner, ctx=ctx))["expires_at"]
    ) == parse_iso_datetime(base) + timedelta(days=30)


@pytest.mark.asyncio
async def test_priority_absolute_sessions_and_shortening_never_revive_expired(configured_fs):
    fs, manager, ctx = configured_fs
    session = Session(viking_fs=fs, session_id="s1", ctx=ctx)
    await session.ensure_exists()
    base = format_iso8601(datetime.now(timezone.utc) - timedelta(days=2))
    fields = await read_directory_fields(fs, session.uri, ctx=ctx)
    await fs.write_file(
        session.uri + "/.meta.json", json.dumps({**fields, "created_at": base}), ctx=ctx
    )
    root = session.uri.rsplit("/", 1)[0]
    absolute = int((datetime.now(timezone.utc) + timedelta(days=10)).timestamp())
    await patch(
        fs,
        manager,
        ctx,
        {
            "global": {"mode": "days", "ttl_days": 7},
            "sessions": {"mode": "days", "ttl_days": 3},
            "directories": {root: {"mode": "absolute", "ttl_absolute": absolute}},
        },
    )
    await session.add_message_async("user", [TextPart("does not renew absolute TTL")])
    fields = await read_directory_fields(fs, session.uri, ctx=ctx)
    assert int(parse_iso_datetime(fields["expires_at"]).timestamp()) == absolute
    assert not fields.get("ttl_days")
    await patch(fs, manager, ctx, {"global": {"ttl_days": 60}})
    assert (await read_directory_fields(fs, session.uri, ctx=ctx))["expires_at"] == fields[
        "expires_at"
    ]
    await patch(fs, manager, ctx, {"directories": {root: None}})
    fields = await read_directory_fields(fs, session.uri, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) == parse_iso_datetime(base) + timedelta(days=3)
    await patch(fs, manager, ctx, {"sessions": {"ttl_days": 1}})
    expired = await read_directory_fields(fs, session.uri, ctx=ctx)
    assert not await fs.exists(session.uri, ctx=ctx)
    await patch(fs, manager, ctx, {"sessions": {"ttl_days": 90}})
    assert await read_directory_fields(fs, session.uri, ctx=ctx) == expired
    await patch(fs, manager, ctx, {"sessions": {"mode": "disabled"}})
    assert await read_directory_fields(fs, session.uri, ctx=ctx) == expired


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["metadata", "users", "owners", "config"])
async def test_same_patch_retries_partial_application_failure(configured_fs, monkeypatch, failure):
    from openviking.config.scope import ConfigScope
    from openviking.service import ttl_policy

    fs, manager, ctx = configured_fs
    owner = "viking://user/default/memories/events/2026/10/01"
    await fs.write_file(owner + "/a.md", "body", ctx=ctx)
    original = await read_directory_fields(fs, owner, ctx=ctx)
    policy = {"user_events": {"mode": "days", "ttl_days": 7}}
    with monkeypatch.context() as m:
        if failure == "metadata":
            m.setattr(fs, "write_file", AsyncMock(side_effect=OSError("metadata unavailable")))
        elif failure == "config":
            current = await ttl_policy._config(fs, ctx.account_id)
            m.setattr(
                ttl_policy,
                "_config",
                AsyncMock(side_effect=[current, OSError("config unavailable")]),
            )
        else:
            path = "viking://user" if failure == "users" else owner.rsplit("/", 3)[0]
            failed_path = fs._uri_to_path(path, ctx=ctx)
            original_ls = fs._async_agfs.ls

            async def fail_listing(path, **kwargs):
                if path == failed_path:
                    raise OSError("listing unavailable")
                return await original_ls(path, **kwargs)

            m.setattr(fs._async_agfs, "ls", fail_listing)
        with pytest.raises(FailedPreconditionError, match="Retry the same configuration") as error:
            await patch(fs, manager, ctx, policy)
        assert error.value.details["failed_count"] == 1
        assert error.value.details["failures"][0]["account_id"] == ctx.account_id
        assert (await manager.get_settings(ConfigScope.account(ctx.account_id)))["ttl"] == policy
    await patch(fs, manager, ctx, policy)
    fields = await read_directory_fields(fs, owner, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) == parse_iso_datetime(
        original["received_at"]
    ) + timedelta(days=7)


@pytest.mark.asyncio
async def test_history_without_reliable_time_reports_incomplete_and_empty_directory_is_skipped(
    configured_fs,
):
    fs, manager, ctx = configured_fs
    root = "viking://user/default/memories/events"
    owner = root + "/2000/01/01"
    await fs._async_agfs.ensure_parent_dirs(fs._uri_to_path(owner + "/old.md", ctx=ctx))
    await fs._async_agfs.write(fs._uri_to_path(owner + "/old.md", ctx=ctx), b"legacy")
    await fs.mkdir(root + "/2000/01/02", ctx=ctx)
    with pytest.raises(FailedPreconditionError) as error:
        await patch(fs, manager, ctx, {"user_events": {"mode": "days", "ttl_days": 7}})
    assert error.value.details["failed_count"] == 1
    assert not (await read_directory_fields(fs, owner, ctx=ctx)).get("expires_at")


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [None, 7])
async def test_sibling_body_io_is_parallel_with_ttl_off_or_on(configured_fs, monkeypatch, days):
    fs, manager, ctx = configured_fs
    if days:
        await patch(fs, manager, ctx, {"global": {"mode": "days", "ttl_days": days}})
    owner = "viking://user/default/memories/events/2026/10/01"
    await fs.write_file(owner + "/seed.md", "first", ctx=ctx)
    original = fs._async_agfs.write
    all_entered, finish = asyncio.Event(), asyncio.Event()
    entered = 0

    async def write(path, data, **kwargs):
        nonlocal entered
        if path.endswith(".parallel"):
            entered += 1
            if entered == 8:
                all_entered.set()
            await finish.wait()
        return await original(path, data, **kwargs)

    monkeypatch.setattr(fs._async_agfs, "write", write)
    tasks = [
        asyncio.create_task(fs.write_file(f"{owner}/{i}.parallel", "body", ctx=ctx))
        for i in range(8)
    ]
    try:
        await asyncio.wait_for(all_entered.wait(), timeout=5)
    finally:
        finish.set()
        await asyncio.gather(*tasks)
    assert entered == 8


@pytest.mark.asyncio
async def test_policy_expiration_waits_for_unmaterialized_body_writer(configured_fs, monkeypatch):
    from openviking.service.ttl_cleanup import TTLCleanupService

    fs, manager, ctx = configured_fs
    owner = "viking://user/default/memories/events/2026/10/02"
    await fs.write_file(owner + "/seed.md", "seed", ctx=ctx)
    original = fs._async_agfs.write
    entered, finish = asyncio.Event(), asyncio.Event()

    async def write(path, data, **kwargs):
        if path.endswith("/new.md"):
            entered.set()
            await finish.wait()
        return await original(path, data, **kwargs)

    monkeypatch.setattr(fs._async_agfs, "write", write)
    task = asyncio.create_task(fs.write_file(owner + "/new.md", "in flight", ctx=ctx))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await patch(
            fs, manager, ctx, {"user_events": {"mode": "absolute", "ttl_absolute": 1000000000}}
        )
        record = await read_record(fs, ctx.account_id, owner)
        cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
        with pytest.raises(Exception, match="lock|Lock|conflict"):
            await _cleanup_once(cleanup, record)
    finally:
        finish.set()
        await task
    assert (await _cleanup_once(cleanup, record))["deleted"]
    assert not await fs.exists(owner, ctx=ctx, include_expired=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_explicit_empty_event_mkdir_is_visible_and_unmanaged(
    configured_fs, monkeypatch, enabled
):
    from openviking.service.fs_service import FSService

    fs, manager, ctx = configured_fs
    if enabled:
        await patch(fs, manager, ctx, {"user_events": {"mode": "days", "ttl_days": 7}})
    monkeypatch.setattr("openviking.service.fs_service.vectorize_directory_meta", AsyncMock())
    service = FSService(viking_fs=fs)
    owner = "viking://user/default/memories/events/2026/10/03"
    await service.mkdir(owner, ctx=ctx)
    assert await fs.exists(owner, ctx=ctx)
    assert not (await read_directory_fields(fs, owner, ctx=ctx)).get("expires_at")
    await fs.write_file(owner + "/first.md", "body", ctx=ctx)
    assert bool((await read_directory_fields(fs, owner, ctx=ctx)).get("expires_at")) == enabled


@pytest.mark.asyncio
async def test_absolute_policy_uses_deadline_without_fabricating_history_time(configured_fs):
    fs, manager, ctx = configured_fs
    owner = "viking://user/default/memories/events/2000/01/01"
    await fs._async_agfs.ensure_parent_dirs(fs._uri_to_path(owner + "/old.md", ctx=ctx))
    await fs._async_agfs.write(fs._uri_to_path(owner + "/old.md", ctx=ctx), b"legacy")
    deadline = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())
    await patch(fs, manager, ctx, {"user_events": {"mode": "absolute", "ttl_absolute": deadline}})
    fields = await read_directory_fields(fs, owner, ctx=ctx)
    assert int(parse_iso_datetime(fields["expires_at"]).timestamp()) == deadline
    assert not fields.get("received_at") and not fields.get("ttl_days")
    with pytest.raises(FailedPreconditionError):
        await patch(fs, manager, ctx, {"user_events": {"mode": "days", "ttl_days": 7}})


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_first", [False, True])
async def test_session_config_waiting_on_expiry_cannot_restore_old_metadata(
    configured_fs, monkeypatch, cleanup_first
):
    from openviking.service.session_service import SessionService
    from openviking.service.ttl_cleanup import TTLCleanupService

    fs, manager, ctx = configured_fs
    service = SessionService(viking_fs=fs)
    await patch(fs, manager, ctx, {"sessions": {"mode": "days", "ttl_days": 7}})
    session = await service.create(ctx, session_id="stale-config")
    old_expiry = session.meta.expires_at
    entered, resume = asyncio.Event(), asyncio.Event()
    original = Session.update_config

    async def delayed(self, **kwargs):
        entered.set()
        await resume.wait()
        await original(self, **kwargs)

    monkeypatch.setattr(Session, "update_config", delayed)
    request = asyncio.create_task(
        service.update_config(session.session_id, ctx, event_tags=["a=b"])
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await patch(
            fs, manager, ctx, {"sessions": {"mode": "absolute", "ttl_absolute": 1000000000}}
        )
        if cleanup_first:
            cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
            record = await read_record(fs, ctx.account_id, session.uri)
            assert (await _cleanup_once(cleanup, record))["deleted"]
        resume.set()
        with pytest.raises(NotFoundError):
            await request
        fields = await read_directory_fields(fs, session.uri, ctx=ctx)
        assert fields.get("expires_at") != old_expiry
        assert not await fs.exists(session.uri, ctx=ctx)
        assert await fs.exists(session.uri, ctx=ctx, include_expired=True) is not cleanup_first
    finally:
        resume.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_legacy_session_without_metadata_still_accepts_config(configured_fs):
    from openviking.service.session_service import SessionService

    fs, _, ctx = configured_fs
    service = SessionService(viking_fs=fs)
    session = await service.create(ctx, session_id="legacy-config")
    await session.add_message_async("user", [TextPart("keep this message")])
    messages = await fs.read_file(session.uri + "/messages.jsonl", ctx=ctx)
    await fs._async_agfs.rm(fs._uri_to_path(session.uri + "/.meta.json", ctx=ctx))
    updated = await service.update_config(session.session_id, ctx, event_tags=["case=legacy"])
    assert updated.meta.event_search_tags == ["case=legacy"]
    assert not updated.meta.expires_at
    assert await fs.read_file(session.uri + "/messages.jsonl", ctx=ctx) == messages


@pytest.mark.asyncio
async def test_policy_application_keeps_later_siblings_when_cleanup_removes_an_earlier_one(
    configured_fs, monkeypatch
):
    from openviking.service import ttl_policy
    from openviking.service.ttl_cleanup import TTLCleanupService

    fs, manager, ctx = configured_fs
    sessions = [Session(viking_fs=fs, ctx=ctx, session_id=f"case-{i:03}") for i in range(101)]
    for start in range(0, len(sessions), 8):
        await asyncio.gather(*(session.ensure_exists() for session in sessions[start : start + 8]))
    victim = sessions[0].uri
    fields = await read_directory_fields(fs, victim, ctx=ctx)
    await fs.write_file(
        victim + "/.meta.json",
        json.dumps({**fields, "expires_at": "2000-01-01T00:00:00Z"}),
        ctx=ctx,
    )
    cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
    apply_owner = ttl_policy._apply_owner
    removed = False

    async def concurrent_cleanup(fs, uri, ctx, *args, **kwargs):
        nonlocal removed
        result = await apply_owner(fs, uri, ctx, *args, **kwargs)
        if uri == sessions[95].uri:
            record = await read_record(fs, ctx.account_id, victim)
            assert (await _cleanup_once(cleanup, record))["deleted"]
            removed = True
        return result

    monkeypatch.setattr(ttl_policy, "_apply_owner", concurrent_cleanup)
    await patch(fs, manager, ctx, {"sessions": {"mode": "days", "ttl_days": 7}})
    assert removed
    for session in sessions[1:]:
        fields = await read_directory_fields(fs, session.uri, ctx=ctx)
        assert parse_iso_datetime(fields["expires_at"]) - parse_iso_datetime(
            fields["created_at"]
        ) == timedelta(days=7)
        assert await read_record(fs, ctx.account_id, session.uri) is not None


@pytest.mark.asyncio
async def test_other_workers_override_wins_and_new_objects_use_saved_policy(
    configured_fs, monkeypatch
):
    from openviking.config.ttl import resolve_ttl_config

    fs, writer, ctx = configured_fs
    reader = manager_over_source(writer._source, base_config=writer._base_config)
    await reader.initialize()
    fs.runtime_config_manager = reader
    await resolve_ttl_config(fs, ctx.account_id)
    fs.runtime_config_manager = writer
    await patch(fs, writer, ctx, {"sessions": {"mode": "days", "ttl_days": 30}})
    session = Session(viking_fs=fs, ctx=ctx, session_id="other-worker")
    await session.ensure_exists()
    fs.runtime_config_manager = reader
    await patch_ttl_configuration(fs, reader, {"ttl": {"global": {"mode": "days", "ttl_days": 7}}})
    fields = await read_directory_fields(fs, session.uri, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) - parse_iso_datetime(
        fields["created_at"]
    ) == timedelta(days=30)
    new_session = Session(viking_fs=fs, ctx=ctx, session_id="fresh-policy")
    await new_session.ensure_exists()
    assert parse_iso_datetime(new_session.meta.expires_at) - parse_iso_datetime(
        new_session.meta.created_at
    ) == timedelta(days=30)

    # Initialization reads current policies; ordinary writes use saved lifetimes.
    fs.runtime_config_manager = writer
    await patch(fs, writer, ctx, {"user_events": {"mode": "days", "ttl_days": 15}})
    fs.runtime_config_manager = reader
    event = "viking://user/default/memories/events/2020/01/01"
    await fs.write_file(event + "/body.md", "body", ctx=ctx)
    fields = await read_directory_fields(fs, event, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) - parse_iso_datetime(
        fields["received_at"]
    ) == timedelta(days=15)
    original_load = reader._source.load
    reads = []

    async def counting_load(scope):
        reads.append(scope)
        return await original_load(scope)

    monkeypatch.setattr(reader._source, "load", counting_load)
    await fs.write_file(event + "/more.md", "more", ctx=ctx)
    await new_session.add_message_async("user", [TextPart("ordinary append")])
    assert not reads
    await patch(fs, reader, ctx, {"sessions": {"ttl_days": 60}})
    # One fresh pair before the patch and one for the entire account application.
    assert len(reads) == 4
    fields = await read_directory_fields(fs, new_session.uri, ctx=ctx)
    assert parse_iso_datetime(fields["expires_at"]) - parse_iso_datetime(
        fields["created_at"]
    ) == timedelta(days=60)
