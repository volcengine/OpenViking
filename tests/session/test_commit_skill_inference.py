import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.core.skill_loader import SkillLoader
from openviking.message import Message, TextPart, ToolPart
from openviking.server.identity import RequestContext, Role
from openviking.session.compressor_v2 import SessionCompressorV2
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
)
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.schema_model_generator import SchemaPromptGenerator
from openviking.session.memory_extractor import MemoryCategory, MemoryExtractor
from openviking.session.skill.session_skill_context_provider import (
    SessionSkillContextProvider,
    resolve_skill_extract_templates_dir,
)
from openviking.session.skill.skill_operation_updater import (
    SkillOperationUpdater,
    SkillOperationUpdateResult,
)
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.session.user_id import UserIdentifier


class _DummyVLM:
    def is_available(self):
        return True

    async def get_completion_async(self, _prompt):
        return json.dumps(
            {
                "skills": [
                    {
                        "situation": "代码审查与总结",
                        "skill_name": "代码审查流程",
                        "skill_content_reasoning": [
                            "从对话中提炼稳定步骤",
                            "保留可复用的检查顺序",
                        ],
                        "skill_content": "## 核心规范\n- 先阅读目标文件。\n- 基于证据总结问题。\n\n## 使用该Skill的场景\n- 代码审查\n\n## 实现指南\n### 先决条件\n- 能访问待审查代码\n### 技术和方法\n1. 先读文件。\n2. 再总结发现。\n### 应用场景示例\n- 阅读 README 后输出审查意见。\n\n## 核心要点\n- 先证据，后结论。",
                    }
                ]
            }
        )


def _mock_config():
    config = MagicMock()
    config.vlm = _DummyVLM()
    config.output_language_override = None
    return config


def _build_skill_registry() -> MemoryTypeRegistry:
    registry = MemoryTypeRegistry(load_schemas=False)
    loaded = registry.load_from_directory(str(resolve_skill_extract_templates_dir()))
    assert loaded > 0
    return registry


def _build_duplicate_session_skill_operations() -> ResolvedOperations:
    content = "## 核心规范\n- 分析常识题需按步骤执行\n- 输出答案"
    return ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=None,
                memory_fields={
                    "skill_name": "常识题分析流程",
                    "description": "规范常识题分析步骤，含选项分析、答案输出及知识点归纳",
                    "content": content,
                },
                memory_type="session_skills",
                uris=["viking://agent/default/skills/general-knowledge-flow/SKILL.md"],
            ),
            ResolvedOperation(
                old_memory_file_content=None,
                memory_fields={
                    "skill_name": "常识题分析步骤",
                    "description": "按步骤分析常识题，含选项分析、答案输出及知识点归纳",
                    "content": content,
                },
                memory_type="session_skills",
                uris=["viking://agent/default/skills/general-knowledge-steps/SKILL.md"],
            ),
        ],
        delete_file_contents=[],
        errors=[],
    )


@pytest.mark.asyncio
async def test_session_skill_schema_description_is_loaded_from_yaml():
    registry = _build_skill_registry()
    schema = registry.get("session_skills")
    assert schema is not None

    descriptions = SchemaPromptGenerator([schema]).generate_type_descriptions()

    assert "### session_skills" in descriptions
    assert "一、提取内容的识别规则" in descriptions
    assert "以后" in descriptions


@pytest.mark.asyncio
async def test_extract_does_not_inject_skill_memory_candidate(monkeypatch):
    extractor = MemoryExtractor()
    monkeypatch.setattr(
        "openviking.session.memory_extractor.get_openviking_config",
        _mock_config,
    )

    user = UserIdentifier.the_default_user()
    messages = [
        Message(
            id="m1",
            role="assistant",
            parts=[
                TextPart(
                    "We should use code-review skill: first read the project files, then summarize findings before drafting output."
                ),
                ToolPart(
                    tool_id="tool_1",
                    tool_name="read",
                    tool_uri="viking://session/test/tools/tool_1",
                    tool_input={"file_path": "README.md"},
                    tool_output="ok",
                    tool_status="completed",
                    duration_ms=12,
                ),
            ],
        )
    ]
    candidates = await extractor.extract(
        {
            "messages": messages,
            "summary": "",
        },
        user,
        "session-1",
    )

    skill_candidates = [c for c in candidates if c.category == MemoryCategory.SKILLS]
    assert skill_candidates == []


