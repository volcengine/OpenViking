# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for `ov compile --skill memory` in-place memory consolidation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.compile_service import MEMORY_COMPILE_SKILL, CompileRequest
from openviking.service.memory_compile import (
    MemoryCompileRunner,
    _discover_memory_types,
    _memory_type_from_target,
    _peer_id_from_memory_uri,
)
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.service.task_work_index import get_task_context
from openviking.session.memory.consolidation_context_provider import (
    ConsolidationExtractContextProvider,
    build_consolidation_isolation_handler,
)
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry, get_default_registry
from openviking.session.memory.memory_updater import (
    ExtractContext,
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier


def _ctx(user_id: str = "u1") -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acc", user_id=user_id),
        role=Role.USER,
    )


# ── CompileRequest memory-mode validation ──


def test_compile_request_memory_mode_needs_no_from():
    request = CompileRequest(**{"to": "viking://user/u1/memories/entities", "skill": "memory"})
    assert request.is_memory_mode is True
    assert request.skill == MEMORY_COMPILE_SKILL
    assert request.from_ == []


def test_compile_request_memory_mode_rejects_from():
    with pytest.raises(ValueError):
        CompileRequest(
            **{
                "to": "viking://user/u1/memories/entities",
                "skill": "memory",
                "from": ["viking://resources/x"],
            }
        )


def test_compile_request_normal_mode_requires_from():
    with pytest.raises(ValueError):
        CompileRequest(**{"to": "viking://resources/wiki", "skill": "viking://agent/skills/wiki"})

    request = CompileRequest(
        **{
            "to": "viking://resources/wiki",
            "skill": "viking://agent/skills/wiki",
            "from": ["viking://resources/y"],
        }
    )
    assert request.is_memory_mode is False
    assert request.from_ == ["viking://resources/y"]


# ── Target parsing helpers ──


def test_memory_type_from_target_reads_type_segment():
    assert _memory_type_from_target("viking://user/u1/memories/entities") == "entities"
    assert (
        _memory_type_from_target("viking://user/u1/peers/agent_x/memories/experiences")
        == "experiences"
    )


def test_memory_type_from_target_accepts_memory_roots():
    assert _memory_type_from_target("viking://user/u1/memories") is None
    assert _memory_type_from_target("viking://user/u1/peers/agent_x/memories") is None


def test_memory_type_from_target_rejects_non_memory_and_user_roots():
    with pytest.raises(InvalidArgumentError):
        _memory_type_from_target("viking://resources/wiki")
    with pytest.raises(InvalidArgumentError):
        _memory_type_from_target("viking://user/u1")


@pytest.mark.asyncio
@pytest.mark.parametrize("peer_id", [None, "agent_x"])
async def test_discover_root_types_uses_schema_paths_and_existing_files(peer_id):
    root = f"viking://user/u1/{'peers/' + peer_id + '/' if peer_id else ''}memories"
    registry = MemoryTypeRegistry(load_schemas=False)
    defaults = get_default_registry()
    for name in ("entities", "events", "preferences", "profile", "identity", "soul", "cases"):
        registry.register(defaults.get(name).model_copy(deep=True))
    disabled = defaults.get("entities").model_copy(
        update={"memory_type": "disabled", "enabled": False}
    )
    outside = defaults.get("entities").model_copy(
        update={"memory_type": "outside", "directory": "viking://user/u2/memories/entities"}
    )
    registry.register(disabled)
    registry.register(outside)
    paths = {
        f"{root}/entities": True,
        f"{root}/events": True,
        f"{root}/profile.md": False,
        f"{root}/identity.md": False,
        f"{root}/soul.md": False,
        f"{root}/cases": True,
        f"{root}/unknown": True,
    }

    async def stat(uri, ctx, skip_count=False):
        assert ctx == _ctx()
        if uri not in paths:
            raise NotFoundError(uri)
        return {"isDir": paths[uri]}

    fs = SimpleNamespace(stat=AsyncMock(side_effect=stat))
    types = await _discover_memory_types(root, registry, fs, _ctx())

    assert types == sorted(
        ["entities", "events", "profile", "identity", "soul"] + ([] if peer_id else ["cases"])
    )
    assert all(call.args[0].startswith(root + "/") for call in fs.stat.await_args_list)
    assert f"{root}/unknown" not in [call.args[0] for call in fs.stat.await_args_list]
    if peer_id:
        assert f"{root}/cases" not in [call.args[0] for call in fs.stat.await_args_list]


