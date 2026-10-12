# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.utils.model_call import ModelCallError


class RecordingVLM:
    def __init__(self):
        self.prompts = []

    def is_available(self):
        return True

    async def get_completion_async(self, prompt):
        self.prompts.append(prompt)
        return f"overview-{len(self.prompts)}"


class MergePlaceholderVLM(RecordingVLM):
    async def get_completion_async(self, prompt):
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return "[first](viking://input_sample_f1)"
        if len(self.prompts) == 2:
            return "[second](viking://input_sample_f2)"
        return "[first](viking://input_sample_f1) and [second](viking://input_sample_f2)"


class _TestVLMResolver:
    def __init__(self, vlm):
        self._vlm = vlm

    async def get_vlm(self, account_id):
        del account_id
        return self._vlm


@pytest.mark.asyncio
async def test_terminal_batch_cancels_and_drains_sibling_requests(monkeypatch):
    started = asyncio.Event()
    drained = asyncio.Event()
    calls = 0

    async def completion(prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            await started.wait()
            raise ModelCallError("max_attempts", "transient", 4, "fixture")
        started.set()
        try:
            await asyncio.Future()
        finally:
            drained.set()

    config = SimpleNamespace(
        vlm=SimpleNamespace(
            is_available=lambda: True, get_completion_async=AsyncMock(side_effect=completion)
        ),
        semantic=SimpleNamespace(max_overview_prompt_chars=1, overview_batch_size=1),
        output_language_override="en",
    )
    monkeypatch.setattr(semantic_processor_module, "get_openviking_config", lambda: config)
    monkeypatch.setattr(semantic_processor_module, "render_prompt", lambda *_: "prompt")
    processor = SemanticProcessor(
        max_concurrent_llm=2,
        vlm_resolver=_TestVLMResolver(config.vlm),
    )
    with pytest.raises(ModelCallError, match="max_attempts"):
        await asyncio.wait_for(
            processor._generate_overview(
                "viking://resources/root",
                [{"name": "a", "summary": "a"}, {"name": "b", "summary": "b"}],
                [],
            ),
            timeout=1,
        )
    assert drained.is_set()
    assert calls == 2


@pytest.mark.asyncio
async def test_children_only_oversized_overview_is_batched(monkeypatch):
    vlm = RecordingVLM()
    config = SimpleNamespace(
        vlm=vlm,
        semantic=SimpleNamespace(
            max_overview_prompt_chars=20,
            overview_batch_size=2,
        ),
        output_language_override="en",
    )
    monkeypatch.setattr(
        semantic_processor_module,
        "get_openviking_config",
        lambda: config,
    )
    monkeypatch.setattr(
        semantic_processor_module,
        "render_prompt",
        lambda _name, values: (
            f"files={values['file_summaries']}|children={values['children_abstracts']}"
        ),
    )
    children = [{"name": f"child-{index}", "abstract": "x" * 20} for index in range(3)]

    overview = await SemanticProcessor(vlm_resolver=_TestVLMResolver(vlm))._generate_overview(
        "viking://resources/root",
        file_summaries=[],
        children_abstracts=children,
    )

    assert overview == "overview-3"
    assert len(vlm.prompts) == 3
    assert "child-0" in vlm.prompts[0]
    assert "child-1" in vlm.prompts[0]
    assert "child-2" not in vlm.prompts[0]
    assert "child-2" in vlm.prompts[1]
    assert all(f"child-{index}" not in vlm.prompts[2] for index in range(3))


@pytest.mark.asyncio
async def test_sampled_overview_prompt_describes_full_directory_coverage(monkeypatch):
    vlm = RecordingVLM()
    config = SimpleNamespace(
        vlm=vlm,
        semantic=SimpleNamespace(
            max_overview_prompt_chars=10_000,
            overview_batch_size=32,
        ),
        output_language_override="en",
    )
    captured = {}
    monkeypatch.setattr(
        semantic_processor_module,
        "get_openviking_config",
        lambda: config,
    )

    def fake_render_prompt(_name, values):
        captured.update(values)
        return "prompt"

    monkeypatch.setattr(semantic_processor_module, "render_prompt", fake_render_prompt)

    await SemanticProcessor(vlm_resolver=_TestVLMResolver(vlm))._generate_overview(
        "viking://resources/docs_flat",
        file_summaries=[],
        children_abstracts=[{"name": "sample", "abstract": "summary"}],
        total_files=0,
        total_children=513,
    )

    coverage = captured["directory_coverage"]
    assert "Total direct entries: 513" in coverage
    assert "Summaries provided for this aggregation: 1" in coverage
    assert "Direct entries not individually shown: 512" in coverage
    assert "Coverage: sampled" in coverage


@pytest.mark.asyncio
async def test_batched_merge_resolves_placeholders_from_merge_output(monkeypatch):
    vlm = MergePlaceholderVLM()
    config = SimpleNamespace(
        vlm=vlm,
        semantic=SimpleNamespace(
            max_overview_prompt_chars=1,
            overview_batch_size=1,
        ),
        output_language_override="en",
    )
    monkeypatch.setattr(
        semantic_processor_module,
        "get_openviking_config",
        lambda: config,
    )
    monkeypatch.setattr(
        semantic_processor_module,
        "render_prompt",
        lambda _name, values: values["file_summaries"],
    )

    overview = await SemanticProcessor(vlm_resolver=_TestVLMResolver(vlm))._generate_overview(
        "viking://resources/业务 docs",
        file_summaries=[
            {"name": "first file.md", "summary": "first summary"},
            {"name": "第二章#file.md", "summary": "second summary"},
        ],
        children_abstracts=[],
    )

    assert overview == (
        "[first](viking://resources/业务%20docs/first%20file.md) and "
        "[second](viking://resources/业务%20docs/第二章%23file.md)"
    )
    assert "viking://input_sample_" not in overview
