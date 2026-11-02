import asyncio
from pathlib import PurePosixPath

import pytest

from openviking.core.namespace_policy import (
    SYSTEM_EXAMPLE_SKILLS_ROOT,
    SYSTEM_FIXED_DIRECTORIES,
    SYSTEM_MEMORY_BUILTIN_ROOT,
    SYSTEM_MEMORY_CUSTOM_ROOT,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.viking_fs import VikingFS
from openviking.system_assets.sync import SYSTEM_ASSET_MANIFEST_PATH, SystemAssetSynchronizer
from openviking_cli.session.user_id import UserIdentifier
from scripts.build_support.system_assets_manifest import write_system_assets_manifest


class _FakeAGFS:
    def __init__(self, files=None):
        self.directories = set()
        self.files = {
            "/local/acme/" + uri.removeprefix("viking://"): content
            for uri, content in (files or {}).items()
        }
        self.writes = []
        self.sync_locks = 0
        self.held = False

    async def pathlock_acquire_batch_async(self, ctx, requests, *args):
        assert requests == [{"path": SYSTEM_ASSET_MANIFEST_PATH, "kind": "exact"}]
        assert not self.held
        self.held = True
        self.sync_locks += 1
        return {"lease_ref": "manifest-lease"}

    def pathlock_release(self, ctx, lease):
        assert self.held
        self.held = False

    def ensure_parent_dirs(self, path, ctx=None):
        self.directories.update(str(parent) for parent in PurePosixPath(path).parents)

    def mkdir(self, path, ctx=None):
        self.directories.add(path)

    def read(self, path, ctx=None):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def stat(self, path, ctx=None):
        return {"size": len(self.read(path)), "isDir": False}

    def write(self, path, content, ctx=None):
        assert self.held or ctx.get("lease_ref") == "account-init"
        assert ctx.get("disable_auto_pathlock") == "true" or ctx.get("lease_ref") == "account-init"
        self.files[path] = content
        self.writes.append(path)


@pytest.mark.asyncio
async def test_system_asset_sync_converges_managed_roots_without_touching_custom(tmp_path):
    compile_source = tmp_path / "compile/example-skills/demo/SKILL.md"
    reference_source = tmp_path / "compile/example-skills/demo/references/guide.bin"
    memory_source = tmp_path / "memory/memory_templates/builtin/profile.yaml"
    compile_source.parent.mkdir(parents=True)
    reference_source.parent.mkdir(parents=True)
    memory_source.parent.mkdir(parents=True)
    compile_source.write_bytes(b"same skill")
    reference_source.write_bytes(b"\x00guide")
    memory_source.write_bytes(b"new profile")
    write_system_assets_manifest(tmp_path)

    compile_target = f"{SYSTEM_EXAMPLE_SKILLS_ROOT}/demo/SKILL.md"
    reference_target = f"{SYSTEM_EXAMPLE_SKILLS_ROOT}/demo/references/guide.bin"
    memory_target = f"{SYSTEM_MEMORY_BUILTIN_ROOT}/profile.yaml"
    stale_target = f"{SYSTEM_EXAMPLE_SKILLS_ROOT}/retired/SKILL.md"
    custom_target = f"{SYSTEM_MEMORY_CUSTOM_ROOT}/profile.yaml"
    agfs = _FakeAGFS(
        {
            compile_target: b"same skill",
            memory_target: b"old profile",
            stale_target: b"keep",
            custom_target: b"custom",
        }
    )
    viking_fs = VikingFS(agfs=agfs)
    synchronizer = SystemAssetSynchronizer(viking_fs=viking_fs, assets_dir=tmp_path)
    ctx = RequestContext(
        user=UserIdentifier("acme", "admin"), role=Role(Role.ADMIN), actor_peer_id="peer"
    )
    paths = {
        uri: viking_fs._uri_to_path(uri, ctx=ctx)
        for uri in (
            compile_target,
            reference_target,
            memory_target,
            stale_target,
            custom_target,
        )
    }

    assert await synchronizer.sync(ctx, lease_ref={"lease_ref": "account-init"}) == 2
    assert agfs.sync_locks == 0
    assert all(
        viking_fs._uri_to_path(uri, ctx=ctx) in agfs.directories for uri in SYSTEM_FIXED_DIRECTORIES
    )
    assert sorted(agfs.writes) == sorted([paths[reference_target], paths[memory_target]])
    assert agfs.files[paths[reference_target]] == b"\x00guide"
    assert agfs.files[paths[memory_target]] == b"new profile"
    assert agfs.files[paths[stale_target]] == b"keep"
    assert agfs.files[paths[custom_target]] == b"custom"

    agfs.writes.clear()
    assert await synchronizer.sync(ctx) == 0
    assert agfs.writes == []
    assert agfs.sync_locks == 1
    assert not agfs.held

    previous_digest = synchronizer.manifest_digest
    compile_source.write_bytes(b"updated skill")
    write_system_assets_manifest(tmp_path)
    updated = SystemAssetSynchronizer(viking_fs=viking_fs, assets_dir=tmp_path)
    assert updated.manifest_digest != previous_digest
    assert await updated.sync(ctx) == 1
    assert agfs.files[paths[compile_target]] == b"updated skill"
    assert agfs.files[paths[custom_target]] == b"custom"
    assert not agfs.held


@pytest.fixture
def concurrent_asset_sync(tmp_path):
    source = tmp_path / "compile/example-skills/demo"
    source.mkdir(parents=True)
    (tmp_path / "memory/memory_templates/builtin").mkdir(parents=True)
    for index in range(9):
        (source / f"{index}.md").write_bytes(b"packaged")
    agfs = _FakeAGFS({f"{SYSTEM_EXAMPLE_SKILLS_ROOT}/demo/0.md": b"packaged"})
    viking_fs = VikingFS(agfs=agfs)
    synchronizer = SystemAssetSynchronizer(viking_fs=viking_fs, assets_dir=tmp_path)
    ctx = RequestContext(user=UserIdentifier("acme", "admin"), role=Role(Role.ADMIN))
    return agfs, viking_fs, synchronizer, ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["check", "write"])