@pytest.mark.asyncio
async def test_discover_root_types_propagates_storage_errors():
    fs = SimpleNamespace(stat=AsyncMock(side_effect=RuntimeError("storage unavailable")))
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await _discover_memory_types(
            "viking://user/u1/memories", get_default_registry(), fs, _ctx()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target", ["viking://user/u1/memories", "viking://user/u1/peers/agent_x/memories"]
)
async def test_root_compile_creates_one_task_and_one_loop(monkeypatch, language_config, target):
    language_config.output_language_override = "en"
    vlm = SimpleNamespace(model="test", get_completion_async=AsyncMock(return_value="sdk.commit()"))
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: vlm)
    store = SimpleNamespace(
        create=AsyncMock(),
        update=AsyncMock(),
        get=AsyncMock(return_value=None),
        list=AsyncMock(return_value=[]),
        delete=AsyncMock(),
    )
    tracker = TaskTracker(store)
    monkeypatch.setattr("openviking.service.memory_compile.get_task_tracker", lambda: tracker)
    uris = [
        f"{target}/entities/person/alice.md",
        f"{target}/preferences/alice/food.md",
        f"{target}/profile.md",
    ]
    existing = {
        target: True,
        f"{target}/entities": True,
        f"{target}/preferences": True,
        f"{target}/profile.md": False,
    }

    async def stat(uri, ctx, skip_count=False):
        if uri not in existing:
            raise NotFoundError(uri)
        return {"uri": uri, "isDir": existing[uri]}

    fs = SimpleNamespace(
        stat=AsyncMock(side_effect=stat),
        glob=AsyncMock(
            return_value={
                "matches": [{"uri": uri, "isDir": False, "size": 100} for uri in uris],
                "count": 3,
            }
        ),
    )
    service = SimpleNamespace(
        ensure_write_access=AsyncMock(), stat=fs.stat, _ensure_initialized=lambda: fs
    )
    apply = AsyncMock(return_value=MemoryUpdateResult())
    monkeypatch.setattr("openviking.service.memory_compile.MemoryUpdater.apply_operations", apply)
    acquire = AsyncMock(return_value=None)
    monkeypatch.setattr("openviking.service.memory_compile.acquire_memory_operation_lease", acquire)
    runner = MemoryCompileRunner(service)

    accepted = await runner.create(target=target, instruction="整理全部已有记忆", ctx=_ctx())
    await asyncio.gather(*list(runner._running))
    final = await tracker.get(accepted.task_id, account_id="acc", user_id="u1")

    store.create.assert_awaited_once()
    vlm.get_completion_async.assert_awaited_once()
    fs.glob.assert_awaited_once()
    assert fs.glob.await_args.kwargs["uri"] == target
    apply.assert_awaited_once()
    acquire.assert_awaited_once()
    handler = apply.await_args.kwargs["isolation_handler"]
    assert handler.allowed_memory_types == {"entities", "preferences", "profile"}
    assert handler.allowed_peer_ids == ({"agent_x"} if "/peers/" in target else set())
    assert handler.allow_self == ("/peers/" not in target)
    assert final.status == TaskStatus.COMPLETED
    assert final.meta["request"]["memory_type"] is None
    assert final.result["memory_type"] is None
    assert final.result["memory_types"] == ["entities", "preferences", "profile"]
    assert final.result["errors"] == []
    prompt = vlm.get_completion_async.await_args.kwargs["messages"][0]["content"]
    for name in ("entities", "preferences", "profile"):
        assert name in prompt
    assert "create_events" not in prompt
    assert not tracker.has_work(accepted.task_id)


@pytest.mark.asyncio
async def test_root_compile_with_no_registered_memories_is_noop(monkeypatch, language_config):
    fs = SimpleNamespace(stat=AsyncMock(side_effect=NotFoundError("missing")))
    get_vlm = Mock(side_effect=AssertionError("empty root must not call the model"))
    language_config.vlm = SimpleNamespace(get_vlm_instance=get_vlm)
    loop = AsyncMock()
    monkeypatch.setattr("openviking.service.memory_compile.ExtractLoop.run", loop)
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: fs))
    result = await runner._consolidate(
        target="viking://user/u1/memories",
        memory_type=None,
        peer_id=None,
        instruction=None,
        ctx=_ctx(),
    )
    assert result["memory_types"] == []
    assert result["errors"] == []
    assert result["adds"] == result["updates"] == result["deletes"] == []
    loop.assert_not_awaited()
    get_vlm.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("peer_id", [None, "agent_x"])
async def test_root_compile_updates_two_types_through_real_loop_and_updater(
    monkeypatch, language_config, peer_id
):
    root = f"viking://user/u1/{'peers/' + peer_id + '/' if peer_id else ''}memories"
    registry = MemoryTypeRegistry(load_schemas=False)
    for name in ("entities", "preferences"):
        registry.register(
            MemoryTypeSchema(
                memory_type=name,
                directory=f"viking://user/{{{{ user_space }}}}/memories/{name}",
                filename_template="{{ name }}.md",
                content_template="{{ content }}",
                fields=[
                    MemoryField(name="name", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
                    MemoryField(
                        name="content", field_type=FieldType.STRING, merge_op=MergeOp.PATCH
                    ),
                ],
            )
        )
    monkeypatch.setattr("openviking.service.memory_compile.get_default_registry", lambda: registry)
    language_config.output_language_override = "en"
    uris = [f"{root}/{name}/alice.md" for name in ("entities", "preferences")]
    files = {
        uri: MemoryFileUtils.write(
            MemoryFile(
                uri=uri,
                memory_type=name,
                content="old",
                extra_fields={"name": "alice", "memory_type": name},
            )
        )
        for uri, name in zip(uris, ("entities", "preferences"), strict=True)
    }

    async def stat(uri, ctx, skip_count=False):
        if uri in files:
            return {"isDir": False}
        if any(path.startswith(uri + "/") for path in files):
            return {"isDir": True}
        raise NotFoundError(uri)

    async def read_file(uri, ctx):
        if uri not in files:
            raise NotFoundError(uri)
        return files[uri]

    async def write_file(uri, content, ctx, lease_ref=None):
        files[uri] = content

    fs = SimpleNamespace(
        stat=AsyncMock(side_effect=stat),
        read_file=AsyncMock(side_effect=read_file),
        write_file=AsyncMock(side_effect=write_file),
        glob=AsyncMock(
            return_value={
                "matches": [{"uri": uri, "isDir": False, "size": 100} for uri in uris],
                "count": 2,
            }
        ),
    )
    original_prefetch = ConsolidationExtractContextProvider.prefetch

    async def prefetch(provider):
        messages = await original_prefetch(provider)
        for uri in uris:
            assert await provider.read_file(uri) is not None
        return messages

    monkeypatch.setattr(ConsolidationExtractContextProvider, "prefetch", prefetch)
    vlm = SimpleNamespace(
        model="test",
        get_completion_async=AsyncMock(
            return_value=(
                'entities_1.content.update("""Alice is a designer.""")\n'
                'preferences_1.content.update("""Alice likes tea.""")\nsdk.commit()'
            )
        ),
    )
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: vlm)
    monkeypatch.setattr("openviking.session.memory.memory_updater.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.session.memory.memory_updater.MemoryUpdater.generate_overview", AsyncMock()
    )
    monkeypatch.setattr(
        "openviking.session.memory.memory_updater.MemoryUpdater._sync_resource_refs_for_result",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.service.memory_compile.acquire_memory_operation_lease",
        AsyncMock(return_value=None),
    )
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: fs))

    result = await runner._consolidate(
        target=root, memory_type=None, peer_id=peer_id, instruction="整理这两类记忆", ctx=_ctx()
    )

    assert result["errors"] == []
    assert result["memory_types"] == ["entities", "preferences"]
    assert set(result["updates"]) == set(uris)
    assert result["adds"] == result["deletes"] == []
    assert "Alice is a designer." in files[uris[0]]
    assert "Alice likes tea." in files[uris[1]]
    assert fs.write_file.await_count == 2
    assert set(files) == set(uris)
    vlm.get_completion_async.assert_awaited_once()


