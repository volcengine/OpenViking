"""Reuse only byte-identical completed Markdown at the path-collision boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.ops import finalize
from vikingbot.compile.ops.reduce import resolve_files
from vikingbot.compile.results import Document

from bot.tests.test_compile_markdown import pipeline as pipeline


async def candidates(pipeline, bodies, *, path="topic.md"):
    documents = []
    for index, body in enumerate(bodies):
        workspace = f"__compile_staging__/drafts/completed-{index}/output/{path}"
        await pipeline.files.sandbox.write_file(workspace, body)
        documents.append(Document(path, workspace, [f"range-{index}"]))
    return documents


async def test_equal_completed_outputs_keep_exact_bytes_and_union_assignment_refs(pipeline):
    body = "# Schedule\n\nValidity: 5 days.\n\n[Source](viking://resources/source/a.md)\n"
    documents = await candidates(pipeline, [body, body, body])
    pipeline.generate = AsyncMock(side_effect=AssertionError("No synthesis is needed"))
    result = await resolve_files(pipeline, documents)
    assert len(result) == 1
    assert result[0].workspace_path == documents[0].workspace_path
    assert result[0].source_refs == ["range-0", "range-1", "range-2"]
    assert documents[0].source_refs == ["range-0"]
    bundle = await finalize.run(pipeline, result)
    assert bundle.operations[0]["content"] == body
    assert bundle.operations[0]["mode"] == "create"
    assert (await pipeline.files.get("finalize"))["outputs"]["topic.md"]["source_refs"] == result[
        0
    ].source_refs
    assert pipeline.metrics["identical_collision_reuses"] == 1
    pipeline.generate.assert_not_awaited()


@pytest.mark.parametrize(
    "bodies",
    [
        ("# Rule\n\n5 days\n", "# Rule\n\n6 days\n"),
        ("# Rule\n\nversion A\n", "# Rule\n\nversion B\n"),
        (
            "# Rule\n\n[Source](viking://resources/source/a.md)\n",
            "# Rule\n\n[Source](viking://resources/source/b.md)\n",
        ),
        ("# Rule\n", "# Rule\r\n"),
        ("", ""),
        ("  \n", "  \n"),
    ],
)
async def test_differences_or_empty_candidates_still_use_collision_worker(pipeline, bodies):
    documents = await candidates(pipeline, bodies)
    repaired = Document("topic.md", "resolved.md", ["range-0", "range-1"])
    pipeline.generate = AsyncMock(return_value=[repaired])
    assert await resolve_files(pipeline, documents) == [repaired]
    pipeline.generate.assert_awaited_once()
    assert pipeline.metrics["identical_collision_reuses"] == 0