@pytest.mark.asyncio
async def test_session_compressor_v2_extract_agent_memories_returns_session_skills(monkeypatch):
    config = MagicMock()
    config.memory.agent_memory_enabled = False
    config.memory.session_skill_extraction_enabled = True

    async def _fake_run_extract_phase(
        self,
        provider,
        messages,
        ctx,
        strict_extract_errors,
        phase_label,
        post_apply=None,
    ):
        del self, messages, ctx, strict_extract_errors, post_apply
        assert phase_label == "trajectory"
        assert provider._include_trajectories is False
        assert provider._include_session_skills is True
        return (
            [],
            [],
            [],
            {},
            [
                {
                    "status": "success",
                    "action": "create",
                    "uri": "viking://agent/default/skills/code-review",
                    "root_uri": "viking://agent/default/skills/code-review",
                    "skill_md_uri": "viking://agent/default/skills/code-review/SKILL.md",
                }
            ],
        )

    monkeypatch.setattr("openviking.session.compressor_v2.get_openviking_config", lambda: config)
    monkeypatch.setattr(SessionCompressorV2, "_run_extract_phase", _fake_run_extract_phase)

    compressor = SessionCompressorV2(vikingdb=MagicMock(), skill_processor=MagicMock())
    result = await compressor.extract_agent_memories(
        messages=[Message(id="m1", role="assistant", parts=[TextPart("Summarize a review flow")])],
        ctx=RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT),
        latest_archive_overview="",
        archive_uri="viking://sessions/s1/history/archive_001",
    )

    assert result == {
        "contexts": [],
        "session_skills": [
            {
                "status": "success",
                "action": "create",
                "uri": "viking://agent/default/skills/code-review",
                "root_uri": "viking://agent/default/skills/code-review",
                "skill_md_uri": "viking://agent/default/skills/code-review/SKILL.md",
                "archive_uri": "viking://sessions/s1/history/archive_001",
            }
        ],
    }


@pytest.mark.asyncio
async def test_session_compressor_v2_run_extract_phase_dedups_duplicate_session_skill_creates(
    monkeypatch,
):
    config = MagicMock()
    config.vlm.get_vlm_instance.return_value = MagicMock(model="dummy-model")
    config.memory.v2_lock_retry_interval_seconds = 0
    config.memory.v2_lock_max_retries = 0
    viking_fs = MagicMock()
    viking_fs.agfs = None

    async def _fake_run(self):
        return _build_duplicate_session_skill_operations(), []

    async def _fake_apply(self, operations, ctx):
        del ctx
        assert len(operations.upsert_operations) == 1
        kept = operations.upsert_operations[0]
        assert kept.memory_fields["skill_name"] == "常识题分析流程"
        result = SkillOperationUpdateResult()
        result.add_result(
            {
                "status": "success",
                "action": "create",
                "uri": kept.uris[0].rsplit("/SKILL.md", 1)[0],
                "root_uri": kept.uris[0].rsplit("/SKILL.md", 1)[0],
                "skill_md_uri": kept.uris[0],
            }
        )
        return result

    monkeypatch.setattr("openviking.session.compressor_v2.get_openviking_config", lambda: config)
    monkeypatch.setattr("openviking.session.compressor_v2.get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr("openviking.session.compressor_v2.ExtractLoop.run", _fake_run)
    monkeypatch.setattr(
        "openviking.session.skill.skill_operation_updater.SkillOperationUpdater.apply_operations",
        _fake_apply,
    )

    compressor = SessionCompressorV2(vikingdb=MagicMock(), skill_processor=MagicMock())
    messages = [Message(id="m1", role="assistant", parts=[TextPart("Summarize a workflow")])]
    provider = AgentTrajectoryContextProvider(
        messages=messages,
        latest_archive_overview="",
        include_trajectories=False,
        include_session_skills=True,
    )
    result = await compressor._run_extract_phase(
        provider=provider,
        messages=messages,
        ctx=RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT),
        strict_extract_errors=True,
        phase_label="trajectory",
    )

    assert result is not None
    assert result[4][0]["uri"] == "viking://agent/default/skills/general-knowledge-flow"


@pytest.mark.asyncio
async def test_session_skill_context_provider_prefetch_lists_existing_skills():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    viking_fs = MagicMock()
    viking_fs.ls = AsyncMock(
        return_value=[
            {
                "name": "code-review",
                "uri": "viking://agent/default/skills/code-review",
                "isDir": True,
                "abstract": "name: code-review\ndescription: Review code carefully",
            }
        ]
    )
    provider = SessionSkillContextProvider(
        messages=[
            Message(id="m1", role="assistant", parts=[TextPart("Summarize a reusable review flow")])
        ],
        latest_archive_overview="",
        ctx=ctx,
        viking_fs=viking_fs,
    )

    prefetched = await provider.prefetch()

    assert len(prefetched) == 2
    assert "Conversation History" in prefetched[0]["content"]
    assert "code-review" in prefetched[1]["content"]
    assert "SKILL.md" in prefetched[1]["content"]