def test_peer_id_from_memory_uri():
    assert _peer_id_from_memory_uri("viking://user/u1/memories/entities") is None
    assert _peer_id_from_memory_uri("viking://user/u1/peers/agent_x/memories/entities") == "agent_x"


# ── Isolation handler scope ──


def test_consolidation_isolation_self_vs_peer():
    ctx = _ctx()
    self_handler = build_consolidation_isolation_handler(
        ctx, ExtractContext([]), memory_types=["entities"], peer_id=None
    )
    assert self_handler.allow_self is True
    assert self_handler.allowed_peer_ids == set()

    peer_handler = build_consolidation_isolation_handler(
        ctx, ExtractContext([]), memory_types=["entities"], peer_id="agent_x"
    )
    assert peer_handler.allow_self is False
    assert peer_handler.allowed_peer_ids == {"agent_x"}


# ── Provider schema scope and prefetch ──


def test_provider_loads_single_schema_from_to():
    ctx = _ctx()
    provider = ConsolidationExtractContextProvider(memory_types=["entities"])
    schemas = provider.get_memory_schemas(ctx)
    assert [s.memory_type for s in schemas] == ["entities"]


def test_provider_unknown_type_raises():
    provider = ConsolidationExtractContextProvider(memory_types=["does_not_exist"])
    with pytest.raises(ValueError):
        provider.get_memory_schemas(_ctx())


@pytest.mark.asyncio
async def test_prefetch_seeds_recursive_listing_and_lets_model_explore():
    ctx = _ctx()
    directory = "viking://user/u1/memories/entities"
    # glob backs the recursive ls: return files across subdirectories.
    glob_result = {
        "matches": [
            {"uri": f"{directory}/person/alice.md", "isDir": False, "size": 100},
            {"uri": f"{directory}/media/book.md", "isDir": False, "size": 200},
            {"uri": f"{directory}/person", "isDir": True, "size": 0},
            {"uri": f"{directory}/.overview.md", "isDir": False, "size": 50},
        ],
        "count": 4,
    }
    viking_fs = SimpleNamespace(glob=AsyncMock(return_value=glob_result))
    provider = ConsolidationExtractContextProvider(
        memory_types=["entities"],
        target_directory=directory,
        ctx=ctx,
        viking_fs=viking_fs,
    )

    messages = await provider.prefetch()
    tool_context = provider.create_tool_context()
    assert tool_context.request_ctx is ctx
    assert tool_context.viking_fs is viking_fs

    # A recursive ls seed is added as a tool-call pair, then a user instruction.
    seeded = "\n".join(str(m.get("content", "")) for m in messages)
    assert "person/alice.md" in seeded
    assert "media/book.md" in seeded
    # Reserved overview files are filtered out of the listing.
    assert ".overview.md" not in seeded
    assert messages[-1]["role"] == "user"
    assert "recursive listing" in messages[-1]["content"]
    assert "consolidation operations" in messages[-1]["content"]


def test_provider_exposes_ls_search_read_tools():
    provider = ConsolidationExtractContextProvider(memory_types=["entities"])
    assert provider.get_tools() == ["ls", "search", "read"]


def test_instruction_mentions_type_and_explore_tools():
    provider = ConsolidationExtractContextProvider(
        memory_types=["entities"], instruction="Only touch pets."
    )
    text = provider.instruction()
    assert "entities" in text
    assert "recursive=true" in text
    assert "read" in text
    assert "no write tool" in text
    assert "Only touch pets." in text


@pytest.fixture
def language_config(monkeypatch):
    registry = get_default_registry()
    config = SimpleNamespace(
        output_language_override="",
        memory=SimpleNamespace(
            eager_prefetch=False,
            prefetch_search_topn=5,
            link_enabled=False,
            maintenance_review_tokens=2000,
        ),
        vlm=SimpleNamespace(),
        registry=registry,
    )
    for module in (
        "openviking.service.memory_compile",
        "openviking.session.memory.consolidation_context_provider",
        "openviking.session.memory.session_extract_context_provider",
        "openviking.session.memory.extract_loop",
        "openviking.session.memory.utils.language",
        "openviking_cli.utils.config",
    ):
        monkeypatch.setattr(f"{module}.get_openviking_config", lambda: config)
    monkeypatch.setattr("openviking.service.memory_compile.get_default_registry", lambda: registry)

    async def _passthrough_registry(_fs, _account, base):
        return base

    monkeypatch.setattr(
        "openviking.session.memory.account_templates.resolve_account_memory_registry",
        _passthrough_registry,
    )
    return config


