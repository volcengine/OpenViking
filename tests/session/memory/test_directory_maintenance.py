# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory import directory_maintenance, memory_updater
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.directory_maintenance import (
    DirectoryMergePlan,
    merge_memory_directories,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.viking_fs import VikingFS
from openviking.system_assets import builtin_memory_templates_dir
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig

ROOT = "viking://user/alice/memories"


@pytest.fixture
def directory_merge_fs(tmp_path, monkeypatch):
    ctx = RequestContext(user=UserIdentifier("merge-test", "alice"), role=Role.USER)
    ctx.bind_frozen_files([])
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.load_from_directory(builtin_memory_templates_dir())
    agfs = create_agfs_client(
        RagfsBindingConfig(
            agfs=AGFSConfig(
                backend="local", path=str(tmp_path), pathlock={"provider": "filesystem"}
            )
        )
    )
    fs = VikingFS(agfs=agfs)
    monkeypatch.setattr(directory_maintenance, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(memory_updater, "get_viking_fs", lambda: fs)
    return fs, ctx, registry


async def write_card(fs, ctx, uri, memory_type, fields, content):
    card = MemoryFile(uri=uri, memory_type=memory_type, content=content, extra_fields=fields)
    await fs.write_file(uri, MemoryFileUtils.write(card), ctx=ctx)


@pytest.mark.asyncio
async def test_merges_noncolliding_cards_and_preserves_unknown_files(directory_merge_fs):
    fs, ctx, registry = directory_merge_fs
    src = f"{ROOT}/entities/fault_record"
    dst = f"{ROOT}/entities/故障记录"
    for uri in (src, dst):
        await fs.mkdir(uri, exist_ok=True, ctx=ctx)
    for uri, body in ((src, "Source collision"), (dst, "Target collision")):
        await write_card(
            fs,
            ctx,
            f"{uri}/same.md",
            "entities",
            {"category": uri.rsplit("/", 1)[1], "name": "Same"},
            body,
        )
    await write_card(
        fs,
        ctx,
        f"{src}/other.md",
        "entities",
        {"category": "fault_record", "name": "Other"},
        "Body must stay",
    )
    await fs.write_file(f"{src}/.unknown", "Keep", ctx=ctx)

    plan = DirectoryMergePlan("entities", src, dst)
    report = await merge_memory_directories(plan, registry=registry, vikingdb=None, ctx=ctx)
    assert report.status == "partial"
    assert report.conflicts == [f"{src}/same.md"]
    assert report.remaining == [f"{src}/.unknown", f"{src}/same.md"]
    assert not report.pruned
    assert (await fs.read_file(f"{src}/same.md", ctx=ctx)).startswith("Source collision")
    assert (await fs.read_file(f"{dst}/same.md", ctx=ctx)).startswith("Target collision")
    moved = MemoryFileUtils.read(await fs.read_file(f"{dst}/other.md", ctx=ctx))
    assert moved.content == "Body must stay"
    assert moved.extra_fields["name"] == "Other"
    assert moved.extra_fields["category"] == "故障记录"
    assert report.operations.delete_replacements == {f"{src}/other.md": f"{dst}/other.md"}


@pytest.mark.asyncio
async def test_preferences_require_verified_owner_before_any_write(directory_merge_fs):
    fs, ctx, registry = directory_merge_fs
    src = f"{ROOT}/preferences/ alice"
    dst = f"{ROOT}/preferences/alice"
    for uri in (src, dst):
        await fs.mkdir(uri, exist_ok=True, ctx=ctx)
    for topic, user in (("First", " Alice "), ("Second", "bob")):
        await write_card(
            fs,
            ctx,
            f"{src}/{topic.lower()}.md",
            "preferences",
            {"topic": topic, "user": user},
            "Preference body",
        )

    plan = DirectoryMergePlan("preferences", src, dst)
    report = await merge_memory_directories(plan, registry=registry, vikingdb=None, ctx=ctx)
    assert report.status == "failed"
    assert not report.result.written_uris
    assert not report.result.deleted_uris
    assert await fs.stat(f"{src}/first.md", ctx=ctx)
    with pytest.raises(NotFoundError):
        await fs.stat(f"{dst}/first.md", ctx=ctx)

    await fs.rm(f"{src}/second.md", ctx=ctx)
    await write_card(
        fs,
        ctx,
        f"{dst}/wrong.md",
        "preferences",
        {"topic": "Wrong", "user": "bob"},
        "Wrong-owner target",
    )
    report = await merge_memory_directories(plan, registry=registry, vikingdb=None, ctx=ctx)
    assert report.status == "failed"
    assert not report.result.written_uris
    assert not report.result.deleted_uris

    await fs.rm(f"{dst}/wrong.md", ctx=ctx)
    report = await merge_memory_directories(plan, registry=registry, vikingdb=None, ctx=ctx)
    assert report.status == "merged"
    assert report.pruned
    moved = MemoryFileUtils.read(await fs.read_file(f"{dst}/first.md", ctx=ctx))
    assert moved.content == "Preference body"
    assert moved.extra_fields["topic"] == "First"
    assert moved.extra_fields["user"] == "alice"
    retry = await merge_memory_directories(plan, registry=registry, vikingdb=None, ctx=ctx)
    assert retry.status == "source_missing"


@pytest.mark.asyncio
async def test_does_not_recreate_target_deleted_while_waiting_for_lease(
    directory_merge_fs, monkeypatch
):
    fs, ctx, registry = directory_merge_fs
    src = f"{ROOT}/entities/fault_record"
    dst = f"{ROOT}/entities/故障记录"
    for uri in (src, dst):
        await fs.mkdir(uri, exist_ok=True, ctx=ctx)
    await write_card(
        fs,
        ctx,
        f"{src}/one.md",
        "entities",
        {"category": "fault_record", "name": "One"},
        "Source body",
    )
    acquire = fs._async_agfs.pathlock_acquire_exact_tree_batch

    async def acquire_after_target_disappears(exact_paths, tree_paths, **kwargs):
        await fs.rm(dst, recursive=True, ctx=ctx)
        assert tree_paths == [fs._uri_to_path(f"{ROOT}/entities", ctx=ctx)]
        return await acquire(exact_paths, tree_paths, **kwargs)

    monkeypatch.setattr(
        fs._async_agfs, "pathlock_acquire_exact_tree_batch", acquire_after_target_disappears
    )
    report = await merge_memory_directories(
        DirectoryMergePlan("entities", src, dst), registry=registry, vikingdb=None, ctx=ctx
    )
    assert report.status == "target_missing"
    assert not report.result.written_uris
    assert await fs.stat(f"{src}/one.md", ctx=ctx)
    with pytest.raises(NotFoundError):
        await fs.stat(dst, ctx=ctx)