@pytest.mark.asyncio
async def test_agent_trajectory_context_provider_without_session_skills_prefetches_history_only():
    provider = AgentTrajectoryContextProvider(
        messages=[
            Message(id="m1", role="assistant", parts=[TextPart("Summarize a reusable review flow")])
        ],
        latest_archive_overview="",
        include_trajectories=True,
        include_session_skills=False,
    )

    prefetched = await provider.prefetch()

    assert not isinstance(provider, SessionSkillContextProvider)
    assert len(prefetched) == 1
    assert "Conversation History" in prefetched[0]["content"]
    assert provider.get_tools() == []


@pytest.mark.asyncio
async def test_agent_trajectory_context_provider_delegates_skill_prefetch_and_read():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    viking_fs = MagicMock()
    viking_fs.ls = AsyncMock(
        return_value=[
            {
                "name": "code-review",
                "uri": "viking://agent/default/skills/code-review",
                "isDir": True,
                "abstract": "name: code-review\ndescription: Review code carefully",
            }
        ]
    )
    viking_fs.read_file = AsyncMock(
        return_value="""---
name: code-review
description: Review code carefully
allowed-tools:
- Read
tags:
- session-derived
---

## 核心规范
- 先读文件
"""
    )
    provider = AgentTrajectoryContextProvider(
        messages=[
            Message(id="m1", role="assistant", parts=[TextPart("Summarize a reusable review flow")])
        ],
        latest_archive_overview="",
        include_trajectories=False,
        include_session_skills=True,
    )
    provider._ctx = ctx
    provider._viking_fs = viking_fs

    prefetched = await provider.prefetch()
    read_result = await provider.execute_tool(
        SimpleNamespace(
            name="read",
            arguments={"uri": "viking://agent/default/skills/code-review/SKILL.md"},
        )
    )

    assert len(prefetched) == 2
    assert "code-review" in prefetched[1]["content"]
    assert provider.get_tools() == ["read"]
    assert read_result["name"] == "code-review"
    assert "先读文件" in read_result["content"]
    assert "viking://agent/default/skills/code-review/SKILL.md" in provider.read_file_contents
    assert provider._skill_provider.read_file_contents is provider.read_file_contents
    assert provider._skill_provider._ctx is ctx
    assert provider._skill_provider._viking_fs is viking_fs
    assert provider._skill_provider._extract_context is provider.get_extract_context()


@pytest.mark.asyncio
async def test_skill_operation_updater_creates_skill_with_session_defaults():
    processor = MagicMock()
    processor.process_skill = AsyncMock(
        return_value={
            "status": "success",
            "uri": "viking://agent/default/skills/code-review",
            "root_uri": "viking://agent/default/skills/code-review",
        }
    )
    processor.sanitize_skill_privacy = AsyncMock(side_effect=lambda skill_dict, _ctx: skill_dict)
    viking_fs = MagicMock()
    viking_fs.read_file = AsyncMock(side_effect=FileNotFoundError())
    updater = SkillOperationUpdater(
        registry=_build_skill_registry(),
        skill_processor=processor,
        viking_fs=viking_fs,
    )
    uri = "viking://agent/default/skills/code-review/SKILL.md"
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=None,
                memory_fields={
                    "skill_name": "code-review",
                    "description": "Review code from evidence",
                    "content": {"blocks": [{"search": "", "replace": "## 核心规范\n- 先读文件"}]},
                },
                memory_type="session_skills",
                uris=[uri],
            )
        ],
        delete_file_contents=[],
        errors=[],
    )

    result = await updater.apply_operations(
        operations,
        RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT),
    )

    processor.process_skill.assert_awaited_once()
    call = processor.process_skill.await_args.kwargs
    assert call["allow_local_path_resolution"] is False
    assert call["data"] == {
        "name": "code-review",
        "description": "Review code from evidence",
        "content": "## 核心规范\n- 先读文件",
        "allowed_tools": [],
        "tags": ["session-derived"],
    }
    assert result.written_uris == [uri]
    assert result.edited_uris == []
    assert result.operation_results[0]["action"] == "create"


