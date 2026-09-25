"""Resource TTL contracts: directory defaults, per-file lifetime and cleanup."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core import ttl
from openviking.server.identity import RequestContext, Role
from openviking.storage.resource_ttl import (
    prepare_resource_ttl,
    read_resource_fields,
    resource_ttl_fields,
    resource_ttl_visible,
    unchanged_expired_resource_paths,
    update_resource_expiry,
)
from openviking.storage.ttl_registry import TTLRegistry
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.ttl_config import ResourceTTL, TTLConfig
from tests.unit.service.test_ttl_cleanup import (
    _cleanup_once,
    _make_service,
    _message,
    _record,
    _session_meta,
)
from tests.unit.service.test_ttl_cleanup import tracker as tracker
from tests.unit.storage.test_ttl_registry import _MemoryAGFS

ROOT = "viking://user/u1/resources"
PAST = "2000-01-01T00:00:00.000Z"
FUTURE = "2999-01-01T00:00:00.000Z"


class MemoryAGFS(_MemoryAGFS):
    async def pathlock_acquire_exact(self, path, **kwargs):
        return await super().pathlock_acquire_exact(path)

    pathlock_acquire_tree = pathlock_acquire_exact

    async def ensure_parent_dirs(self, path, **kwargs):
        await super().ensure_parent_dirs(path)

    async def read(self, path, **kwargs):
        return await super().read(path)

    async def stat(self, path, **kwargs):
        self.stat_calls.append(path)
        if path in self.files:
            return {"isDir": False, "size": len(self.files[path])}
        if any(key.startswith(path.rstrip("/") + "/") for key in self.files):
            return {"isDir": True}
        raise FileNotFoundError(path)


@pytest.fixture
def fs_ctx(monkeypatch):
    monkeypatch.setattr(ttl, "get_openviking_config", lambda: SimpleNamespace(ttl=TTLConfig()))
    fs = VikingFS(agfs=SimpleNamespace())
    agfs = MemoryAGFS()
    fs._async_agfs = agfs
    fs.ttl_registry = TTLRegistry(agfs)
    fs._ensure_parent_dirs = AsyncMock()
    ctx = RequestContext(user=UserIdentifier("acct", "u1"), role=Role.ROOT)
    return fs, ctx


async def install(fs, ctx, uri, *, is_dir, expires_at=FUTURE):
    fields = {
        "received_at": "2020-01-01T00:00:00.000Z",
        "expires_at": expires_at,
        "ttl_generation": "g1",
    }
    kind = ttl.OBJECT_TYPE_RESOURCE if is_dir else ttl.OBJECT_TYPE_RESOURCE_FILE
    await fs.write_file(ttl.ttl_metadata_uri(kind, uri), json.dumps(fields), ctx=ctx)
    return fields


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri", [ROOT + "/manual.txt", "viking://user/u1/memories/events/manual.txt"]
)
async def test_set_relative_ttl_without_global_policy_preserves_content_time(fs_ctx, uri):
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
    from openviking.storage.document_ttl import update_document_expiry

    fs, ctx = fs_ctx
    await fs.write_file(uri, "keep this body", ctx=ctx)
    stat = fs._async_agfs.stat

    async def with_mtime(path, **kwargs):
        return {**await stat(path, **kwargs), "modTime": "2997-01-01T00:00:00Z"}

    fs._async_agfs.stat = with_mtime
    first = await update_document_expiry(fs, uri, ctx=ctx, ttl_relative=7)
    assert first["expires_at"] == "2997-01-08T00:00:00.000Z"
    changed = await update_document_expiry(fs, uri, ctx=ctx, ttl_relative=30)
    assert changed["expires_at"] == "2997-01-31T00:00:00.000Z"
    assert changed["received_at"] == first["received_at"]
    assert changed["ttl_generation"] == first["ttl_generation"]
    assert (await fs.ttl_registry.get(ctx.account_id, uri)).expires_at == changed["expires_at"]
    assert MemoryFileUtils.read(await fs.read_file(uri, ctx=ctx)).content == "keep this body"


@pytest.mark.asyncio
async def test_absolute_edit_equal_to_relative_expiry_does_not_renew(fs_ctx):
    fs, ctx = fs_ctx
    uri = ROOT + "/absolute-edit.txt"
    await fs.write_file(uri, "keep", ctx=ctx)
    fields = await prepare_resource_ttl(
        fs,
        uri,
        is_dir=False,
        existing=False,
        ctx=ctx,
        lease_ref=None,
        resource_ttl={"ttl_relative": 7},
        received_at=ttl.parse_iso_datetime("2997-01-01T00:00:00Z"),
    )
    await update_resource_expiry(fs, uri, fields["expires_at"], ctx=ctx)
    renewed = await prepare_resource_ttl(
        fs,
        uri,
        is_dir=False,
        existing=True,
        ctx=ctx,
        lease_ref=None,
        received_at=ttl.parse_iso_datetime("2997-01-03T00:00:00Z"),
    )
    assert renewed["expires_at"] == fields["expires_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [".abstract.md", ".overview.md", ".relations.json"])
async def test_resource_metadata_cannot_become_ttl_document(fs_ctx, name):
    from openviking.storage.document_ttl import get_document_ttl

    fs, ctx = fs_ctx
    uri = ROOT + "/" + name
    await fs.write_file(uri, "summary", ctx=ctx)
    with pytest.raises(InvalidArgumentError):
        await get_document_ttl(fs, uri, ctx=ctx)


@pytest.mark.asyncio
async def test_retry_after_registry_only_write_keeps_pending_deadline(fs_ctx):
    fs, ctx = fs_ctx
    uri = ROOT + "/interrupted"
    expected = await install(fs, ctx, uri, is_dir=False)
    metadata = ttl.ttl_metadata_uri(ttl.OBJECT_TYPE_RESOURCE_FILE, uri)
    del fs._async_agfs.files[fs._uri_to_path(metadata, ctx=ctx)]
    fields = await prepare_resource_ttl(
        fs,
        uri,
        is_dir=False,
        existing=False,
        ctx=ctx,
        lease_ref=None,
        resource_ttl={"ttl_relative": 7},
    )
    assert fields["expires_at"] == expected["expires_at"]
    assert fields["ttl_generation"] == expected["ttl_generation"]
    assert await read_resource_fields(fs, "resource_file", uri, ctx=ctx) == fields


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "root", [ROOT, "viking://resources", "viking://user/u1/peers/p1/resources"]
)
async def test_file_lifecycle_hides_only_its_exact_bytes(fs_ctx, root):
    fs, ctx = fs_ctx
    uri = root + "/doc"
    await fs.write_file_bytes(uri, b"\x00\xff original", ctx=ctx)
    await fs.write_file(ROOT + "/sibling", "keep", ctx=ctx)
    fields = await install(fs, ctx, uri, is_dir=False)
    assert await fs.read_file_bytes(uri, ctx=ctx) == b"\x00\xff original"
    assert (await fs.ttl_registry.get("acct", uri)).generation == "g1"
    fields["expires_at"] = PAST
    await install(fs, ctx, uri, is_dir=False, expires_at=PAST)
    with pytest.raises(NotFoundError):
        await fs.read_file_bytes(uri, ctx=ctx)
    assert await fs.read_file(ROOT + "/sibling", ctx=ctx) == "keep"
    # Removing metadata during a failed strict cleanup must not revive bytes.
    fs._async_agfs.files.pop(fs._uri_to_path(ttl.ttl_metadata_uri("resource_file", uri), ctx=ctx))
    assert not await resource_ttl_visible(fs, uri, ctx=ctx)


@pytest.mark.asyncio
async def test_directory_default_creates_independent_file_lifetimes(fs_ctx):
    fs, ctx = fs_ctx
    directory = ROOT + "/doc"
    first = await prepare_resource_ttl(
        fs,
        directory + "/first.txt",
        is_dir=False,
        existing=False,
        ctx=ctx,
        lease_ref=None,
        resource_ttl={"ttl_relative": 7},
        received_at=ttl.parse_iso_datetime("2026-01-01T00:00:00Z"),
    )
    second = await prepare_resource_ttl(
        fs,
        directory + "/second.txt",
        is_dir=False,
        existing=False,
        ctx=ctx,
        lease_ref=None,
        resource_ttl={"ttl_relative": 7},
        received_at=ttl.parse_iso_datetime("2026-01-03T00:00:00Z"),
    )
    assert first["expires_at"] == "2026-01-08T00:00:00.000Z"
    assert second["expires_at"] == "2026-01-10T00:00:00.000Z"
    assert first["ttl_generation"] != second["ttl_generation"]
    assert (await fs.ttl_registry.get("acct", directory + "/first.txt")).generation == first[
        "ttl_generation"
    ]
    assert (await fs.ttl_registry.get("acct", directory + "/second.txt")).generation == second[
        "ttl_generation"
    ]


@pytest.mark.asyncio
async def test_relative_resource_update_renews_but_absolute_deadline_does_not(fs_ctx):
    fs, ctx = fs_ctx
    relative_uri = ROOT + "/relative.txt"
    relative = await prepare_resource_ttl(
        fs,
        relative_uri,
        is_dir=False,
        existing=False,
        ctx=ctx,
        lease_ref=None,
        resource_ttl={"ttl_relative": 7},
        received_at=ttl.parse_iso_datetime("2997-01-01T00:00:00Z"),
    )
    renewed = await prepare_resource_ttl(
        fs,
        relative_uri,
        is_dir=False,
        existing=True,
        ctx=ctx,
        lease_ref=None,
        received_at=ttl.parse_iso_datetime("2997-01-10T00:00:00Z"),
    )
    assert renewed == {
        **relative,
        "received_at": "2997-01-10T00:00:00.000Z",
        "expires_at": "2997-01-17T00:00:00.000Z",
    }

    absolute_uri = ROOT + "/absolute.txt"
    absolute = await prepare_resource_ttl(
        fs,
        absolute_uri,
        is_dir=False,
        existing=False,
        ctx=ctx,
        lease_ref=None,
        resource_ttl={"ttl_absolute": 2_000_000_000},
        received_at=ttl.parse_iso_datetime("2026-01-01T00:00:00Z"),
    )
    unchanged = await prepare_resource_ttl(
        fs,
        absolute_uri,
        is_dir=False,
        existing=True,
        ctx=ctx,
        lease_ref=None,
        received_at=ttl.parse_iso_datetime("2026-01-10T00:00:00Z"),
    )
    assert unchanged == {**absolute, "received_at": "2026-01-10T00:00:00.000Z", "ttl_days": None}


@pytest.mark.asyncio
async def test_switch_absolute_back_to_relative_uses_latest_content_update(fs_ctx):
    from openviking.storage.document_ttl import update_document_expiry

    fs, ctx = fs_ctx
    uri = ROOT + "/switch.txt"
    await fs.write_file(uri, "updated body", ctx=ctx)
    await prepare_resource_ttl(
        fs,
        uri,
        is_dir=False,
        existing=False,
        ctx=ctx,
        lease_ref=None,
        resource_ttl={"ttl_relative": 7},
        received_at=ttl.parse_iso_datetime("2997-01-01T00:00:00Z"),
    )
    await update_document_expiry(fs, uri, FUTURE, ctx=ctx)
    await prepare_resource_ttl(
        fs,
        uri,
        is_dir=False,
        existing=True,
        ctx=ctx,
        lease_ref=None,
        received_at=ttl.parse_iso_datetime("2997-01-10T00:00:00Z"),
    )
    relative = await update_document_expiry(fs, uri, ctx=ctx, ttl_relative=7)
    assert relative["expires_at"] == "2997-01-17T00:00:00.000Z"
    assert relative["received_at"] == "2997-01-10T00:00:00.000Z"


@pytest.mark.asyncio
async def test_expired_directory_metadata_does_not_hide_live_child(fs_ctx):
    fs, ctx = fs_ctx
    directory = ROOT + "/legacy-directory"
    child = directory + "/still-live.txt"
    await fs.write_file(child, "keep", ctx=ctx)
    await install(fs, ctx, directory, is_dir=True, expires_at=PAST)
    await install(fs, ctx, child, is_dir=False, expires_at=FUTURE)

    assert (await resource_ttl_fields(fs, child, ctx=ctx))["expires_at"] == FUTURE
    assert await resource_ttl_visible(fs, child, ctx=ctx)
    assert await fs.read_file(child, ctx=ctx) == "keep"


@pytest.mark.asyncio
async def test_disabled_and_legacy_resources_do_not_gain_metadata(fs_ctx):
    fs, ctx = fs_ctx
    for existing, options in [(False, {}), (True, {"ttl_relative": 3})]:
        assert (
            await prepare_resource_ttl(
                fs,
                ROOT + "/legacy",
                is_dir=False,
                existing=existing,
                ctx=ctx,
                lease_ref=None,
                resource_ttl=options,
            )
            == {}
        )
    assert fs._async_agfs.files == {}


@pytest.mark.asyncio
async def test_expiry_edit_keeps_incarnation_and_updates_due_record(fs_ctx):
    fs, ctx = fs_ctx
    uri = ROOT + "/doc.txt"
    await fs.write_file(uri, "text", ctx=ctx)
    before = await install(fs, ctx, uri, is_dir=False)
    fs.stat = AsyncMock(return_value={"isDir": False})
    after = await update_resource_expiry(fs, uri, "2998-01-01T00:00:00Z", ctx=ctx)
    assert after["ttl_generation"] == before["ttl_generation"]
    assert after["received_at"] == before["received_at"]
    assert (await fs.ttl_registry.get("acct", uri)).expires_at == after["expires_at"]
    with pytest.raises(InvalidArgumentError):
        await update_resource_expiry(fs, uri, "bad timestamp", ctx=ctx)


@pytest.mark.asyncio
async def test_resource_directory_deadline_edit_is_rejected(fs_ctx):
    fs, ctx = fs_ctx
    uri = ROOT + "/folder"
    await fs.write_file(uri + "/child.txt", "text", ctx=ctx)
    fs.stat = AsyncMock(return_value={"isDir": True})

    with pytest.raises(InvalidArgumentError, match="resources/config"):
        await update_resource_expiry(fs, uri, FUTURE, ctx=ctx)


@pytest.mark.asyncio
async def test_watch_tombstone_only_suppresses_same_expired_content(fs_ctx):
    fs, ctx = fs_ctx
    uri = ROOT + "/watched/doc.txt"
    fields = await install(fs, ctx, uri, is_dir=False, expires_at=PAST)
    fields["content_md5"] = "old-md5"
    await fs.write_file(
        ttl.ttl_metadata_uri(ttl.OBJECT_TYPE_RESOURCE_FILE, uri),
        json.dumps(fields),
        ctx=ctx,
    )

    assert await unchanged_expired_resource_paths(
        fs, ROOT + "/watched", {"doc.txt": "old-md5"}, ctx=ctx
    ) == {"doc.txt"}
    assert (
        await unchanged_expired_resource_paths(
            fs, ROOT + "/watched", {"doc.txt": "new-md5"}, ctx=ctx
        )
        == set()
    )


@pytest.mark.asyncio
async def test_cleanup_uses_common_strict_delete_and_persistent_retry(tracker):
    record = _record("resource_file", object_uri=ROOT + "/doc")
    cleanup, fs, registry, queues = _make_service(
        record=record, live_content=_session_meta(), rm_error=RuntimeError("vector delete failed")
    )
    cleanup._service.fs = SimpleNamespace(rm=fs.rm)
    await cleanup._process(_message(record))
    registry.remove_if_generation.assert_not_awaited()
    registry.defer_retry.assert_awaited_once()
    assert fs.rm.await_args.kwargs["strict"] is True
    assert fs.rm.await_args.kwargs["recursive"] is False
    fs.rm.side_effect = None
    result = await _cleanup_once(cleanup, record)
    assert result["deleted"]
    registry.remove_if_generation.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_drops_legacy_directory_record_without_deleting_tree(tracker):
    record = _record("resource", object_uri=ROOT + "/doc")
    cleanup, fs, registry, _ = _make_service(
        record=record, live_content=_session_meta(expires_at=PAST)
    )
    cleanup._service.fs = SimpleNamespace(rm=fs.rm)

    result = await cleanup._cleanup_record(record)

    assert result == {"deleted": False, "skipped": "legacy_resource_directory"}
    fs.rm.assert_not_awaited()
    fs.remove_files.assert_awaited_once()
    registry.remove_if_generation.assert_awaited_once_with(
        record.account_id, record.object_uri, record.generation
    )


@pytest.mark.parametrize(
    "values",
    [
        {"ttl_relative": 0},
        {"ttl_relative": True},
        {"ttl_relative": 1.5},
        {"ttl_absolute": 1.5},
        {"ttl_relative": 1, "ttl_absolute": 2000000000},
    ],
)
def test_public_ttl_rejects_ambiguous_or_non_integral_values(values):
    with pytest.raises(ValueError):
        ResourceTTL(**values)


def test_policy_nearest_and_per_import_override():
    config = TTLConfig(
        resources={"mode": "days", "ttl_days": 30},
        directories={ROOT + "/a": {"mode": "days", "ttl_days": 7}},
    )
    fields = ttl.freeze_ttl_fields(ROOT + "/a/doc", config=config)
    assert fields["ttl_days"] == 7
    exact = ttl.freeze_ttl_fields(
        ROOT + "/a/doc", config=config, resource_ttl={"ttl_absolute": 2000000000}
    )
    assert ttl.parse_iso_datetime(exact["expires_at"]).timestamp() == 2000000000
    assert config.resolve_uri(ROOT + "/ab/doc", "resources") == 30
