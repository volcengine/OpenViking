# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_ops.directory_generation import (
    generate_directory_semantics,
)
from openviking_cli.utils.config.parser_config import SemanticConfig


@pytest.mark.asyncio
async def test_oversized_directory_material_is_batched_before_model_calls():
    complete = AsyncMock(side_effect=["x" * 2000] * 3 + ["Merged summary."])
    config = SemanticConfig(max_overview_prompt_chars=2000, overview_batch_size=50)
    result = await generate_directory_semantics(
        "viking://resources/root",
        {f"child-{i}": {"kind": "directory", "summary": str(i) * 1000} for i in range(3)},
        previous=None,
        total_entries=3,
        config=config,
        language="en",
        complete=complete,
    )
    prompts = [call.args[0] for call in complete.await_args_list]
    assert len(prompts) == 4
    assert all(len(prompt) <= config.max_overview_prompt_chars for prompt in prompts)
    assert all(f"child-{i}" in prompts[i] for i in range(3))
    assert all(f"child-{i}" not in prompts[-1] for i in range(3))
    assert result.abstract == "Merged summary."


@pytest.mark.asyncio
async def test_single_oversized_node_fails_before_sending_an_oversized_prompt():
    complete = AsyncMock()
    with pytest.raises(ValueError, match="input budget"):
        await generate_directory_semantics(
            "viking://resources/root",
            {"large": {"kind": "directory", "summary": "x" * 3000}},
            previous=None,
            total_entries=1,
            config=SemanticConfig(max_overview_prompt_chars=2000),
            language="en",
            complete=complete,
        )
    complete.assert_not_awaited()