@pytest.mark.asyncio
async def test_skill_operation_updater_updates_existing_skill(monkeypatch):
    existing_skill_md = """---
name: code-review
description: Review code carefully
allowed-tools:
- Read
tags:
- session-derived
---

## 核心规范
- 先读文件
"""
    viking_fs = MagicMock()
    viking_fs.read_file = AsyncMock(return_value=existing_skill_md)
    write_calls = {}

    class _FakeContentWriter:
        def __init__(self, _viking_fs):
            self._viking_fs = _viking_fs

        async def write(self, **kwargs):
            write_calls.update(kwargs)
            return {
                "uri": kwargs["uri"],
                "root_uri": kwargs["uri"].rsplit("/SKILL.md", 1)[0],
            }

    monkeypatch.setattr(
        "openviking.session.skill.skill_operation_updater.ContentWriteCoordinator",
        _FakeContentWriter,
    )

    processor = MagicMock()
    processor.sanitize_skill_privacy = AsyncMock(side_effect=lambda skill_dict, _ctx: skill_dict)
    updater = SkillOperationUpdater(
        registry=_build_skill_registry(),
        skill_processor=processor,
        viking_fs=viking_fs,
    )
    uri = "viking://agent/default/skills/code-review/SKILL.md"
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=None,
                memory_fields={
                    "skill_name": "code-review",
                    "description": "Review code from evidence",
                    "content": {
                        "blocks": [
                            {
                                "search": "- 先读文件",
                                "replace": "- 先读文件\n- 基于证据总结问题",
                            }
                        ]
                    },
                },
                memory_type="session_skills",
                uris=[uri],
            )
        ],
        delete_file_contents=[],
        errors=[],
    )

    result = await updater.apply_operations(
        operations,
        RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT),
    )

    assert result.written_uris == []
    assert result.edited_uris == [uri]
    assert result.operation_results[0]["action"] == "update"
    assert write_calls["uri"] == uri
    assert write_calls["mode"] == "replace"
    assert "allowed-tools:" in write_calls["content"]
    assert "tags:" in write_calls["content"]
    assert "Review code from evidence" in write_calls["content"]
    assert "- 基于证据总结问题" in write_calls["content"]


@pytest.mark.asyncio
async def test_skill_operation_updater_sanitizes_existing_skill_updates(monkeypatch):
    existing_skill_md = """---
name: code-review
description: Review code carefully
allowed-tools:
- Read
tags:
- session-derived
---

## 核心规范
- 先读文件
"""
    viking_fs = MagicMock()
    viking_fs.read_file = AsyncMock(return_value=existing_skill_md)
    write_calls = {}

    class _FakeContentWriter:
        def __init__(self, _viking_fs):
            self._viking_fs = _viking_fs

        async def write(self, **kwargs):
            write_calls.update(kwargs)
            return {
                "uri": kwargs["uri"],
                "root_uri": kwargs["uri"].rsplit("/SKILL.md", 1)[0],
            }

    monkeypatch.setattr(
        "openviking.session.skill.skill_operation_updater.ContentWriteCoordinator",
        _FakeContentWriter,
    )

    async def _fake_extract_skill_privacy_values(*, skill_name, skill_description, content):
        assert skill_name == "code-review"
        assert skill_description == "Review code from evidence"
        assert "api_key=secret-xyz" in content
        return SimpleNamespace(
            values={"api_key": "secret-xyz"},
            sanitized_content=content.replace(
                "api_key=secret-xyz",
                "api_key={{ov_privacy:skill:code-review:api_key}}",
            ),
        )

    monkeypatch.setattr(
        "openviking.utils.skill_processor.extract_skill_privacy_values",
        _fake_extract_skill_privacy_values,
    )

    privacy_config_service = MagicMock()
    privacy_config_service.upsert = AsyncMock()
    processor = SkillProcessor(
        vikingdb=MagicMock(),
        privacy_config_service=privacy_config_service,
    )
    updater = SkillOperationUpdater(
        registry=_build_skill_registry(),
        skill_processor=processor,
        viking_fs=viking_fs,
    )
    uri = "viking://agent/default/skills/code-review/SKILL.md"
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=None,
                memory_fields={
                    "skill_name": "code-review",
                    "description": "Review code from evidence",
                    "content": {
                        "blocks": [
                            {
                                "search": "- 先读文件",
                                "replace": "- 先读文件\napi_key=secret-xyz",
                            }
                        ]
                    },
                },
                memory_type="session_skills",
                uris=[uri],
            )
        ],
        delete_file_contents=[],
        errors=[],
    )
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    result = await updater.apply_operations(operations, ctx)

    assert result.written_uris == []
    assert result.edited_uris == [uri]
    assert result.operation_results[0]["action"] == "update"
    assert "secret-xyz" not in write_calls["content"]
    assert "{{ov_privacy:skill:code-review:api_key}}" in write_calls["content"]
    privacy_config_service.upsert.assert_awaited_once_with(
        ctx=ctx,
        category="skill",
        target_key="code-review",
        values={"api_key": "secret-xyz"},
        updated_by=ctx.user.user_id,
        change_reason="auto-extracted from add_skill",
    )


def test_skill_loader_to_skill_md_round_trip_with_lists():
    skill_md = SkillLoader.to_skill_md(
        {
            "name": "code-review",
            "description": "Review code from evidence",
            "content": "## 核心规范\n- 先读文件",
            "allowed_tools": ["Read"],
            "tags": ["session-derived"],
        }
    )

    parsed = SkillLoader.parse(skill_md)

    assert parsed == {
        "name": "code-review",
        "description": "Review code from evidence",
        "content": "## 核心规范\n- 先读文件",
        "source_path": "",
        "allowed_tools": ["Read"],
        "tags": ["session-derived"],
    }
