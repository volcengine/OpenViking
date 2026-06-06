# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session import create_session_compressor
from openviking.session.compressor_v3 import SessionCompressorV3
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.session.train import StreamingPolicyTrainerConfig
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user("u"), role=Role.ROOT)


def _messages() -> list[Message]:
    return [
        Message(
            id="m1",
            role="user",
            parts=[TextPart("请处理重复预订，只取消确认是重复的那一单。")],
        ),
        Message(
            id="m2",
            role="assistant",
            parts=[TextPart("已读取两个预订，确认第二个是重复记录并取消。")],
        ),
    ]


def _case_operation() -> ResolvedOperation:
    return ResolvedOperation(
        old_memory_file_content=None,
        memory_type="cases",
        uris=["viking://user/u/memories/cases/重复预订处理.md"],
        memory_fields={
            "case_name": "重复预订处理",
            "task_signature": "处理重复预订并只取消确认重复的订单",
            "input": '{"summary":"用户要求处理重复预订","preconditions":["存在两个相似预订"]}',
            "rubric": '{"name":"重复预订处理Rubric","description":"成功且高效处理重复预订","criteria":[{"name":"先验证重复","description":"取消前必须确认哪一单是重复订单","required":true,"weight":0.6},{"name":"只取消重复项","description":"不得影响有效订单","required":true,"weight":0.4}]}',
            "evidence": "助手根据读取结果确认重复项并完成取消。",
        },
    )


def test_factory_supports_v3():
    compressor = create_session_compressor(vikingdb=None, memory_version="v3")
    assert isinstance(compressor, SessionCompressorV3)


@pytest.mark.asyncio
async def test_train_from_extracted_case_memories_submits_streaming_rollout(monkeypatch):
    submitted = []

    class FakeTrainer:
        async def submit_rollout(self, rollout):
            submitted.append(rollout)
            return None

    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_viking_fs",
        lambda: SimpleNamespace(ls=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_policy_trainer",
        AsyncMock(return_value=FakeTrainer()),
    )

    compressor = SessionCompressorV3(
        vikingdb=None,
        streaming_trainer_config=StreamingPolicyTrainerConfig(
            max_wait_seconds=60,
            max_gradients_per_update=8,
        ),
    )
    operations = ResolvedOperations(
        upsert_operations=[_case_operation()],
        delete_file_contents=[],
        errors=[],
    )

    # The extracted case comes from the same memory operations as profile/preferences/etc.;
    # no extra LLM/VLM case extractor is involved.
    cases = __import__(
        "openviking.session.compressor_v3", fromlist=["_operations_to_cases"]
    )._operations_to_cases(operations)
    result = await compressor.train_from_extracted_cases(
        cases=cases,
        messages=_messages(),
        ctx=_ctx(),
        session_id="s1",
    )

    assert result == {"case_count": 1, "submitted": 1}
    assert len(submitted) == 1
    assert submitted[0].case.name == "重复预订处理"
    assert submitted[0].case.input["summary"] == "用户要求处理重复预订"
    assert submitted[0].case.rubric.criteria[0].name == "先验证重复"


@pytest.mark.asyncio
async def test_v3_extract_uses_patch_merge_without_directory_lock(monkeypatch):
    applied_operations = []
    trained_cases = []

    class DummyRegistry:
        async def initialize_memory_files(self, ctx):
            return None

    class DummyOrchestrator:
        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[_case_operation()],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    class FakeStreamingUpdater:
        async def submit(self, request):
            applied_operations.append(request.operations)
            result = MemoryUpdateResult()
            result.add_written(_case_operation().uris[0])
            return SimpleNamespace(operations=request.operations, apply_result=result)

    compressor = SessionCompressorV3(vikingdb=None)
    compressor._get_or_create_react = lambda **kwargs: DummyOrchestrator()

    async def fake_train_from_extracted_cases(**kwargs):
        trained_cases.extend(kwargs["cases"])
        return {"case_count": len(kwargs["cases"]), "submitted": len(kwargs["cases"])}

    compressor.train_from_extracted_cases = fake_train_from_extracted_cases

    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_viking_fs",
        lambda: SimpleNamespace(write_file=AsyncMock()),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.create_default_registry",
        lambda: DummyRegistry(),
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_memory_updater",
        AsyncMock(return_value=FakeStreamingUpdater()),
    )

    contexts = await compressor.extract_long_term_memories(
        messages=_messages(),
        ctx=_ctx(),
        allowed_memory_types={"cases", "profile"},
    )

    assert len(applied_operations) == 1
    assert applied_operations[0].upsert_operations[0].memory_type == "cases"
    assert [case.name for case in trained_cases] == ["重复预订处理"]
    assert contexts[0].uri.endswith("重复预订处理.md")
