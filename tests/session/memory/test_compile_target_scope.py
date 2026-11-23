# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile may read outside --to, but explicit writes must stay inside it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.service.memory_compile import MemoryCompileRunner
from openviking.session.memory.consolidation_context_provider import (
    ConsolidationExtractContextProvider,
)
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from tests.session.memory.test_consolidation_compile import _ctx
from tests.session.memory.test_consolidation_compile import language_config as _language_config

language_config = _language_config
ROOT = "viking://user/u1/memories"


def _file(uri, memory_type="entities", **kwargs):
    return MemoryFile(uri=uri, memory_type=memory_type, content="original", **kwargs)


def _upsert(uri, old_uri=None):
    return ResolvedOperation(
        old_memory_file_content=_file(old_uri) if old_uri else None,
        memory_fields={"name": "new", "content": "updated"},
        memory_type="entities",
        uris=[uri],
    )


@pytest.fixture
def compile_scope(monkeypatch, language_config):
    language_config.output_language_override = "en"
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: SimpleNamespace(model="test"))
    fs = SimpleNamespace(
        glob=AsyncMock(return_value={"matches": []}),
        read_file=AsyncMock(),
        write_file=AsyncMock(),
        rm=AsyncMock(),
    )
    apply = AsyncMock(return_value=MemoryUpdateResult())
    acquire = AsyncMock(return_value=None)
    monkeypatch.setattr("openviking.service.memory_compile.MemoryUpdater.apply_operations", apply)
    monkeypatch.setattr("openviking.service.memory_compile.acquire_memory_operation_lease", acquire)
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: fs))
    return runner, fs, apply, acquire


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,relative_uri",
    [
        ("delete", "entities/2026/11/outside.md"),
        ("existing_replacement", "entities/2026/11/outside.md"),
        ("new_replacement", "entities/2026/11/outside.md"),
        ("replacement_source", "entities/2026/11/outside.md"),
        ("upsert", "entities/2026/11/outside.md"),
        ("update", "entities/2026/11/outside.md"),
        ("rename_source", "entities/2026/11/outside.md"),
        ("rename_target", "entities/2026/11/outside.md"),
        ("link_source", "entities/2026/11/outside.md"),
        ("delete", "entities/2026/100/outside.md"),
        ("upsert", "entities/2026/10/../11/outside.md"),
        ("upsert", "entities/2026/10/%2e%2e/11/outside.md"),
    ],
)
async def test_compile_rejects_whole_batch_before_any_apply(
    monkeypatch, compile_scope, kind, relative_uri
):
    runner, fs, apply, acquire = compile_scope
    target = f"{ROOT}/entities/2026/10"
    inside = f"{target}/source.md"
    outside = f"{ROOT}/{relative_uri}"
    # An otherwise valid write must not survive an invalid operation in the batch.
    operations = ResolvedOperations(
        upsert_operations=[_upsert(f"{target}/valid.md")],
        delete_file_contents=[],
        errors=[],
    )
    if kind == "delete":
        operations.delete_file_contents.append(_file(outside))
    elif kind in {"existing_replacement", "new_replacement"}:
        operations.delete_file_contents.append(_file(inside))
        operations.delete_replacements[inside] = outside
        if kind == "new_replacement":
            operations.upsert_operations.append(_upsert(outside))
    elif kind == "replacement_source":
        operations.delete_replacements[outside] = inside
    elif kind == "link_source":
        operations.resolved_links.append(StoredLink(from_uri=outside, to_uri=inside))
    elif kind == "rename_source":
        operations.upsert_operations.append(_upsert(inside, outside))
    else:
        old_uri = inside if kind == "rename_target" else outside if kind == "update" else None
        operations.upsert_operations.append(_upsert(outside, old_uri))
    monkeypatch.setattr(
        "openviking.service.memory_compile.ExtractLoop.run",
        AsyncMock(return_value=(operations, [])),
    )

    with pytest.raises(InvalidArgumentError) as error:
        await runner._consolidate(
            target=target, memory_type="entities", peer_id=None, instruction=None, ctx=_ctx()
        )

    assert outside in str(error.value)
    assert target in str(error.value)
    acquire.assert_not_awaited()
    apply.assert_not_awaited()
    fs.write_file.assert_not_awaited()
    fs.rm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/entities", "/entities/2026/10/"])