@pytest.mark.asyncio
@pytest.mark.parametrize("override, expected", [("", "zh-CN"), ("ja", "ja")])
async def test_compile_resolves_language_before_prompt_and_schema(
    monkeypatch, language_config, override, expected
):
    language_config.output_language_override = override
    vlm = SimpleNamespace(
        model="test-model", get_completion_async=AsyncMock(return_value="sdk.commit()")
    )
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: vlm)
    directory = "viking://user/u1/memories/entities"
    uri = f"{directory}/person/alice.md"
    content = "# 小丽\n小丽是小美的同事，她们经常一起吃午饭，也会一起讨论活动文案。"
    viking_fs = SimpleNamespace(
        glob=AsyncMock(return_value={"matches": [{"uri": uri, "size": 200, "isDir": False}]}),
        read=AsyncMock(return_value=content.encode()),
        read_file=AsyncMock(side_effect=AssertionError("must not prefetch full content")),
    )
    registry = language_config.registry
    schema = registry.get("entities").model_copy(deep=True)
    schema.description = "Memory schema language: {{ language }}."
    schema.fields[-1].description = "Field language: {{ language }}."
    monkeypatch.setattr(registry, "get", lambda name: schema)
    apply = AsyncMock(return_value=MemoryUpdateResult())
    monkeypatch.setattr("openviking.service.memory_compile.MemoryUpdater.apply_operations", apply)
    acquire_lease = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "openviking.service.memory_compile.acquire_memory_operation_lease",
        acquire_lease,
    )
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: viking_fs))

    result = await runner._consolidate(
        target=directory,
        memory_type="entities",
        peer_id=None,
        instruction="Merge duplicate memories without losing facts.",
        ctx=_ctx(),
    )

    prompt = vlm.get_completion_async.call_args.kwargs["messages"][0]["content"]
    assert f"All memory content MUST be written in {expected}." in prompt
    assert f"Memory schema language: {expected}." in prompt
    assert f"Field language: {expected}." in prompt
    assert "{{ language }}" not in prompt
    assert content not in str(vlm.get_completion_async.call_args.kwargs["messages"])
    assert result["errors"] == []
    apply.assert_awaited_once()
    acquire_lease.assert_awaited_once()
    if override:
        viking_fs.read.assert_not_awaited()
    else:
        viking_fs.read.assert_awaited_once_with(uri, size=4096, ctx=_ctx())
        assert content not in str(vlm.get_completion_async.call_args.kwargs["messages"])


@pytest.mark.asyncio
async def test_compile_prefers_account_registry(monkeypatch, language_config):
    language_config.output_language_override = "en"
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: SimpleNamespace(model="test"))
    directory = "viking://user/u1/memories/entities"
    deployment = language_config.registry
    account_registry = MemoryTypeRegistry(load_schemas=False)
    for schema in deployment.list_all(include_disabled=True):
        account_registry.register(schema.model_copy(deep=True))
    account_registry.get("soul").content_template = "# Company Assistant\n{{ content }}"

    captured = {}

    async def fake_resolver(fs, account_id, base_registry):
        captured["resolver_called_with"] = (fs, account_id, base_registry)
        return account_registry

    monkeypatch.setattr(
        "openviking.session.memory.account_templates.resolve_account_memory_registry",
        fake_resolver,
    )

    def capture_run(self):
        captured["provider_registry"] = self.context_provider._get_registry()
        return AsyncMock(return_value=(None, []))()

    monkeypatch.setattr("openviking.service.memory_compile.ExtractLoop.run", capture_run)
    original_updater_init = MemoryUpdater.__init__

    def capture_updater(self, *args, **kwargs):
        captured["updater_registry"] = kwargs.get("registry") or (args[0] if args else None)
        original_updater_init(self, *args, **kwargs)

    monkeypatch.setattr("openviking.service.memory_compile.MemoryUpdater.__init__", capture_updater)
    viking_fs = SimpleNamespace(
        glob=AsyncMock(return_value={"matches": []}),
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    monkeypatch.setattr(
        "openviking.service.memory_compile.acquire_memory_operation_lease",
        AsyncMock(return_value=None),
    )

    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: viking_fs))
    await runner._consolidate(
        target=directory,
        memory_type="entities",
        peer_id=None,
        instruction="check account templates",
        ctx=_ctx(),
    )

    fs_arg, account_id, base_registry = captured["resolver_called_with"]
    assert fs_arg is viking_fs
    assert account_id == _ctx().account_id
    assert base_registry is language_config.registry
    assert captured["provider_registry"] is account_registry
    assert (
        captured["provider_registry"].get("soul").content_template
        == "# Company Assistant\n{{ content }}"
    )


