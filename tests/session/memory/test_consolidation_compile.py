# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for `ov compile --skill memory` in-place memory consolidation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.compile_service import MEMORY_COMPILE_SKILL, CompileRequest
from openviking.service.memory_compile import (
    MemoryCompileRunner,
    _memory_type_from_target,
    _peer_id_from_memory_uri,
)
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.service.task_work_index import get_task_context
from openviking.session.memory.consolidation_context_provider import (
    ConsolidationExtractContextProvider,
    build_consolidation_isolation_handler,
)
from openviking.session.memory.memory_type_registry import get_default_registry
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdateResult
from openviking_cli.exceptions import InvalidArgumentError
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


def test_memory_type_from_target_rejects_non_memory_or_root():
    with pytest.raises(InvalidArgumentError):
        _memory_type_from_target("viking://resources/wiki")
    with pytest.raises(InvalidArgumentError):
        _memory_type_from_target("viking://user/u1/memories")


def test_peer_id_from_memory_uri():
    assert _peer_id_from_memory_uri("viking://user/u1/memories/entities") is None
    assert _peer_id_from_memory_uri("viking://user/u1/peers/agent_x/memories/entities") == "agent_x"


# ── Isolation handler scope ──


def test_consolidation_isolation_self_vs_peer():
    ctx = _ctx()
    self_handler = build_consolidation_isolation_handler(
        ctx, ExtractContext([]), memory_type="entities", peer_id=None
    )
    assert self_handler.allow_self is True
    assert self_handler.allowed_peer_ids == set()

    peer_handler = build_consolidation_isolation_handler(
        ctx, ExtractContext([]), memory_type="entities", peer_id="agent_x"
    )
    assert peer_handler.allow_self is False
    assert peer_handler.allowed_peer_ids == {"agent_x"}


# ── Provider schema scope and prefetch ──


def test_provider_loads_single_schema_from_to():
    ctx = _ctx()
    provider = ConsolidationExtractContextProvider(memory_type="entities")
    schemas = provider.get_memory_schemas(ctx)
    assert [s.memory_type for s in schemas] == ["entities"]


def test_provider_unknown_type_raises():
    provider = ConsolidationExtractContextProvider(memory_type="does_not_exist")
    with pytest.raises(ValueError):
        provider.get_memory_schemas(_ctx())


@pytest.mark.asyncio
async def test_prefetch_seeds_recursive_listing_and_lets_model_explore():
    ctx = _ctx()
    provider = ConsolidationExtractContextProvider(
        memory_type="entities",
        target_directory="viking://user/u1/memories/entities",
    )
    provider._ctx = ctx

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
    provider._viking_fs = SimpleNamespace(glob=AsyncMock(return_value=glob_result))

    messages = await provider.prefetch()

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
    provider = ConsolidationExtractContextProvider(memory_type="entities")
    assert provider.get_tools() == ["ls", "search", "read"]


def test_instruction_mentions_type_and_explore_tools():
    provider = ConsolidationExtractContextProvider(
        memory_type="entities", instruction="Only touch pets."
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
        memory=SimpleNamespace(eager_prefetch=False, prefetch_search_topn=5, link_enabled=False),
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
    if override:
        viking_fs.read.assert_not_awaited()
    else:
        viking_fs.read.assert_awaited_once_with(uri, size=4096, ctx=_ctx())
        assert content not in str(vlm.get_completion_async.call_args.kwargs["messages"])


@pytest.mark.asyncio
async def test_cancel_running_compile_interrupts_before_memory_write(monkeypatch):
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
            target="viking://user/u1/memories/entities",
            memory_type="entities",
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
