"""Directory TTL on public content writes and summary reads."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core import ttl
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils.config.ttl_config import TTLConfig
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.storage.ttl_test_storage import MemoryAGFS, read_record


@pytest.fixture
def ttl_fs(monkeypatch):
    fs = VikingFS(agfs=SimpleNamespace())
    fs._async_agfs = MemoryAGFS()
    monkeypatch.setattr(fs.ttl_registry, "account_may_have_records", AsyncMock(return_value=True))
    return fs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entrypoint,filename",
    [
        ("write", "event.md"),
        ("write", ".note.MD"),
        ("batch_write", "event.txt"),
        ("batch_write", ".note.TXT"),
        ("replace", "event.custom"),
        ("append", "event"),
    ],
)
@pytest.mark.parametrize("owner", ["user/default", "user/default/peers/assistant"])
async def test_public_write_entrypoints_share_directory_ttl(
    monkeypatch, entrypoint, owner, filename, binding_fs
):
    class Clock(datetime):
        current = datetime(2026, 1, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    config = TTLConfig(**{"global": {"mode": "days", "ttl_days": 1}})
    monkeypatch.setattr(ttl, "datetime", Clock)
    monkeypatch.setattr(ttl, "get_openviking_config", lambda: SimpleNamespace(ttl=config))
    root = f"viking://{owner}/memories/events/2026/09/28"
    uri = root + "/" + filename
    ctx = root_ctx()
    fs = binding_fs
    await fs.mkdir(root, ctx=ctx)
    writer = ContentWriteCoordinator(fs)
    monkeypatch.setattr(writer, "_refresh_batch", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "openviking.storage.content_write.MemoryUpdater.refresh_schema_overview",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "openviking.storage.content_write.MemoryUpdater.refresh_file_embedding",
        AsyncMock(return_value=False),
    )
    # File frontmatter remains user content; only the owner metadata sets TTL.
    content = "---\nexpires_at: 2999-01-01T00:00:00Z\n---\nevent body"
    if entrypoint != "batch_write":
        await writer.write(
            uri=uri,
            content=content,
            mode="create" if entrypoint == "write" else entrypoint,
            ctx=ctx,
        )
    else:
        await writer.batch_write(
            root_uri=root,
            operations=[{"uri": uri, "content": content, "mode": "create"}],
            ctx=ctx,
        )

    record = await read_record(fs, ctx.account_id, root)
    assert record is not None
    assert record.object_type == "event"
    assert record.object_uri == root
    assert "event body" in await fs.read_file(uri, ctx=ctx)
    # Changing defaults alone cannot override the persisted deadline.
    config.global_default.mode = "disabled"
    Clock.current = datetime(2026, 1, 3, tzinfo=timezone.utc)
    for read in (fs.read_file, fs.read_file_bytes):
        with pytest.raises(NotFoundError):
            await read(uri, ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata,expiry,visible",
    [
        (".meta.json", "2000-01-01T00:00:00.000Z", False),
        (".meta.json", "2999-01-01T00:00:00.000Z", True),
        (".ttl.json", "2000-01-01T00:00:00.000Z", False),
    ],
)
async def test_summary_frontmatter_does_not_override_owner_deadline(
    ttl_fs, metadata, expiry, visible
):
    from openviking.storage.abstract_overview import render_abstract_overview

    fs, ctx = ttl_fs, root_ctx()
    parent = "viking://user/default/memories/events/2026/09/28"
    path = fs._uri_to_path(parent, ctx=ctx)
    fs._async_agfs.files.update(
        {
            path + "/" + metadata: json.dumps({"expires_at": expiry}).encode(),
            path + "/e.md": b"secret",
        }
    )
    for level, filename in enumerate((".abstract.md", ".overview.md")):
        fs._async_agfs.files[path + "/" + filename] = (
            render_abstract_overview(level, parent, "secret")
            .replace("---\n", "---\nexpires_at: 2999-12-01T00:00:00.000Z\n", 1)
            .encode()
        )
    for read in (fs.abstract, fs.overview):
        if visible:
            assert "secret" in await read(parent, ctx=ctx)
        else:
            with pytest.raises(NotFoundError):
                await read(parent, ctx=ctx)
    for filename in ("e.md", ".abstract.md", ".overview.md"):
        for read in (fs.read_file, fs.read_file_bytes):
            if visible:
                assert await read(parent + "/" + filename, ctx=ctx)
            else:
                with pytest.raises(NotFoundError):
                    await read(parent + "/" + filename, ctx=ctx)