@pytest.mark.asyncio
async def test_compile_reports_uri_migration_as_add_and_delete(monkeypatch, language_config):
    language_config.output_language_override = "en"
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: SimpleNamespace(model="test"))
    directory = "viking://user/u1/memories/entities"
    source_uri = f"{directory}/person/old.md"
    target_uri = f"{directory}/person/new.md"
    source_file = MemoryFile(
        uri=source_uri,
        memory_type="entities",
        content="source",
        extra_fields={"category": "person", "name": "old"},
    )
    viking_fs = SimpleNamespace(
        glob=AsyncMock(return_value={"matches": []}),
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
    )
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=source_file,
                memory_fields={"category": "person", "name": "new"},
                memory_type="entities",
                uris=[target_uri],
            )
        ],
        delete_file_contents=[],
        errors=[],
    )
    monkeypatch.setattr(
        "openviking.service.memory_compile.ExtractLoop.run",
        AsyncMock(return_value=(operations, [])),
    )
    apply_result = MemoryUpdateResult()
    apply_result.add_written(target_uri)
    apply_result.add_deleted(source_uri)
    monkeypatch.setattr(
        "openviking.service.memory_compile.MemoryUpdater.apply_operations",
        AsyncMock(return_value=apply_result),
    )
    monkeypatch.setattr(
        "openviking.service.memory_compile.acquire_memory_operation_lease",
        AsyncMock(return_value=None),
    )
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: viking_fs))

    result = await runner._consolidate(
        target=directory,
        memory_type="entities",
        peer_id=None,
        instruction="Rename old to new.",
        ctx=_ctx(),
    )

    assert result["adds"] == [target_uri]
    assert result["updates"] == []
    assert result["deletes"] == [source_uri]
    assert result["total_adds"] == 1
    assert result["total_deletes"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("peer_id", [None, "agent_x"])
async def test_provider_binds_isolation_before_prefetch(peer_id):
    ctx = _ctx()
    viking_fs = SimpleNamespace(glob=AsyncMock(return_value={"matches": [], "count": 0}))
    provider = ConsolidationExtractContextProvider(
        memory_types=["entities"],
        memory_registry=get_default_registry(),
        ctx=ctx,
        viking_fs=viking_fs,
    )
    extract_context = provider.get_extract_context()
    handler = build_consolidation_isolation_handler(
        ctx, extract_context, memory_types=["entities"], peer_id=peer_id
    )
    handler.prepare_messages()
    provider.bind_isolation_handler(handler)

    await provider.prefetch()

    space = f"peers/{peer_id}/" if peer_id else ""
    viking_fs.glob.assert_awaited_once()
    assert viking_fs.glob.await_args.kwargs["uri"] == f"viking://user/u1/{space}memories/entities"
    assert viking_fs.glob.await_args.kwargs["ctx"] is ctx
    assert provider.get_extract_context() is extract_context
    assert provider.create_tool_context().page_id_map is extract_context.page_id_map


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change, has_error, has_operations, expected_status",
    [
        (None, True, True, TaskStatus.FAILED),
        ("written", True, True, TaskStatus.COMPLETED),
        ("edited", True, True, TaskStatus.COMPLETED),
        ("deleted", True, True, TaskStatus.COMPLETED),
        ("written", False, True, TaskStatus.COMPLETED),
        (None, False, True, TaskStatus.COMPLETED),
        (None, False, False, TaskStatus.COMPLETED),
    ],
    ids=[
        "all-failed",
        "partial-add",
        "partial-edit",
        "partial-delete",
        "success",
        "no-op",
        "no-operations",
    ],
)
async def test_compile_task_apply_outcomes(
    monkeypatch, language_config, change, has_error, has_operations, expected_status
):
    language_config.output_language_override = "en"
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: SimpleNamespace(model="test"))
    ctx = _ctx()
    store = SimpleNamespace(
        create=AsyncMock(),
        update=AsyncMock(),
        get=AsyncMock(return_value=None),
        list=AsyncMock(return_value=[]),
        delete=AsyncMock(),
    )
    tracker = TaskTracker(store)
    task = await tracker.create("compile", account_id=ctx.account_id, user_id=ctx.user.user_id)
    monkeypatch.setattr("openviking.service.memory_compile.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.service.memory_compile.tracer.get_trace_id", lambda: "test-trace"
    )
    warning = Mock()
    monkeypatch.setattr("openviking.service.memory_compile.logger.warning", warning)
    directory = "viking://user/u1/memories/entities"
    uri = f"{directory}/person/alice.md"
    operations = ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[])
    monkeypatch.setattr(
        "openviking.service.memory_compile.ExtractLoop.run",
        AsyncMock(return_value=(operations if has_operations else None, [])),
    )
    lease = {"lease_ref": "test-lease"}
    viking_fs = SimpleNamespace(_async_agfs=SimpleNamespace(pathlock_release=AsyncMock()))
    monkeypatch.setattr(
        "openviking.service.memory_compile.acquire_memory_operation_lease",
        AsyncMock(return_value=lease),
    )
    apply_result = MemoryUpdateResult()
    if change:
        getattr(apply_result, f"add_{change}")(uri)
    if has_error:
        apply_result.add_error(f"{directory}/person/bob.md", RuntimeError("write failed"))
    apply = AsyncMock(return_value=apply_result)
    monkeypatch.setattr("openviking.service.memory_compile.MemoryUpdater.apply_operations", apply)
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: viking_fs))

    await runner._run(
        task_id=task.task_id,
        target=directory,
        memory_type="entities",
        peer_id=None,
        instruction=None,
        ctx=ctx,
    )

    final = await tracker.get(task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
    assert final.status == expected_status
    assert final.stage == expected_status.value
    assert final.result == {
        "to": directory,
        "skill": "memory",
        "trace_id": "test-trace",
        "memory_type": "entities",
        "memory_types": ["entities"],
        "adds": [uri] if change == "written" else [],
        "updates": [uri] if change == "edited" else [],
        "deletes": [uri] if change == "deleted" else [],
        "total_adds": int(change == "written"),
        "total_updates": int(change == "edited"),
        "total_deletes": int(change == "deleted"),
        "errors": ["write failed"] if has_error else [],
    }
    if expected_status == TaskStatus.FAILED:
        assert final.error.startswith("MEMORY_CONSOLIDATION_FAILED:")
    else:
        assert final.error is None
    assert not tracker.has_work(task.task_id)
    if has_operations:
        apply.assert_awaited_once()
        viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lease)
    else:
        apply.assert_not_awaited()
        viking_fs._async_agfs.pathlock_release.assert_not_awaited()
    if change and has_error:
        warning.assert_called_once_with(
            "Memory-mode compile %s partially succeeded: %s", task.task_id, ["write failed"]
        )
    else:
        warning.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_type", [None, "entities"])
