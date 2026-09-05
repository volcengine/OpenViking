from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


class _FakeAGFS:
    """Minimal synchronous AGFS stub for VikingFS write-path tests."""

    def __init__(self):
        self.storage = {}

    def ensure_parent_dirs(self, path, ctx=None):
        """Pretend parent directories already exist."""
        return None

    def write(self, path, data, ctx=None):
        """Record writes and simulate a successful backend write."""
        self.storage[path] = data
        return path

    def read(self, path, ctx=None):
        """Return stored data or raise FileNotFoundError when missing."""
        if path not in self.storage:
            raise FileNotFoundError(path)
        return self.storage[path]


class _AsyncAppendAGFS:
    """Async AGFS stub that records append lock ordering."""

    def __init__(self):
        self.events = []

    async def pathlock_acquire_exact(self, path):
        """Record exact acquisition and return an opaque lease ref."""
        lease = {"lease_ref": "lease-1"}
        self.events.append(("acquire", path, lease))
        return lease

    async def pathlock_release(self, lease):
        """Record lease release."""
        self.events.append(("release", lease))

    async def read(self, path, fs_ctx=None):
        """Record reads with propagated fs_ctx and return existing content."""
        self.events.append(("read", path, fs_ctx))
        return b"old"

    async def write(self, path, data, fs_ctx=None):
        """Record writes with propagated fs_ctx."""
        self.events.append(("write", path, data, fs_ctx))
        return len(data)


class _AsyncMoveAGFS:
    """Async AGFS stub that records move lock ownership and release."""

    def __init__(self, source_is_dir=False):
        self.acquire_calls = []
        self.tree_calls = []
        self.release_calls = []
        self.rm_calls = []
        self.source_is_dir = source_is_dir

    async def stat(self, path):
        """Return the configured source kind and a missing destination."""
        if path.endswith("/source.md") or path.endswith("/source"):
            return {"isDir": self.source_is_dir}
        if path.endswith("/resources"):
            return {"isDir": True}
        raise FileNotFoundError(path)

    async def pathlock_acquire_batch(
        self,
        requests,
        timeout_secs=0.0,
        owner_lease_ref=None,
    ):
        """Record the operation lease request."""
        self.acquire_calls.append((requests, owner_lease_ref))
        return {
            "lease_ref": "operation-ref",
            "owner_id": (owner_lease_ref["owner_id"] if owner_lease_ref else "operation-owner"),
            "ownership_ref": "operation-ownership",
            "owned": True,
        }

    async def pathlock_acquire_tree(
        self,
        path,
        timeout_secs=0.0,
        owner_lease_ref=None,
    ):
        """Record a temporary Tree extension used only for cleanup."""
        del timeout_secs
        self.tree_calls.append((path, owner_lease_ref))
        return {
            "lease_ref": "cleanup-ref",
            "owner_id": owner_lease_ref["owner_id"],
            "ownership_ref": "cleanup-ownership",
            "owned": True,
        }

    async def pathlock_release(self, lease):
        """Record operation lease release."""
        self.release_calls.append(lease)

    async def rm(self, path, recursive=False, fs_ctx=None):
        """Accept source removal under the operation lease."""
        self.rm_calls.append((path, recursive, fs_ctx))
        return {"path": path, "recursive": recursive, "fs_ctx": fs_ctx}


def _default_ctx() -> RequestContext:
    """Return a stable root request context for pathlock fs_ctx tests."""
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role(Role.ROOT))


@pytest.mark.parametrize(
    ("final_path", "temp_path"),
    [
        (
            "/local/default/resources/.abstract.md",
            "/local/default/temp/.encrypt_stage/1ea2c4fea9d85474a57fd03cac44d3bbe7c85fd6eb3c678c54044c4f49ecdbf7.encrypt",
        ),
        (
            "/local/default/resources/note.md",
            "/local/default/temp/.encrypt_stage/571a25aab6e6bca05a60a6e4aec646389a9ac38237daf55ceda4f72f3d1b4afe.encrypt",
        ),
        (
            "/s3/bucket/docs/a.md",
            "/s3/bucket/temp/.encrypt_stage/2a95445c0cdc88c4efdf32724651d61f72990a95d445fe0ce146150e39e5630d.encrypt",
        ),
        (
            "/note.md",
            "/temp/.encrypt_stage/71f3eca3f3ad54df082026dd6b40f2f3b2c2ba67b51bb5d24fda5632044c3228.encrypt",
        ),
        (
            ".abstract.md",
            "/temp/.encrypt_stage/2f3429bdbec8a484aec67cd1584e5dd64d8cf139d2fc4b4cca97d9cdc9b66ad3.encrypt",
        ),
    ],
)
def test_encrypted_temp_path_mapping_matches_rust(final_path, temp_path):
    """Keep Python temp-path mapping aligned with the Rust encryption wrapper."""
    fs = VikingFS(agfs=_FakeAGFS(), encryptor=object())
    assert fs._encrypted_temp_path(final_path) == temp_path


