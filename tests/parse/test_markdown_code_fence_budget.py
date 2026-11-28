# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Character-budget splitting must keep fenced code blocks in one file."""

from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig

FENCE = "~~~python\nfirst_sample = 1\n\nnext_sample = 2\n~~~"


async def _writes(content: str, max_chars: int = 120) -> list[str]:
    parser = MarkdownParser(config=ParserConfig(max_section_chars=max_chars))
    layout = await parser._compute_layout(
        content,
        "viking://temp/fence",
        source_path="/tmp/fence.md",
        resource_name="fence",
        split_content=True,
    )
    return [op.content for op in layout.ops if op.kind == "write"]


def _fence_lines(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.strip().startswith(("```", "~~~")))


async def test_blank_line_inside_fitting_fence_is_not_a_paragraph_boundary() -> None:
    prose = "intro paragraph " + "x" * 68  # 86 chars, keeps the fence over budget with it
    content = f"{prose}\n\n{FENCE}"

    writes = await _writes(content)

    assert len(writes) == 2
    assert all(len(part) <= 120 for part in writes)
    # The whole fence lands in a single file, opener and closer together.
    assert any(FENCE in part for part in writes)
    assert all(_fence_lines(part) % 2 == 0 for part in writes)


async def test_raw_fallback_does_not_cut_a_fence_adjacent_to_prose() -> None:
    # No blank line between prose and the fence, and the block is over budget,
    # so the raw character fallback would previously slice through the fence.
    content = "intro " + "y" * 130 + "\n```js\nconst a = 1;\n```"

    writes = await _writes(content)

    assert any(_fence_lines(part) == 2 for part in writes)
    joined = "\n".join(writes)
    assert "```js" in joined and "const a = 1;" in joined
