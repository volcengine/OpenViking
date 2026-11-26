# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_ops.directory_generation import (
    generate_directory_semantics,
)
from openviking_cli.utils.config.parser_config import SemanticConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget", "responses", "expected"),
    [
        (60_000, ("Summary.",), "Summary."),
        (2_000, ("First batch.", "Second batch.", "Summary."), "Summary."),
        (2_000, (None, "Second batch."), "Second batch."),
        (2_000, (None, None), "[Directory overview is not generated]"),
        (2_000, ("First batch.", "Second batch.", None), "First batch."),
    ],
)
async def test_directory_batch_generation_retains_successful_summaries(budget, responses, expected):
    remaining = iter(responses)
    batches_started = asyncio.Event()

    async def respond(_prompt):
        response = next(remaining)
        call_count = complete.await_count
        if budget == 2_000 and call_count <= 2:
            if call_count == 2:
                batches_started.set()
            await asyncio.wait_for(batches_started.wait(), timeout=1)
        if response is None:
            raise RuntimeError("model call failed")
        return response

    complete = AsyncMock(side_effect=respond)
    config = SemanticConfig(max_overview_prompt_chars=budget, overview_batch_size=2)
    result = await generate_directory_semantics(
        "viking://resources/root",
        {f"child-{i}": {"kind": "directory", "summary": str(i) * 1000} for i in range(3)},
        previous=None,
        total_entries=3,
        config=config,
        language="en",
        complete=complete,
        max_concurrent_llm=2,
    )
    prompts = [call.args[0] for call in complete.await_args_list]
    assert len(prompts) == len(responses)
    if budget == 2_000:
        first, second, *merge = prompts
        assert "child-0" in first and "child-1" in first and "child-2" not in first
        assert "child-2" in second and "child-0" not in second and "child-1" not in second
        if merge:
            (merged,) = merge
            assert "First batch." in merged and "Second batch." in merged
            assert all(f"child-{i}" not in merged for i in range(3))
    else:
        (prompt,) = prompts
        assert all(f"child-{i}" in prompt for i in range(3))
    assert result.abstract == expected
    assert expected in result.overview


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
        max_concurrent_llm=2,
    )
    complete.assert_awaited_once()
    prompt = complete.await_args.args[0]
    assert "x" * 4_000 in prompt
    assert "y" * 800 not in prompt
    assert result.abstract == "Guide to local setup."
    assert "[guide.md]" in result.overview