def test_pathlock_fs_ctx_rejects_malformed_explicit_lease():
    """An explicit malformed lease must fail instead of enabling a new auto-lock."""
    fs = VikingFS(agfs=_FakeAGFS())

    with pytest.raises(ValueError, match="lease_ref"):
        fs._pathlock_fs_ctx(_default_ctx(), {})


@pytest.mark.asyncio
async def test_append_file_holds_exact_lease_across_read_and_write(monkeypatch):
    """append_file should keep read and rewrite inside one exact pathlock lease."""
    fake = _AsyncAppendAGFS()
    fs = VikingFS(agfs=_FakeAGFS())
    fs._async_agfs = fake  # type: ignore[assignment]

    async def ensure_parent_dirs(path, ctx=None, lease_ref=None):
        """Record parent creation boundary without touching storage."""
        fake.events.append(("ensure_parent", path, lease_ref))

    monkeypatch.setattr(fs, "_ensure_parent_dirs", ensure_parent_dirs)
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda _uri, **_kwargs: "/local/default/resources/a.md",
    )
    monkeypatch.setattr(fs, "_ctx_or_default", lambda _ctx=None: _default_ctx())

    await fs.append_file("viking://a.md", "+new")

    assert fake.events[0] == ("ensure_parent", "/local/default/resources/a.md", None)
    assert fake.events[1][0] == "acquire"
    assert fake.events[2][0:2] == ("read", "/local/default/resources/a.md")
    assert fake.events[2][2]["lease_ref"] == "lease-1"
    assert fake.events[3][0:3] == (
        "write",
        "/local/default/resources/a.md",
        b"old+new",
    )
    assert fake.events[3][3]["lease_ref"] == "lease-1"
    assert fake.events[4] == ("release", {"lease_ref": "lease-1"})


@pytest.mark.asyncio
async def test_mv_extends_outer_lease_with_owned_capability(monkeypatch):
    """Move must acquire uncovered source locks with the outer owned capability."""
    fake = _AsyncMoveAGFS()
    fs = VikingFS(agfs=_FakeAGFS())
    fs._async_agfs = fake  # type: ignore[assignment]
    outer = {
        "lease_ref": "outer-ref",
        "owner_id": "outer-owner",
        "ownership_ref": "outer-ownership",
        "owned": True,
    }
    copied_with = []

    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, **_kwargs: (
            "/local/default/temp/source.md"
            if uri == "viking://temp/source.md"
            else "/local/default/resources/target.md"
        ),
    )
    monkeypatch.setattr(fs, "_collect_uris", AsyncMock(return_value=[]))
    monkeypatch.setattr(fs, "_delete_from_vector_store", AsyncMock())
    monkeypatch.setattr(fs, "_update_vector_store_uris", AsyncMock())

    async def record_copy(**kwargs):
        """Record the lease used by the copy stage."""
        copied_with.append(kwargs["lease_ref"])

    monkeypatch.setattr(fs, "_copy_for_mv", record_copy)

    await fs.mv(
        "viking://temp/source.md",
        "viking://resources/target.md",
        ctx=_default_ctx(),
        lease_ref=outer,
    )

    assert fake.acquire_calls[0][1] == outer
    assert copied_with[0]["lease_ref"] == "operation-ref"
    assert fake.release_calls == [copied_with[0]]


@pytest.mark.asyncio
async def test_directory_mv_locks_stable_source_and_destination_parents(monkeypatch):
    """Directory move keeps both stable parents locked through copy and cleanup."""
    fake = _AsyncMoveAGFS(source_is_dir=True)
    fs = VikingFS(agfs=_FakeAGFS())
    fs._async_agfs = fake  # type: ignore[assignment]

    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, **_kwargs: (
            "/local/default/temp/source"
            if uri == "viking://temp/source"
            else "/local/default/resources/target"
        ),
    )
    monkeypatch.setattr(fs, "_collect_uris", AsyncMock(return_value=[]))
    monkeypatch.setattr(fs, "_delete_from_vector_store", AsyncMock())
    monkeypatch.setattr(fs, "_update_vector_store_uris", AsyncMock())
    monkeypatch.setattr(fs, "_copy_for_mv", AsyncMock())

    await fs.mv(
        "viking://temp/source",
        "viking://resources/target",
        ctx=_default_ctx(),
    )

    assert fake.acquire_calls[0][0] == [
        {"path": "/local/default/temp", "kind": "tree"},
        {"path": "/local/default/resources", "kind": "tree"},
    ]


