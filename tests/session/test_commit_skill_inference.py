from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.core.skill_loader import SkillLoader
from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.compressor_v3 import _v3_extraction_response
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
)
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.skill.dedup import dedup_session_skill_operations
from openviking.session.skill.session_skill_context_provider import (
    SessionSkillContextProvider,
    resolve_skill_extract_templates_dir,
)
from openviking.session.skill.skill_operation_updater import SkillOperationUpdater
from openviking.utils.skill_processor import SkillProcessor
from openviking_cli.session.user_id import UserIdentifier


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
                uris=["viking://user/default/skills/general-knowledge-flow/SKILL.md"],
            ),
            ResolvedOperation(
                old_memory_file_content=None,
                memory_fields={
                    "skill_name": "常识题分析步骤",
                    "description": "按步骤分析常识题，含选项分析、答案输出及知识点归纳",
                    "content": content,
                },
                memory_type="session_skills",
                uris=["viking://user/default/skills/general-knowledge-steps/SKILL.md"],
            ),
        ],
        delete_file_contents=[],
        errors=[],
    )


def test_v3_extraction_response_returns_session_skills():
    result = _v3_extraction_response(
        contexts=[],
        train_result={"skill_uris": ["viking://user/default/skills/code-review"]},
        archive_uri="viking://sessions/s1/history/archive_001",
    )

    assert result == {
        "contexts": [],
        "session_skills": [
            {
                "uri": "viking://user/default/skills/code-review",
                "archive_uri": "viking://sessions/s1/history/archive_001",
            }
        ],
    }


def test_session_skill_operations_dedup_duplicate_creates():
    result = dedup_session_skill_operations(_build_duplicate_session_skill_operations())

    assert len(result.upsert_operations) == 1
    assert result.upsert_operations[0].memory_fields["skill_name"] == "常识题分析流程"


@pytest.mark.asyncio
async def test_session_skill_context_provider_prefetch_lists_existing_skills():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    viking_fs = MagicMock()
    viking_fs.ls = AsyncMock(
        return_value=[
            {
                "name": "code-review",
                "uri": "viking://user/default/skills/code-review",
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
                "uri": "viking://user/default/skills/code-review",
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
            arguments={"uri": "viking://user/default/skills/code-review/SKILL.md"},
        )
    )

    assert len(prefetched) == 2
    assert "code-review" in prefetched[1]["content"]
    assert provider.get_tools() == ["read"]
    assert read_result["name"] == "code-review"
    assert "先读文件" in read_result["content"]
    assert "viking://user/default/skills/code-review/SKILL.md" in provider.read_file_contents
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
            "uri": "viking://user/default/skills/code-review",
            "root_uri": "viking://user/default/skills/code-review",
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
    uri = "viking://user/default/skills/code-review/SKILL.md"
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
    uri = "viking://user/default/skills/code-review/SKILL.md"
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
    uri = "viking://user/default/skills/code-review/SKILL.md"
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
