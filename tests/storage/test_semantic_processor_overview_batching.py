# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

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

    overview = await SemanticProcessor()._generate_overview(
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
