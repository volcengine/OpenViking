# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from dataclasses import dataclass, field
from typing import Any, List

import pytest

from openviking.message import Message, TextPart
from openviking.session.memory.core import ExtractContextProvider
from openviking.session.memory.memory_updater import MemoryUpdateResult
from openviking.session.memory.segmented_extract_runner import (
    CallableProviderFactory,
    CallableUpdaterFactory,
    SegmentedExtractRunner,
    SegmentedExtractSharedContext,
)
from openviking.session.skill.skill_operation_updater import SkillOperationUpdateResult


def _make_message(msg_id: str, char_count: int, *, content: str | None = None) -> Message:
    return Message(
        id=msg_id,
        role="user",
        parts=[TextPart(content if content is not None else ("x" * char_count))],
    )


class _FakeProvider(ExtractContextProvider):
    def __init__(self, messages: List[Message]):
        self.messages = messages

    @staticmethod
    def get_reserve_tokens() -> int:
        return 10

    def instruction(self) -> str:
        return "instruction"

    async def prefetch(self) -> List[dict]:
        return []

    def get_tools(self) -> List[str]:
        return []

    def get_memory_schemas(self, ctx):
        del ctx
        return []


class _LargeReserveProvider(_FakeProvider):
    @staticmethod
    def get_reserve_tokens() -> int:
        return 999


@dataclass
class _FakeResult:
    values: List[str] = field(default_factory=list)


class _FakeUpdater:
    def __init__(self, applied: List[str]):
        self._applied = applied

    @classmethod
    def merge(cls, results: List[_FakeResult]) -> _FakeResult:
        merged = _FakeResult()
        for result in results:
            merged.values.extend(result.values)
        return merged

    async def apply_operations(self, operations, ctx, **kwargs) -> _FakeResult:
        del ctx, kwargs
        self._applied.append(operations["value"])
        return _FakeResult(values=[operations["value"]])


class _FakeExtractLoop:
    def __init__(self, **kwargs):
        self._provider = kwargs["context_provider"]

    async def run(self):
        value = self._provider.messages[0].content
        if value == "FAIL":
            raise RuntimeError("boom")
        return {"value": value}, []


class TestSegmentedExtractRunner:
    def test_segment_messages_under_budget_returns_one_segment(self):
        messages = [
            _make_message("m1", 40),
            _make_message("m2", 40),
        ]

        segments = SegmentedExtractRunner.segment_messages(messages, max_segment_tokens=100)

        assert len(segments) == 1
        assert segments[0].start_index == 0
        assert segments[0].end_index == 1
        assert segments[0].messages == messages

    def test_segment_messages_splits_without_overlap(self):
        messages = [
            _make_message("m1", 40),
            _make_message("m2", 40),
            _make_message("m3", 40),
        ]

        segments = SegmentedExtractRunner.segment_messages(messages, max_segment_tokens=15)

        assert [(segment.start_index, segment.end_index) for segment in segments] == [
            (0, 0),
            (1, 1),
            (2, 2),
        ]

    def test_segment_messages_keeps_oversized_message_alone(self):
        messages = [
            _make_message("m1", 80),
            _make_message("m2", 20),
        ]

        segments = SegmentedExtractRunner.segment_messages(messages, max_segment_tokens=10)

        assert len(segments) == 2
        assert segments[0].start_index == 0
        assert segments[0].end_index == 0
        assert segments[1].start_index == 1
        assert segments[1].end_index == 1

    @pytest.mark.asyncio
    async def test_runner_skips_provider_when_budget_not_positive(self, monkeypatch):
        errors: List[str] = []
        monkeypatch.setattr(
            "openviking.session.memory.segmented_extract_runner.tracer.error",
            errors.append,
        )

        runner = SegmentedExtractRunner(
            messages=[_make_message("m1", 20)],
            shared_context=SegmentedExtractSharedContext(
                ctx=None,
                vlm=object(),
                viking_fs=None,
                phase_label="test",
            ),
            provider_factory=CallableProviderFactory(
                provider_cls=_LargeReserveProvider,
                factory=lambda segment, shared_context: _LargeReserveProvider(segment.messages),
            ),
            updater_factory=CallableUpdaterFactory(
                updater_cls=_FakeUpdater,
                factory=lambda shared_context: _FakeUpdater([]),
            ),
            input_window_tokens=100,
        )

        result = await runner.run()

        assert result.values == []
        assert errors
        assert "Skipping provider" in errors[0]

    @pytest.mark.asyncio
    async def test_runner_continues_after_segment_failure(self, monkeypatch):
        errors: List[str] = []
        applied: List[str] = []
        monkeypatch.setattr(
            "openviking.session.memory.segmented_extract_runner.tracer.error",
            errors.append,
        )

        runner = SegmentedExtractRunner(
            messages=[
                _make_message("m1", 40, content="A"),
                _make_message("m2", 40, content="FAIL"),
                _make_message("m3", 40, content="C"),
            ],
            shared_context=SegmentedExtractSharedContext(
                ctx=None,
                vlm=object(),
                viking_fs=None,
                phase_label="test",
                metadata={"extract_loop_cls": _FakeExtractLoop},
            ),
            provider_factory=CallableProviderFactory(
                provider_cls=_FakeProvider,
                factory=lambda segment, shared_context: _FakeProvider(segment.messages),
            ),
            updater_factory=CallableUpdaterFactory(
                updater_cls=_FakeUpdater,
                factory=lambda shared_context: _FakeUpdater(applied),
            ),
            input_window_tokens=11,
        )

        result = await runner.run()

        assert applied == ["A", "C"]
        assert result.values == ["A", "C"]
        assert any("Segment 1" in message for message in errors)


class TestMergeSemantics:
    def test_memory_update_result_merge_net_effect(self):
        first = MemoryUpdateResult()
        first.add_written("uri://new")
        first.add_edited("uri://existing")
        first.add_deleted("uri://old")

        second = MemoryUpdateResult()
        second.add_edited("uri://new")
        second.add_deleted("uri://new")
        second.add_deleted("uri://existing")
        second.add_written("uri://old")

        merged = MemoryUpdateResult.merge([first, second])

        assert merged.written_uris == []
        assert merged.edited_uris == ["uri://old"]
        assert merged.deleted_uris == ["uri://existing"]

    def test_skill_update_result_merge_normalizes_final_action(self):
        first = SkillOperationUpdateResult()
        first.add_written("viking://agent/default/skills/demo/SKILL.md")
        first.add_result(
            {
                "action": "create",
                "skill_md_uri": "viking://agent/default/skills/demo/SKILL.md",
                "uri": "viking://agent/default/skills/demo",
            }
        )

        second = SkillOperationUpdateResult()
        second.add_edited("viking://agent/default/skills/demo/SKILL.md")
        second.add_result(
            {
                "action": "update",
                "skill_md_uri": "viking://agent/default/skills/demo/SKILL.md",
                "uri": "viking://agent/default/skills/demo",
            }
        )

        merged = SkillOperationUpdateResult.merge([first, second])

        assert merged.written_uris == ["viking://agent/default/skills/demo/SKILL.md"]
        assert merged.edited_uris == []
        assert merged.operation_results[0]["action"] == "create"
