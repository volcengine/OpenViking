"""Assigned drafts can become Reduce outputs without the model copying their bodies."""

# ruff: noqa: F811 - imported shared fixtures are reused as parameters.

import json
from unittest.mock import AsyncMock

import pytest
from test_compile_markdown import MERGE, agent_loop, contract, pipeline  # noqa: F401
from vikingbot.compile.ops import finalize, reduce
from vikingbot.compile.pipeline_agent import agent_runner
from vikingbot.compile.plan import parse_plan
from vikingbot.compile.results import Document, Group
from vikingbot.providers.base import LLMResponse, ToolCallRequest


@pytest.mark.parametrize("index", range(5))
async def test_reduce_reuses_assigned_unchanged_draft(pipeline, agent_loop, index):
    """Finish the real stage and compare publication with normal model writes."""
    draft = "# Limits\r\n\r\n" + ("Scope, version, and exact evidence. 数据。\r\n" * (40 + index))
    path = "topics/limits.md"
    refs = ["source-a"]
    await pipeline.files.sandbox.write_file("drafts/limits.md", draft)
    group = Group("limits", [Document(path, "drafts/limits.md", refs)])
    pipeline.contract = contract(True)
    node = parse_plan(MERGE, pipeline.contract)[2]
    loop, key, _ = agent_loop
    pipeline.model.agent_runner = agent_runner(loop, key, None, pipeline.limits)
    calls = []

    async def respond(**kwargs):
        turn = len(calls) % 3
        names = {tool["function"]["name"] for tool in kwargs["tools"]}
        if turn == 0:
            name, arguments = "read_file", {"path": "inputs/0.md"}
        elif turn == 1:
            if len(calls) >= 3 and "reuse_draft" in names:
                name, arguments = "reuse_draft", {"content_file": "inputs/0.md"}
            else:
                name, arguments = "write_file", {"path": path, "content": draft}
        else:
            calls.append(("finish", {}))
            return LLMResponse(content="Complete", finish_reason="stop")
        calls.append((name, arguments))
        return LLMResponse(content="", tool_calls=[ToolCallRequest(name, name, arguments, 0)])

    pipeline.model.provider.chat = AsyncMock(side_effect=respond)
    before = await reduce.run(pipeline, node, [group])
    after = await reduce.run(pipeline, node, [group])
    original = await finalize.run(pipeline, before)
    reused = await finalize.run(pipeline, after)
    assert (
        original.operations
        == reused.operations
        == [{"uri": pipeline.target + "/" + path, "content": draft, "mode": "create"}]
    )
    assert before[0].source_refs == after[0].source_refs == refs
    assert len(calls) == 6
    assert calls[4] == ("reuse_draft", {"content_file": "inputs/0.md"})
    assert len(json.dumps(calls[4][1], ensure_ascii=False).encode()) < len(
        json.dumps(calls[1][1], ensure_ascii=False).encode()
    )


async def test_reduce_cannot_reuse_an_unassigned_path(pipeline, agent_loop):
    sandbox = pipeline.files.sandbox
    await sandbox.write_file("drafts/assigned.md", "# Assigned")
    await sandbox.write_file("drafts/other.md", "# Other")
    group = Group("test", [Document("topic.md", "drafts/assigned.md", ["source-a"])])
    pipeline.contract = contract(True)
    node = parse_plan(MERGE, pipeline.contract)[2]
    loop, key, _ = agent_loop
    pipeline.model.agent_runner = agent_runner(loop, key, None, pipeline.limits)
    pipeline.model.provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("reuse", "reuse_draft", {"content_file": "drafts/other.md"}, 0)
                ],
            ),
            LLMResponse(content="Complete", finish_reason="stop"),
        ]
    )
    assert await reduce.run(pipeline, node, [group]) == []
    assert pipeline.status["source-a"] == "failed"
    assert await sandbox.read_file("drafts/other.md") == "# Other"
