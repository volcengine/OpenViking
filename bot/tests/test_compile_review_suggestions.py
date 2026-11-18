"""Optional human-review guidance stays metadata, not a resolution or schema change."""

# ruff: noqa: F811 - shared isolated agent/runtime fixtures.

import json
from unittest.mock import AsyncMock

import pytest
from test_compile_markdown import _human_conflict_page, agent_loop, pipeline  # noqa: F401
from test_compile_stage_context import ASSETS
from vikingbot.compile.ops import finalize
from vikingbot.compile.pipeline_agent import agent_runner
from vikingbot.compile.results import Document
from vikingbot.providers.base import LLMResponse, ToolCallRequest


@pytest.mark.parametrize("with_suggestion", [True, False])
async def test_real_reduce_preserves_optional_advice_and_existing_human_decision(
    pipeline, agent_loop, with_suggestion
):
    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    pipeline.skill = (ASSETS / "SKILL.md").read_text()
    original = _human_conflict_page()
    retained = original.split("<!-- CONFLICTS_CONTENT\n", 1)[1].split("\n-->", 1)[0]
    anchor = "The secondary same-version sources disagree about the applicable retry interval."
    reason = "Synthetic source A section 1 says 3 seconds; source B section 2 says 5 under the same conditions."
    if with_suggestion:
        reason += "\nVerify the applicable version with authoritative evidence; if confirmed, update the retry table and its scope."
    unresolved = {
        "match_text": anchor,
        "problem_description": "Two intervals conflict in the same scope.",
        "reason": reason,
        "severity": "medium",
        "state": "unfixed",
        "begin_time": 1794900000,
        "fix_plan": "",
        "fix_time": None,
        "resolution_basis": "",
        "conflict_type": "fact",
        "resolved_by": "",
    }
    content = (
        "# Limits\n\nThe approved limit is 7.\n\n"
        + anchor
        + "\n\n<!-- CONFLICTS_CONTENT\n"
        + retained
        + ",\n"
        + json.dumps(unresolved)
        + "\n-->\n"
    )
    pipeline.client.stat = AsyncMock(return_value={"isDir": False})
    pipeline.client.download_bytes = AsyncMock(
        side_effect=lambda uri: (
            (ASSETS / uri.split("/wiki/", 1)[1]).read_bytes()
            if "/wiki/references/" in uri
            else original.encode()
        )
    )
    await pipeline.resources.prepare_stages(pipeline.skill)
    loop, key, _ = agent_loop
    pipeline.model.provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("read", "read_target_file", {"path": "topic.md"}, 0)],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        "write", "write_file", {"path": "topic.md", "content": content}, 0
                    )
                ],
            ),
            LLMResponse(content="Complete", finish_reason="stop"),
        ]
    )
    pipeline.model.agent_runner = agent_runner(loop, key, None, pipeline.limits)
    await pipeline.files.sandbox.write_file("prior.md", original)
    documents = await pipeline.generate(
        "reduce-review",
        "Preserve human decisions and compare the assigned evidence.",
        {"documents": [vars(Document("topic.md", "prior.md"))]},
        [],
        skill_stage="reduce",
    )
    bundle = await finalize.run(pipeline, documents)
    published = bundle.operations[0]["content"]
    assert published == content and retained in published
    records = json.loads(
        "[" + published.split("<!-- CONFLICTS_CONTENT\n", 1)[1].split("\n-->", 1)[0] + "]"
    )
    assert records[0] == json.loads(retained)
    assert records[1] == unresolved
    assert records[1]["state"] == "unfixed" and records[1]["fix_time"] is None
    assert all(records[1][field] == "" for field in ("fix_plan", "resolution_basis", "resolved_by"))
    receipt = await pipeline.files.get("calls/reduce-review-1")
    assert receipt["skill_attachment_paths"] == ["references/conflicts.md"]
    # Advice alone does not supply an actual repair/action/evidence contract.
    forged = published.replace('"state": "unfixed"', '"state": "fixed"')
    with pytest.raises(ValueError, match="resolution requires"):
        pipeline.validate_conflict_output("topic.md", forged)