async def test_compile_allows_in_scope_endpoints_and_external_link_destination(
    monkeypatch, compile_scope, suffix
):
    runner, _fs, apply, acquire = compile_scope
    target = ROOT + suffix
    directory = target.rstrip("/") + ("/entities" if not suffix else "")
    source, replacement = f"{directory}/source.md", f"{directory}/new.md"
    operations = ResolvedOperations(
        upsert_operations=[_upsert(replacement, source)],
        delete_file_contents=[_file(f"{directory}/duplicate.md")],
        delete_replacements={f"{directory}/duplicate.md": replacement},
        resolved_links=[StoredLink(from_uri=replacement, to_uri=f"{ROOT}/events/reference.md")],
        errors=[],
    )
    monkeypatch.setattr(
        "openviking.service.memory_compile.ExtractLoop.run",
        AsyncMock(return_value=(operations, [])),
    )
    monkeypatch.setattr(
        "openviking.service.memory_compile._discover_memory_types",
        AsyncMock(return_value=["entities"]),
    )

    result = await runner._consolidate(
        target=target,
        memory_type="entities" if suffix else None,
        peer_id=None,
        instruction=None,
        ctx=_ctx(),
    )

    assert result["errors"] == []
    acquire.assert_awaited_once_with(operations, runner._fs._ensure_initialized(), _ctx())
    assert apply.await_args.args[0] is operations


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_outside", [False, True])
async def test_real_loop_can_read_other_month_but_cannot_delete_it(
    monkeypatch, language_config, delete_outside
):
    language_config.output_language_override = "en"
    target = f"{ROOT}/events/2026/10"
    inside, outside = f"{target}/event.md", f"{ROOT}/events/2026/11/event.md"
    files = {uri: MemoryFileUtils.write(_file(uri, "events")) for uri in (inside, outside)}
    fs = SimpleNamespace(
        glob=AsyncMock(return_value={"matches": [{"uri": inside, "isDir": False}], "count": 1}),
        read_file=AsyncMock(side_effect=lambda uri, **kwargs: files[uri]),
    )
    original_prefetch = ConsolidationExtractContextProvider.prefetch

    async def prefetch(provider):
        messages = await original_prefetch(provider)
        for uri in (inside, outside):
            assert await provider.read_file(uri) is not None
        return messages

    monkeypatch.setattr(ConsolidationExtractContextProvider, "prefetch", prefetch)
    vlm = SimpleNamespace(
        model="test",
        get_completion_async=AsyncMock(
            return_value=f"events_{2 if delete_outside else 1}.delete()\nsdk.commit()"
        ),
    )
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: vlm)
    apply = AsyncMock(return_value=MemoryUpdateResult())
    acquire = AsyncMock(return_value=None)
    monkeypatch.setattr("openviking.service.memory_compile.MemoryUpdater.apply_operations", apply)
    monkeypatch.setattr("openviking.service.memory_compile.acquire_memory_operation_lease", acquire)
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: fs))
    args = {
        "target": target,
        "memory_type": "events",
        "peer_id": None,
        "instruction": None,
        "ctx": _ctx(),
    }

    if delete_outside:
        with pytest.raises(InvalidArgumentError, match="outside --to"):
            await runner._consolidate(**args)
        acquire.assert_not_awaited()
        apply.assert_not_awaited()
    else:
        await runner._consolidate(**args)
        assert [file.uri for file in apply.await_args.args[0].delete_file_contents] == [inside]
    fs.read_file.assert_any_await(outside, ctx=_ctx())
    assert fs.glob.await_args.kwargs["uri"] == target
    vlm.get_completion_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_compile_real_updater_keeps_automatic_external_relations(
    monkeypatch, language_config
):
    language_config.output_language_override = "en"
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: SimpleNamespace(model="test"))
    directory = f"{ROOT}/entities"
    source, replacement, external = (
        f"{directory}/source.md",
        f"{directory}/new.md",
        f"{ROOT}/events/reference.md",
    )
    old_link = StoredLink(from_uri=external, to_uri=source).model_dump()
    source_file = _file(source, backlinks=[old_link], extra_fields={"name": "source"})
    files = {
        source: MemoryFileUtils.write(source_file),
        external: MemoryFileUtils.write(_file(external, "events", links=[old_link])),
    }

    async def read_file(uri, **kwargs):
        if uri not in files:
            raise NotFoundError(uri)
        return files[uri]

    async def write_file(uri, content, **kwargs):
        files[uri] = content

    async def rm(uri, **kwargs):
        del files[uri]

    async def stat(uri, **kwargs):
        if uri not in files:
            raise NotFoundError(uri)
        return {"isDir": False}

    fs = SimpleNamespace(
        glob=AsyncMock(return_value={"matches": []}),
        read_file=AsyncMock(side_effect=read_file),
        write_file=AsyncMock(side_effect=write_file),
        rm=AsyncMock(side_effect=rm),
        stat=AsyncMock(side_effect=stat),
    )
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(
        MemoryTypeSchema(
            memory_type="entities",
            directory="viking://user/{{ user_space }}/memories/entities",
            filename_template="{{ name }}.md",
            content_template="{{ content }}",
            fields=[
                MemoryField(name=name, field_type=FieldType.STRING, merge_op=MergeOp.REPLACE)
                for name in ("name", "content")
            ],
        )
    )
    operations = ResolvedOperations(
        upsert_operations=[_upsert(replacement, source)],
        delete_file_contents=[],
        resolved_links=[StoredLink(from_uri=replacement, to_uri=external)],
        errors=[],
    )
    operations.upsert_operations[0].old_memory_file_content = source_file
    monkeypatch.setattr("openviking.service.memory_compile.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "openviking.service.memory_compile.ExtractLoop.run",
        AsyncMock(return_value=(operations, [])),
    )
    monkeypatch.setattr(
        "openviking.service.memory_compile.acquire_memory_operation_lease",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("openviking.session.memory.memory_updater.get_viking_fs", lambda: fs)
    for method in ("generate_overview", "_vectorize_memories", "_sync_resource_refs_for_result"):
        monkeypatch.setattr(
            f"openviking.session.memory.memory_updater.MemoryUpdater.{method}", AsyncMock()
        )
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: fs))

    result = await runner._consolidate(
        target=directory, memory_type="entities", peer_id=None, instruction=None, ctx=_ctx()
    )

    assert result["errors"] == []
    assert source not in files and replacement in files
    assert external in result["updates"]
    reference = MemoryFileUtils.read(files[external], uri=external)
    assert reference.links[0]["to_uri"] == replacement
    assert reference.backlinks[0]["from_uri"] == replacement
