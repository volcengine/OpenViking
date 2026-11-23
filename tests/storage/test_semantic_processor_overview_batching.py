# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_ops.directory_generation import (
    generate_directory_semantics,
)
from openviking_cli.utils.config.parser_config import SemanticConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [2_000, 60_000])
async def test_directory_batches_only_when_both_limits_are_exceeded(budget):
    responses = ["First batch.", "Second batch.", "Summary."] if budget == 2_000 else ["Summary."]
    complete = AsyncMock(side_effect=responses)
    config = SemanticConfig(max_overview_prompt_chars=budget, overview_batch_size=2)
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
    if budget == 2_000:
        first, second, merged = prompts
        assert "child-0" in first and "child-1" in first and "child-2" not in first
        assert "child-2" in second and "child-0" not in second and "child-1" not in second
        assert "First batch." in merged and "Second batch." in merged
        assert all(f"child-{i}" not in merged for i in range(3))
    else:
        (prompt,) = prompts
        assert all(f"child-{i}" in prompt for i in range(3))
    assert result.abstract == "Summary."


@pytest.mark.asyncio
async def test_single_oversized_file_summary_is_truncated_before_summarizing():
    complete = AsyncMock(return_value="Guide to local setup.")
    result = await generate_directory_semantics(
        "viking://resources/root",
        {"guide.md": {"kind": "file", "summary": "x" * 4_000 + "y" * 800}},
        previous=None,
        total_entries=1,
        config=SemanticConfig(max_overview_prompt_chars=4_000),
        language="en",
        complete=complete,
    )
    complete.assert_awaited_once()
    prompt = complete.await_args.args[0]
    assert "x" * 4_000 in prompt
    assert "y" * 800 not in prompt
    assert result.abstract == "Guide to local setup."
    assert "[guide.md]" in result.overview
