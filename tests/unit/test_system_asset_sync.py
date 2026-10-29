import base64

import pytest

from openviking.core.namespace_policy import (
    SYSTEM_EXAMPLE_SKILLS_ROOT,
    SYSTEM_FIXED_DIRECTORIES,
    SYSTEM_MEMORY_BUILTIN_ROOT,
    SYSTEM_MEMORY_CUSTOM_ROOT,
)
from openviking.server.identity import RequestContext, Role
from openviking.system_assets.sync import SystemAssetSynchronizer
from openviking_cli.session.user_id import UserIdentifier
from scripts.build_support.system_assets_manifest import write_system_assets_manifest


class _FakeVikingFS:
    def __init__(self, files=None):
        self.directories = set()
        self.files = dict(files or {})
        self.mkdir_contexts = []

    async def mkdir(self, uri, exist_ok, ctx):
        assert exist_ok is True
        self.directories.add(uri)
        self.mkdir_contexts.append(ctx)

    async def read_file_bytes(self, uri, ctx):
        assert ctx.role == Role.ROOT
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]


class _FakeCoordinator:
    def __init__(self, viking_fs):
        self._viking_fs = viking_fs
        self.calls = []

    async def batch_write(self, *, root_uri, operations, ctx):
        assert ctx.role == Role.ROOT
        self.calls.append((root_uri, operations, ctx))
        for operation in operations:
            self._viking_fs.files[operation["uri"]] = base64.b64decode(
                operation["content_base64"]
            )


@pytest.mark.asyncio
async def test_system_asset_sync_converges_managed_roots_without_touching_custom(
    tmp_path, monkeypatch
):
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
    viking_fs = _FakeVikingFS(
        {
            compile_target: b"same skill",
            memory_target: b"old profile",
            stale_target: b"keep",
            custom_target: b"custom",
        }
    )
    coordinator = _FakeCoordinator(viking_fs)
    monkeypatch.setattr(
        "openviking.system_assets.sync.ContentWriteCoordinator",
        lambda **kwargs: coordinator,
    )
    synchronizer = SystemAssetSynchronizer(
        viking_fs=viking_fs,
        assets_dir=tmp_path,
    )
    ctx = RequestContext(
        user=UserIdentifier("acme", "admin"),
        role=Role.ADMIN,
        actor_peer_id="peer",
    )

    assert await synchronizer.sync(ctx) == 2
    assert viking_fs.directories == set(SYSTEM_FIXED_DIRECTORIES)
    assert all(
        item.role == Role.ROOT and item.actor_peer_id is None
        for item in viking_fs.mkdir_contexts
    )
    assert [call[0] for call in coordinator.calls] == [
        SYSTEM_EXAMPLE_SKILLS_ROOT,
        SYSTEM_MEMORY_BUILTIN_ROOT,
    ]
    assert viking_fs.files[reference_target] == b"\x00guide"
    assert viking_fs.files[memory_target] == b"new profile"
    assert viking_fs.files[stale_target] == b"keep"
    assert viking_fs.files[custom_target] == b"custom"

    coordinator.calls.clear()
    assert await synchronizer.sync(ctx) == 0
    assert coordinator.calls == []

    previous_digest = synchronizer.manifest_digest
    compile_source.write_bytes(b"updated skill")
    write_system_assets_manifest(tmp_path)
    updated = SystemAssetSynchronizer(viking_fs=viking_fs, assets_dir=tmp_path)

    assert updated.manifest_digest != previous_digest
    assert await updated.sync(ctx) == 1
    assert viking_fs.files[compile_target] == b"updated skill"
    assert viking_fs.files[custom_target] == b"custom"


@pytest.mark.asyncio
async def test_system_asset_sync_rejects_missing_packaged_root(tmp_path, monkeypatch):
    (tmp_path / "compile/example-skills").mkdir(parents=True)
    viking_fs = _FakeVikingFS()
    monkeypatch.setattr(
        "openviking.system_assets.sync.ContentWriteCoordinator",
        lambda **kwargs: _FakeCoordinator(viking_fs),
    )
    synchronizer = SystemAssetSynchronizer(
        viking_fs=viking_fs,
        assets_dir=tmp_path,
    )

    with pytest.raises(FileNotFoundError, match="memory/memory_templates/builtin"):
        await synchronizer.sync(
            RequestContext(
                user=UserIdentifier("acme", "admin"),
                role=Role.ADMIN,
            )
        )