async def test_cancel_running_compile_interrupts_before_memory_write(monkeypatch, memory_type):
    store = SimpleNamespace(
        create=AsyncMock(),
        update=AsyncMock(),
        get=AsyncMock(return_value=None),
        list=AsyncMock(return_value=[]),
        delete=AsyncMock(),
    )
    tracker = TaskTracker(store)
    ctx = _ctx()
    task = await tracker.create(
        "compile",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        task_id="cmp_cancel",
    )
    monkeypatch.setattr("openviking.service.memory_compile.get_task_tracker", lambda: tracker)
    model_started = asyncio.Event()
    memory_write = AsyncMock()
    runner = MemoryCompileRunner(SimpleNamespace())

    async def consolidate(**kwargs):
        del kwargs
        assert get_task_context().task_id == task.task_id
        model_started.set()
        await asyncio.Future()
        await memory_write()

    monkeypatch.setattr(runner, "_consolidate", consolidate)
    worker = asyncio.create_task(
        runner._run(
            task_id=task.task_id,
            target="viking://user/u1/memories" + (f"/{memory_type}" if memory_type else ""),
            memory_type=memory_type,
            peer_id=None,
            instruction=None,
            ctx=ctx,
        )
    )
    await model_started.wait()

    cancelling = await tracker.cancel(
        task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
    )
    assert cancelling.status == TaskStatus.CANCELLING
    await asyncio.wait_for(worker, timeout=1)

    final = await tracker.get(task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
    assert final.status == TaskStatus.CANCELLED
    assert not tracker.has_work(task.task_id)
    memory_write.assert_not_awaited()


@pytest.fixture
def xiaomei_root_result():
    from tests.integration import test_compile_memory_xiaomei as script

    root = "viking://user/xiaomei/memories"
    entity, preference, target = script._memory_root_seed_files(root, "xiaomei", "test1234")
    profile = MemoryFile(
        uri=f"{root}/profile.md",
        memory_type="profile",
        extra_fields={"memory_type": "profile"},
        content="# 小美\n- 姓名：小美 (as of 2023-04-09)\n",
    )
    before = {memory.uri: MemoryFileUtils.write(memory) for memory in (entity, preference, profile)}
    link = {**preference.links[0], "to_uri": target}
    renamed = entity.model_copy(deep=True)
    renamed.uri = target
    renamed.extra_fields["name"] = "pottery_teacher_test1234"
    renamed.backlinks = [link]
    compacted = preference.model_copy(deep=True)
    compacted.links = [link]
    compacted.content = (
        f"- 喜欢{entity.extra_fields['name']}老师的陶艺小班课，每班最多六人，方便老师逐个指导。\n"
        "- [课程资料](https://example.com/docs)\n"
    )
    after = {
        profile.uri: before[profile.uri],
        target: MemoryFileUtils.write(renamed),
        preference.uri: MemoryFileUtils.write(compacted),
    }
    task = {
        "task_id": "root-task",
        "status": "completed",
        "result": {
            "to": root,
            "memory_type": None,
            "memory_types": ["entities", "preferences", "profile"],
            "adds": [target],
            "updates": [preference.uri],
            "deletes": [entity.uri],
            "total_adds": 1,
            "total_updates": 1,
            "total_deletes": 1,
            "errors": [],
        },
    }
    return {
        "root": root,
        "source_uri": entity.uri,
        "target_uri": target,
        "preference_uri": preference.uri,
        "task": task,
        "before": before,
        "after": after,
    }


def test_xiaomei_memory_root_integrity_checks(xiaomei_root_result):
    from tests.integration import test_compile_memory_xiaomei as script

    script._verify_memory_root_case(**xiaomei_root_result)
    assert script.CASES["memory_root"] is script.CASE_MEMORY_ROOT


@pytest.mark.parametrize(
    "damage",
    [
        "scope",
        "flat-file",
        "diff",
        "extra-delete",
        "fact",
        "no-dedup",
        "external-link",
        "backlink",
        "stale-href",
        "unrelated",
    ],
)
def test_xiaomei_memory_root_rejects_false_success(xiaomei_root_result, damage):
    from tests.integration import test_compile_memory_xiaomei as script

    data = xiaomei_root_result
    result, after = data["task"]["result"], data["after"]
    preference_uri, target = data["preference_uri"], data["target_uri"]
    if damage == "scope":
        result["memory_type"] = "entities"
    elif damage == "flat-file":
        result["memory_types"].remove("profile")
    elif damage == "diff":
        result["updates"] = []
    elif damage == "extra-delete":
        del after[f"{data['root']}/profile.md"]
    elif damage == "fact":
        after[target] = after[target].replace("苏州", "上海")
    elif damage == "no-dedup":
        old = MemoryFileUtils.read(data["before"][preference_uri], uri=preference_uri)
        compacted = MemoryFileUtils.read(after[preference_uri], uri=preference_uri)
        compacted.content = old.content
        after[preference_uri] = MemoryFileUtils.write(compacted)
    elif damage == "external-link":
        after[preference_uri] = after[preference_uri].replace(
            "[课程资料](https://example.com/docs)", "课程资料"
        )
    elif damage == "backlink":
        entity = MemoryFileUtils.read(after[target], uri=target)
        entity.backlinks = []
        after[target] = MemoryFileUtils.write(entity)
    elif damage == "stale-href":
        after[preference_uri] += data["source_uri"]
    else:
        after[f"{data['root']}/profile.md"] += "\nchanged"
    with pytest.raises(AssertionError):
        script._verify_memory_root_case(**data)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_profile", [True, False])
async def test_xiaomei_root_case_submits_once_without_overwriting_history(
    monkeypatch, xiaomei_root_result, existing_profile
):
    from tests.integration import test_compile_memory_xiaomei as script

    data = xiaomei_root_result
    profile_uri = f"{data['root']}/profile.md"
    files = {profile_uri: data["before"][profile_uri]} if existing_profile else {}
    monkeypatch.setattr(script.uuid, "uuid4", lambda: SimpleNamespace(hex="test1234"))

    async def stat(uri):
        if uri not in files:
            raise NotFoundError(uri)
        return {"isDir": False}

    async def write(uri, content, mode, wait):
        assert mode == "create" and wait
        assert uri not in files
        files[uri] = content

    async def ls(*args, **kwargs):
        return [{"uri": uri, "isDir": False} for uri in files]

    async def read_raw(uri):
        return files[uri]

    async def get_task(task_id):
        assert files == data["before"]
        files.clear()
        files.update(data["after"])
        return data["task"]

    client = SimpleNamespace(
        stat=AsyncMock(side_effect=stat),
        write=AsyncMock(side_effect=write),
        ls=AsyncMock(side_effect=ls),
        read_raw=AsyncMock(side_effect=read_raw),
        compile=AsyncMock(return_value={"task_id": "root-task"}),
        get_task=AsyncMock(side_effect=get_task),
        wait_processed=AsyncMock(),
    )
    await script._run_case(
        client, SimpleNamespace(user="xiaomei", to=None, wait=0), script.CASE_MEMORY_ROOT
    )
    client.compile.assert_awaited_once()
    assert client.compile.await_args.kwargs["to"] == data["root"]
    assert client.compile.await_args.kwargs["skill"] == "memory"
    assert client.write.await_count == (2 if existing_profile else 3)
    assert files[profile_uri] == data["before"][profile_uri]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task",
    [
        None,
        {},
        {"status": "failed"},
        {"status": "cancelled"},
        {"status": "completed", "error": "failed"},
        {"status": "completed", "result": {"errors": ["write failed"]}},
        {"status": "completed"},
    ],
)
async def test_xiaomei_compile_rejects_failed_or_missing_result(task):
    from tests.integration import test_compile_memory_xiaomei as script

    client = SimpleNamespace(
        compile=AsyncMock(return_value={"task_id": "task"}), get_task=AsyncMock(return_value=task)
    )
    with pytest.raises(AssertionError):
        await script._run_memory_compile(client, "viking://user/xiaomei/memories/entities", "整理")