async def test_system_asset_sync_bounds_each_io_stage(concurrent_asset_sync, monkeypatch, stage):
    agfs, viking_fs, synchronizer, ctx = concurrent_asset_sync
    target, method = (
        (viking_fs, "read_file_bytes") if stage == "check" else (viking_fs._async_agfs, "write")
    )
    original = getattr(target, method)
    started, release = asyncio.Event(), asyncio.Event()
    active = peak = 0

    async def gated(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 4:
            started.set()
        try:
            await release.wait()
            return await original(*args, **kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(target, method, gated)
    task = asyncio.create_task(synchronizer.sync(ctx))
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert agfs.held
        assert not agfs.writes
        release.set()
        assert await asyncio.wait_for(task, 5) == 8
        assert peak == 4
        assert len(agfs.writes) == 8
        assert not agfs.held
        assert await synchronizer.sync(ctx) == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["check", "write"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_system_asset_sync_settles_io_before_unlock(
    concurrent_asset_sync, monkeypatch, stage, cancel
):
    agfs, viking_fs, synchronizer, ctx = concurrent_asset_sync
    target, method = (
        (viking_fs, "read_file_bytes") if stage == "check" else (viking_fs._async_agfs, "write")
    )
    original = getattr(target, method)
    started, release, fail = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = finished = 0

    async def gated(*args, **kwargs):
        nonlocal calls
        calls += 1
        index = calls
        if calls == 4:
            started.set()

        async def io():
            nonlocal finished
            try:
                if index == 1 and not cancel:
                    await fail.wait()
                    raise OSError("asset I/O failed")
                await release.wait()
                assert agfs.held
                return await original(*args, **kwargs)
            finally:
                finished += 1

        # Match AGFS's blocking-I/O lifetime: cancellation cannot stop an
        # operation already running in a worker thread.
        return await run_to_completion(io)

    monkeypatch.setattr(target, method, gated)
    task = asyncio.create_task(synchronizer.sync(ctx))
    try:
        await asyncio.wait_for(started.wait(), 5)
        if cancel:
            task.cancel()
        else:
            fail.set()
        done, _ = await asyncio.wait({task}, timeout=0.02)
        assert not done
        assert agfs.held
        release.set()
        with pytest.raises(asyncio.CancelledError if cancel else OSError):
            await asyncio.wait_for(task, 5)
        assert calls == finished == 4
        assert not agfs.held
        if stage == "check":
            assert not agfs.writes
    finally:
        release.set()
        fail.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_system_asset_sync_rejects_missing_packaged_root(tmp_path):
    (tmp_path / "compile/example-skills").mkdir(parents=True)
    agfs = _FakeAGFS()
    synchronizer = SystemAssetSynchronizer(viking_fs=VikingFS(agfs=agfs), assets_dir=tmp_path)
    with pytest.raises(FileNotFoundError, match="memory/memory_templates/builtin"):
        await synchronizer.sync(
            RequestContext(user=UserIdentifier("acme", "admin"), role=Role(Role.ADMIN))
        )
    assert not agfs.held
    assert agfs.writes == []
