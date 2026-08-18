# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.prompts.manager import PromptManager


@pytest.mark.parametrize(
    "prompt_id",
    [
        "retrieval.ov_intent_analysis_sft_v4",
        "retrieval.ov_intent_analysis_sft_v7",
    ],
)
def test_sft_intent_prompts_use_compact_queries_contract(prompt_id: str):
    manager = PromptManager(templates_dir=PromptManager._get_bundled_templates_dir())
    rendered = manager.render(
        prompt_id,
        {
            "compression_summary": "The user is working on OpenViking retrieval.",
            "recent_messages": "[user]: Improve semantic search.",
            "current_message": "Use the style we discussed earlier.",
            "context_type": "",
            "target_abstract": "",
        },
    )

    output_contract = rendered.split("## Output Format", maxsplit=1)[1]

    assert "queries" in output_contract
    assert "query" in output_contract
    assert "context_type" in output_contract
    assert "priority" in output_contract
    assert '"reasoning"' not in output_contract
    assert '"intent"' not in output_contract
