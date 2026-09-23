# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace

import pytest

from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", [None, "batch", "merge"])
async def test_children_only_oversized_overview_is_batched(monkeypatch, failure_stage):
    vlm = RecordingVLM()
    error = RuntimeError("500 model unavailable")
    pending_started = asyncio.Event()
    pending_stopped = asyncio.Event()
    complete = vlm.get_completion_async

    async def fail_completion(prompt):
        if failure_stage == "batch":
            if "child-0" in prompt:
                await pending_started.wait()
                raise error
            pending_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                pending_stopped.set()
        if failure_stage == "merge" and "overview-1" in prompt:
            raise error
        return await complete(prompt)

    vlm.get_completion_async = fail_completion
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

    generation = SemanticProcessor()._generate_overview(
        "viking://resources/root",
        file_summaries=[],
        children_abstracts=children,
    )
    if failure_stage is not None:
        with pytest.raises(RuntimeError) as exc_info:
            await asyncio.wait_for(generation, timeout=2)
        assert exc_info.value is error
        if failure_stage == "batch":
            assert pending_stopped.is_set()
        return
    overview = await generation

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

    await SemanticProcessor()._generate_overview(
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

    overview = await SemanticProcessor()._generate_overview(
        "viking://resources/product docs",
        file_summaries=[
            {"name": "first file.md", "summary": "first summary"},
            {"name": "second#file.md", "summary": "second summary"},
        ],
        children_abstracts=[],
    )

    assert overview == (
        "[first](viking://resources/product%20docs/first%20file.md) and "
        "[second](viking://resources/product%20docs/second%23file.md)"
    )
    assert "viking://input_sample_" not in overview