@pytest.mark.asyncio
async def test_xiaomei_compile_missing_id_and_noop():
    from tests.integration import test_compile_memory_xiaomei as script

    client = SimpleNamespace(compile=AsyncMock(return_value={}), get_task=AsyncMock())
    with pytest.raises(AssertionError, match="task_id"):
        await script._run_memory_compile(client, "viking://user/xiaomei/memories/entities", "整理")
    client.get_task.assert_not_awaited()
    task = {
        "status": "completed",
        "result": {"errors": [], "adds": [], "updates": [], "deletes": []},
    }
    client.compile.return_value = {"task_id": "task"}
    client.get_task.return_value = task
    assert (
        await script._run_memory_compile(client, "viking://user/xiaomei/memories/entities", "整理")
        == task
    )


def test_xiaomei_case_directory_rejects_wrong_scope():
    from tests.integration import test_compile_memory_xiaomei as script

    args = SimpleNamespace(user="xiaomei", to="viking://user/xiaomei/memories")
    assert script._case_directory(args, script.CASE_MEMORY_ROOT) == args.to
    with pytest.raises(ValueError, match="entities"):
        script._case_directory(args, script.CASE_RENAME)
    args.to = "viking://user/xiaomei/memories/entities"
    with pytest.raises(ValueError, match="根目录"):
        script._case_directory(args, script.CASE_MEMORY_ROOT)