@pytest.mark.asyncio
async def test_directory_mv_reuses_parent_tree_for_failed_copy_cleanup(monkeypatch):
    """Failed directory move cleans the target under the existing parent Tree lease."""
    fake = _AsyncMoveAGFS(source_is_dir=True)
    fs = VikingFS(agfs=_FakeAGFS())
    fs._async_agfs = fake  # type: ignore[assignment]

    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, **_kwargs: (
            "/local/default/temp/source"
            if uri == "viking://temp/source"
            else "/local/default/resources/target"
        ),
    )
    monkeypatch.setattr(fs, "_collect_uris", AsyncMock(return_value=[]))
    monkeypatch.setattr(fs, "_delete_from_vector_store", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_update_vector_store_uris",
        AsyncMock(side_effect=RuntimeError("vector update failed")),
    )
    monkeypatch.setattr(fs, "_copy_for_mv", AsyncMock())

    with pytest.raises(RuntimeError, match="vector update failed"):
        await fs.mv(
            "viking://temp/source",
            "viking://resources/target",
            ctx=_default_ctx(),
        )

    assert fake.tree_calls == []
    assert fake.rm_calls[0][2]["lease_ref"] == "operation-ref"
    assert fake.release_calls == [
        {
            "lease_ref": "operation-ref",
            "owner_id": "operation-owner",
            "ownership_ref": "operation-ownership",
            "owned": True,
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("competing_write", ["default", "direct", "description"])
async def test_default_mkdir_preserves_concurrent_abstract(monkeypatch, competing_write):
    """Defaults are created once and cannot overwrite a concurrent explicit write."""
    import asyncio

    from openviking.service.fs_service import FSService

    uri = "viking://resources/shared"
    abstract_uri = uri + "/.abstract.md"
    lock = asyncio.Lock()
    storage = {}
    writes = []

    class ConcurrentAGFS:
        async def pathlock_acquire_exact(self, path):
            await lock.acquire()
            return {"lease_ref": "held"}

        async def pathlock_release(self, lease):
            lock.release()

        async def write(self, path, data, fs_ctx=None, auto_pathlock=True):
            owned = not (fs_ctx and fs_ctx.get("lease_ref") == "held")
            if owned:
                await lock.acquire()
            try:
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                storage[path] = data
                writes.append(data)
            finally:
                if owned:
                    lock.release()

    fs = VikingFS(agfs=_FakeAGFS())
    fs._async_agfs = ConcurrentAGFS()
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(fs, "_ensure_parent_dirs", AsyncMock())
    monkeypatch.setattr(fs, "_uri_to_path", lambda value, **kw: value)
    monkeypatch.setattr(fs, "mkdir", AsyncMock())

    async def exists(value, ctx=None):
        present = value in storage
        await asyncio.sleep(0)
        return present

    monkeypatch.setattr(fs, "exists", exists)
    vectorize = AsyncMock()
    monkeypatch.setattr("openviking.service.fs_service.vectorize_directory_meta", vectorize)
    service = FSService(viking_fs=fs)
    ctx = _default_ctx()
    if competing_write == "direct":
        other = fs.write_file(abstract_uri, "custom", ctx=ctx)
    else:
        other = service.mkdir(
            uri, ctx, description="custom" if competing_write == "description" else None
        )
    await asyncio.gather(other, service.mkdir(uri, ctx))
    await service.mkdir(uri, ctx)  # A later default also preserves the stored abstract.

    assert len(writes) == 1
    assert vectorize.await_count == (0 if competing_write == "direct" else 1)
    if competing_write != "default":
        assert b"custom" in storage[abstract_uri]
    assert not lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exists", "write_file"])
async def test_create_if_absent_releases_lease_on_failure(monkeypatch, failure):
    fs = VikingFS(agfs=_FakeAGFS())
    backend = _AsyncAppendAGFS()
    fs._async_agfs = backend
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(fs, "_ensure_parent_dirs", AsyncMock())
    monkeypatch.setattr(fs, "exists", AsyncMock(return_value=False))
    monkeypatch.setattr(fs, "write_file", AsyncMock())
    monkeypatch.setattr(fs, failure, AsyncMock(side_effect=OSError("backend unavailable")))

    with pytest.raises(OSError, match="backend unavailable"):
        await fs.write_file_if_absent("viking://resources/note.md", "default", _default_ctx())

    assert backend.events[-1] == ("release", {"lease_ref": "lease-1"})
    if failure == "exists":
        fs.write_file.assert_not_awaited()
