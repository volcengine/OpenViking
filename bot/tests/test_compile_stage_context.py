"""Frozen stage rules and cross-purpose comparison in the Markdown pipeline."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_compile_markdown import MERGE, contract, sources
from test_compile_markdown import agent_loop as _agent_loop
from test_compile_markdown import pipeline as _pipeline
from vikingbot.compile.models import COMPILE_DRAFT_ROOT, CompileFailure
from vikingbot.compile.ops.shuffle import Shuffle, group_related_documents
from vikingbot.compile.plan import parse_plan
from vikingbot.compile.results import Document, RouteResponse

from openviking_cli.exceptions import NotFoundError

pipeline = _pipeline
agent_loop = _agent_loop
ASSETS = (
    Path(__file__).resolve().parents[2] / "openviking/system_assets/compile/example-skills/llm-wiki"
)
SKILL = """---
name: synthetic
compile_stage_resources:
  map: [references/common.md, references/map.md]
  reduce: [references/common.md, references/final.md]
---
Keep all facts and citations.
"""


def attach(pipeline):
    values = {
        "references/common.md": "COMMON RULES",
        "references/map.md": "MAP RULES",
        "references/final.md": "FINAL RECORD SCHEMA",
    }
    pipeline.client.stat = AsyncMock(return_value={"size": 0})
    pipeline.client.download_bytes = AsyncMock(
        side_effect=lambda uri: values[uri.split("/synthetic/")[-1]].encode()
    )
    pipeline.resources.root = "viking://agent/skills/synthetic"
    return values


async def test_rule_snapshots_are_authorized_deduplicated_and_stage_scoped(pipeline):
    attach(pipeline)
    await pipeline.resources.prepare_stages(SKILL)
    await pipeline.resources.prepare_stages(SKILL)
    assert pipeline.client.download_bytes.await_count == 3
    map_rules = pipeline.resources.stage_context("map")
    reduce_rules = pipeline.resources.stage_context("reduce")
    assert "COMMON RULES" in map_rules and "MAP RULES" in map_rules
    assert "FINAL RECORD SCHEMA" not in map_rules
    assert "COMMON RULES" in reduce_rules and "FINAL RECORD SCHEMA" in reduce_rules
    assert "MAP RULES" not in reduce_rules
    # Later cache mutation cannot alter this run's frozen rule text.
    pipeline.resources.snapshots["references/common.md"] = "CHANGED"
    assert "CHANGED" not in pipeline.resources.stage_context("map")
    assert len((await pipeline.files.get("skill-dependencies"))) == 3


@pytest.mark.parametrize(
    "declaration",
    [
        "map: ../outside.md",
        "unknown: []",
        "map: [../../outside.md]",
        "map: [42]",
        "map: [viking://agent/skills/another/rules.md]",
        'map: ["%2e%2e/outside.md"]',
    ],
)
async def test_invalid_stage_declarations_fail_before_io(pipeline, declaration):
    with pytest.raises(ValueError):
        await pipeline.resources.prepare_stages(
            "---\ncompile_stage_resources:\n  " + declaration + "\n---\nSkill"
        )
    pipeline.client.download_bytes.assert_not_awaited()


async def test_missing_required_rule_fails_before_any_planning_or_publication(pipeline):
    pipeline.skill = SKILL
    pipeline.model.ask = AsyncMock()
    with pytest.raises(CompileFailure) as raised:
        await pipeline.run(sources())
    assert isinstance(raised.value.__cause__, NotFoundError)
    pipeline.model.ask.assert_not_awaited()
    pipeline.client.batch_write.assert_not_awaited()


async def test_legacy_skill_does_not_load_or_inject_rules(pipeline):
    await pipeline.resources.prepare_stages("Ordinary Skill without stage declarations.")
    assert pipeline.resources.stage_context("map") == ""
    assert pipeline.resources.stage_context(None) == ""
    pipeline.client.download_bytes.assert_not_awaited()


async def test_actual_generation_receives_only_its_frozen_skill_rules(pipeline):
    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    attach(pipeline)
    await pipeline.resources.prepare_stages(SKILL)
    seen = []

    async def run(system, data, model, **kwargs):
        seen.append(system)
        root = COMPILE_DRAFT_ROOT + "/stage-test/output"
        await pipeline.files.sandbox.write_file(root + "/topic.md", "# Topic\n\nSource facts.")
        return root

    pipeline.model.agent_runner = run
    await pipeline.generate("arbitrary-node-name", "Draft", {}, [], skill_stage="map")
    await pipeline.generate("another-node", "Finalize", {}, [], skill_stage="reduce")
    assert "MAP RULES" in seen[0] and "FINAL RECORD SCHEMA" not in seen[0]
    assert "FINAL RECORD SCHEMA" in seen[1] and "MAP RULES" not in seen[1]


def test_builtin_skill_keeps_final_schema_and_matches_worker_tools():
    from vikingbot.compile.renderer import _split_frontmatter

    skill = (ASSETS / "SKILL.md").read_text()
    metadata, _ = _split_frontmatter(skill)
    assert metadata["compile_stage_resources"]["reduce"] == ["references/conflicts.md"]
    assert "In an isolated Compile Markdown worker, use its provided `write_file`" in skill
    final = (ASSETS / "references/conflicts.md").read_text()
    for field in (
        "match_text",
        "problem_description",
        "reason",
        "severity",
        "state",
        "begin_time",
        "fix_plan",
        "fix_time",
        "resolution_basis",
        "conflict_type",
        "resolved_by",
    ):
        assert "`" + field + "`" in final
    assert "用户手动解决内容冲突，修正后的内容如正文所示" in final
    assert "人工修复" in final


async def test_shuffle_preserves_hub_comparison_and_selected_history(pipeline, monkeypatch):
    pipeline.contract = contract(True)
    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    docs = [Document(str(i) + ".md", "drafts/" + str(i) + ".md", [str(i)]) for i in range(4)]
    for doc in docs:
        await pipeline.files.sandbox.write_file(
            doc.workspace_path,
            "# Shared subject\n\nDifferent reader purposes.\n<!-- CONFLICTS_CONTENT HIDDEN_DISPUTE -->",
        )
    monkeypatch.setattr(Shuffle, "vectors", AsyncMock(return_value=[[1.0, 0.0]] * 4))

    async def decide(stage, system, data, response_type, validate, **kwargs):
        assert "different reader purposes" in system
        assert all("HIDDEN_DISPUTE" not in d["markdown"] for d in data["documents"])
        for d in data["documents"]:
            assert "HIDDEN_DISPUTE" not in await kwargs["readers"]["read_document"].execute(
                d["document"]
            )
        choices = []
        for item in data["assignments"]:
            document = item["document"]
            related = (
                [d.workspace_path for d in docs[1:]]
                if document == docs[0].workspace_path
                else [docs[0].workspace_path]
            )
            choices.append({"document": document, "related": related, "history": []})
        result = RouteResponse.model_validate({"decisions": choices})
        validate(result)
        return result

    pipeline.model.ask = decide
    groups = await Shuffle(pipeline).run(parse_plan(MERGE, pipeline.contract)[1], docs)
    assert len(groups) == 1 and {d.workspace_path for d in groups[0].documents} == {
        d.workspace_path for d in docs
    }
    assert {r for d in groups[0].documents for r in d.source_refs} == {"0", "1", "2", "3"}


@pytest.mark.parametrize(
    "keys,links",
    [
        (list("abcd"), [("a", "b"), ("a", "c"), ("a", "d")]),
        (list("abcd"), [("b", "a"), ("c", "a"), ("d", "a")]),
        (list("abcd"), [("a", "b"), ("b", "a"), ("a", "c"), ("a", "d")]),
        (list("abcde"), [("a", "b"), ("a", "c"), ("a", "d")]),
        (list("abcd"), [("a", "b"), ("a", "c"), ("b", "c"), ("a", "d")]),
    ],
)
def test_centered_groups_keep_confirmed_comparisons_without_duplicate_inputs(keys, links):
    groups = group_related_documents(keys, links, centered=True)
    assert set(groups[0]) == set("abcd")
    assert len([k for g in groups for k in g]) == len(set(keys))
    assert group_related_documents(keys, links) == group_related_documents(
        keys, links, centered=False
    )


def test_comparison_group_is_bounded_and_does_not_close_transitive_chains():
    keys = ["hub", *[str(i) for i in range(70)]]
    groups = group_related_documents(keys, [("hub", k) for k in keys[1:]], centered=True)
    assert max(map(len, groups)) == 50
    assert sorted(k for g in groups for k in g) == sorted(keys)
    groups = group_related_documents(
        list("abcde"), [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")], centered=True
    )
    assert not any("a" in g and "e" in g for g in groups)


async def test_visibility_projection_leaves_raw_draft_available_to_reduce(pipeline):
    import json

    from vikingbot.compile.file_ops import DocumentReader

    doc = Document("topic.md", "drafts/topic.md")
    raw = "# Topic\n\nCommon facts.\n<!-- CONFLICTS_CONTENT private evidence -->\n"
    await pipeline.files.sandbox.write_file(doc.workspace_path, raw)
    visible = json.loads(
        await DocumentReader(pipeline, [doc], visible_only=True).execute(doc.workspace_path)
    )
    original = json.loads(await DocumentReader(pipeline, [doc]).execute(doc.workspace_path))
    assert "private evidence" not in visible["text"]
    assert original["text"] == raw
    assert visible["total_chars"] == len(visible["text"]) and visible["complete"] is True


async def test_ordinary_map_keeps_final_rules_if_reduce_can_be_skipped(pipeline):
    attach(pipeline)
    await pipeline.resources.prepare_stages(SKILL)
    captured = []

    async def run(system, data, model, **kwargs):
        captured.append((system, data))
        root = COMPILE_DRAFT_ROOT + "/ordinary-map/output"
        await pipeline.files.sandbox.write_file(root + "/topic.md", "# Topic\n\nFacts.")
        return root

    pipeline.model.agent_runner = run
    await pipeline.generate("custom-map", "Write final pages", {}, [], skill_stage="map")
    assert "MAP RULES" in captured[0][0] and "FINAL RECORD SCHEMA" in captured[0][0]
    context = captured[0][1]["runtime"]["skill_context"]
    assert len(context["skill_attachment_paths"]) == 3
    assert context["skill_attachment_chars"] == len("COMMON RULESMAP RULESFINAL RECORD SCHEMA")


async def test_reduce_preserves_planned_work_and_review_revisions(pipeline):
    from types import SimpleNamespace

    from vikingbot.compile.ops import reduce
    from vikingbot.compile.results import Group

    pipeline.contract = contract(True)
    planned = "Preserve approval exceptions and separate procedures from fees."
    revised = planned + " Retain the cancellation branch identified by stage review."
    pipeline.generate = AsyncMock(return_value=[])
    group = Group("group", [Document("topic.md", "drafts/topic.md", ["source"])])
    for instructions in (planned, revised):
        pipeline.contract.reduce.instructions = instructions
        await reduce.run(pipeline, SimpleNamespace(name="merge", task="reduce"), [group])
        call = pipeline.generate.await_args
        assert instructions in call.args[1] and call.kwargs["skill_stage"] == "reduce"
    assert pipeline.generate.await_count == 2


async def test_actual_agent_calls_record_injected_rules_not_every_loaded_attachment(
    pipeline, agent_loop
):
    from vikingbot.compile.pipeline_agent import agent_runner
    from vikingbot.providers.base import LLMResponse, ToolCallRequest

    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    attach(pipeline)
    await pipeline.resources.prepare_stages(SKILL)
    loop, key, _ = agent_loop
    pipeline.model.provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        "write",
                        "write_file",
                        {"path": "topic.md", "content": "# Topic\n\nFacts."},
                        0,
                    )
                ],
            ),
            LLMResponse(content="Done", finish_reason="stop"),
        ]
    )
    pipeline.model.agent_runner = agent_runner(loop, key, None, pipeline.limits)
    await pipeline.generate("map-receipt", "Draft source facts.", {}, [], skill_stage="map")
    for number in [1, 2]:
        receipt = await pipeline.files.get(f"calls/map-receipt-{number}")
        assert receipt["skill_attachment_paths"] == ["references/common.md", "references/map.md"]
        assert receipt["skill_attachment_chars"] == len("COMMON RULESMAP RULES")
        assert receipt["skill_context_policy"] == "staged-rules-v1"
    assert len(pipeline.resources.hashes) == 3