@pytest.mark.asyncio
async def test_xiaomei_default_all_runs_root_after_single_type_cases(monkeypatch):
    from tests.integration import test_compile_memory_xiaomei as script

    client = SimpleNamespace(initialize=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(script.ov, "AsyncHTTPClient", lambda **kwargs: client)
    monkeypatch.setattr(script, "resolve_api_key", lambda key: key)
    run_case = AsyncMock()
    monkeypatch.setattr(script, "_run_case", run_case)
    args = SimpleNamespace(
        url="http://localhost:1933",
        api_key=None,
        account="default",
        user="xiaomei",
        to=None,
        case="all",
        ingest=False,
    )
    await script._main_async(args)
    assert [call.args[2].key for call in run_case.await_args_list] == [
        "merge",
        "split",
        "dedup",
        "rename",
        "preferences",
        "memory_root",
    ]
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_xiaomei_rename_uses_known_chinese_seed_with_english_output(
    monkeypatch, language_config
):
    from openviking.session.memory.utils.link_renderer import LinkRenderer
    from tests.integration import test_compile_memory_xiaomei as script

    language_config.output_language_override = "en"
    directory = "viking://user/xiaomei/memories/entities"
    chinese_name, english_name = "沈雨诗宁", "photo_teacher_test1234"
    chinese_uri = f"{directory}/人物/{chinese_name}.md"
    english_uri = f"{directory}/person/{english_name}.md"
    old_uri = f"{directory}/person/shen yushining.md"
    old_raw = "# Shen Yushining\nHistorical teacher memory must stay unchanged."
    files = {old_uri: old_raw}
    steps = [
        (f"{directory}/person/{chinese_name}.md", english_uri),
        (english_uri, chinese_uri),
        (chinese_uri, english_uri),
    ]
    completed = []
    monkeypatch.setattr(script, "_unique_rename_names", lambda case: (chinese_name, english_name))

    async def write(uri, raw, *, mode, wait):
        assert mode == "create" and wait
        assert uri not in files
        files[uri] = raw

    async def ls(uri, **kwargs):
        return [
            {"uri": path, "isDir": False, "size": len(raw)}
            for path, raw in files.items()
            if path.startswith(uri + "/")
        ]

    async def read(uri):
        return files[uri]

    async def stat(uri):
        if uri not in files and not any(path.startswith(uri + "/") for path in files):
            raise NotFoundError(uri)
        return {"isDir": uri not in files}

    async def compile(**kwargs):
        assert kwargs["to"] == directory and kwargs["skill"] == "memory"
        return {"task_id": str(len(completed))}

    async def get_task(task_id):
        source, target = steps[int(task_id)]
        entity = MemoryFileUtils.read(files.pop(source), uri=source)
        assert entity.memory_type == "entities"
        entity.uri = target
        entity.extra_fields.update(category=target.split("/")[-2], name=target.split("/")[-1][:-3])
        entity.content = (
            "# Shen Yushining\nXiaomei's photography teacher.\n\n## Background\n"
            "- Lives in Hangzhou.\n- Specializes in landscape photography.\n\n## Lesson\n"
            "- Takes Xiaomei to West Lake in October to practice long exposure."
        )
        event_uri = entity.backlinks[0]["from_uri"]
        event = MemoryFileUtils.read(files[event_uri], uri=event_uri)
        event.content = LinkRenderer.strip_managed_links(event.content, event_uri, event.links)
        event.links = [{**link, "to_uri": target} for link in event.links]
        entity.backlinks = [dict(link) for link in event.links]
        files[target] = MemoryFileUtils.write(entity)
        files[event_uri] = MemoryFileUtils.write(event)
        completed.append((source, target))
        return {
            "task_id": task_id,
            "status": "completed",
            "result": {
                "adds": [target],
                "updates": [event_uri],
                "deletes": [source],
                "total_adds": 1,
                "total_updates": 1,
                "total_deletes": 1,
                "errors": [],
            },
        }

    client = SimpleNamespace(
        write=AsyncMock(side_effect=write),
        ls=AsyncMock(side_effect=ls),
        read=AsyncMock(side_effect=read),
        read_raw=AsyncMock(side_effect=read),
        stat=AsyncMock(side_effect=stat),
        wait_processed=AsyncMock(),
        compile=AsyncMock(side_effect=compile),
        get_task=AsyncMock(side_effect=get_task),
        create_session=AsyncMock(side_effect=AssertionError("rename must not use extraction")),
    )
    await script._run_case(
        client, SimpleNamespace(user="xiaomei", to=None, wait=0), script.CASE_RENAME
    )
    assert completed == steps
    assert client.write.await_count == 2
    assert chinese_name in client.write.await_args_list[0].args[1]
    assert client.compile.await_count == 3
    client.create_session.assert_not_awaited()
    assert files[old_uri] == old_raw
    assert chinese_uri not in files
    assert english_uri in files
    assert language_config.output_language_override == "en"


@pytest.mark.asyncio
async def test_xiaomei_rename_seed_collision_preserves_existing_file():
    from openviking_cli.exceptions import ConflictError
    from tests.integration import test_compile_memory_xiaomei as script

    source = "viking://user/xiaomei/memories/entities/person/沈雨诗宁.md"
    files = {source: "existing"}

    async def write(uri, raw, *, mode, wait):
        assert mode == "create"
        if uri in files:
            raise ConflictError("already exists", resource=uri)
        files[uri] = raw

    client = SimpleNamespace(write=AsyncMock(side_effect=write), wait_processed=AsyncMock())
    with pytest.raises(ConflictError):
        await script._seed_rename_case(
            client, source.rpartition("/person/")[0], "沈雨诗宁", "photo_teacher_test1234"
        )
    assert files == {source: "existing"}
    assert client.write.await_count == 1
    client.wait_processed.assert_not_awaited()


@pytest.mark.asyncio
async def test_xiaomei_snapshot_does_not_hide_errors_or_truncation():
    from tests.integration import test_compile_memory_xiaomei as script

    client = SimpleNamespace(ls=AsyncMock(side_effect=RuntimeError("unavailable")))
    with pytest.raises(RuntimeError, match="unavailable"):
        await script._snapshot_memory_dir(client, "viking://user/xiaomei/memories")
    client.ls.side_effect = None
    client.ls.return_value = [{}] * 1000
    with pytest.raises(AssertionError, match="截断"):
        await script._snapshot_memory_dir(client, "viking://user/xiaomei/memories")
