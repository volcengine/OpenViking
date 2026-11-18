"""Frozen stage rules and cross-purpose comparison in the Markdown pipeline."""

from unittest.mock import AsyncMock

import pytest
from test_compile_markdown import MERGE, contract
from test_compile_markdown import agent_loop as _agent_loop
from test_compile_markdown import pipeline as _pipeline
from vikingbot.compile.conflict_rules import (
    BUNDLED_WIKI_URI,
    MAP_CONFLICT_RULES,
    REDUCE_CONFLICT_RULES,
    WIKI_WORKER_RULES,
)
from vikingbot.compile.conflict_visibility import without_conflict_comments
from vikingbot.compile.models import COMPILE_DRAFT_ROOT
from vikingbot.compile.ops.shuffle import Shuffle, group_related_documents
from vikingbot.compile.pipeline import Pipeline
from vikingbot.compile.plan import parse_plan
from vikingbot.compile.results import Document, RouteResponse

pipeline = _pipeline
agent_loop = _agent_loop


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("builtin", [False, True])
async def test_only_opted_in_workers_receive_stage_rules(pipeline, enabled, builtin):
    request = pipeline.request.model_copy(
        update={
            "skill": BUNDLED_WIKI_URI if builtin else pipeline.request.skill,
            "global_conflict_detection": enabled,
        }
    )
    run = Pipeline(
        client=pipeline.client,
        sandbox=pipeline.files.sandbox,
        provider=pipeline.model.provider,
        model="synthetic",
        temperature=0,
        limits=pipeline.limits,
        request=request,
        skill="Original Skill text.",
        usage={},
    )
    run.timestamp = pipeline.timestamp
    seen = []

    async def worker(system, data, model, **kwargs):
        seen.append((system, data["runtime"]))
        root = COMPILE_DRAFT_ROOT + "/stage-test/output"
        await run.files.sandbox.write_file(root + "/topic.md", "# Topic\n\nSource facts.")
        return root

    run.model.agent_runner = worker
    await run.generate("draft", "Draft evidence", {}, [], skill_stage="map")
    await run.generate("merge", "Merge evidence", {}, [], skill_stage="reduce")
    for (prompt, context), rules, stage in zip(
        seen, (MAP_CONFLICT_RULES, REDUCE_CONFLICT_RULES), ("map", "reduce"), strict=True
    ):
        assert ("Original Skill text." in prompt) is (not builtin or not enabled)
        assert (WIKI_WORKER_RULES in prompt) is (builtin and enabled)
        assert (rules in prompt) is enabled
        assert ("| `match_text` |" in prompt) is (enabled and stage == "reduce")
        if enabled:
            assert context["skill_context"]["conflict_rule_stage"] == stage
        else:
            assert context == {"time": run.timestamp}
    pipeline.client.download_bytes.assert_not_awaited()


@pytest.mark.parametrize("enabled", [False, True])
async def test_shuffle_scopes_comparison_and_preserves_selected_history(
    pipeline, monkeypatch, enabled
):
    pipeline.contract = contract(True)
    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": enabled})
    docs = [Document(str(i) + ".md", "drafts/" + str(i) + ".md", [str(i)]) for i in range(4)]
    for doc in docs:
        await pipeline.files.sandbox.write_file(
            doc.workspace_path,
            "# Shared subject\n\nDifferent reader purposes.\n<!-- CONFLICTS_CONTENT HIDDEN_DISPUTE -->",
        )
    monkeypatch.setattr(Shuffle, "vectors", AsyncMock(return_value=[[1.0, 0.0]] * 4))

    history_uri = pipeline.target + "/old.md"
    monkeypatch.setattr(Shuffle, "candidates", AsyncMock(return_value=[{"uri": history_uri}]))

    async def decide(stage, system, data, response_type, validate, **kwargs):
        assert ("different reader purposes" in system) is enabled
        for d in data["documents"]:
            assert ("HIDDEN_DISPUTE" in d["markdown"]) is not enabled
            body = await kwargs["readers"]["read_document"].execute(d["document"])
            assert ("HIDDEN_DISPUTE" in body) is not enabled
        choices = []
        for item in data["assignments"]:
            document = item["document"]
            related = (
                [d.workspace_path for d in docs[1:]]
                if document == docs[0].workspace_path
                else [docs[0].workspace_path]
            )
            choices.append(
                {
                    "document": document,
                    "related": related,
                    "history": [history_uri] if document == docs[0].workspace_path else [],
                }
            )
        result = RouteResponse.model_validate({"decisions": choices})
        validate(result)
        return result

    pipeline.model.ask = decide
    from dataclasses import replace

    node = replace(parse_plan(MERGE, pipeline.contract)[1], against_target=True)
    groups = await Shuffle(pipeline).run(node, docs)
    assert groups[0].target_uris == [history_uri]
    assert len(groups) == (1 if enabled else 3)
    assert {d.workspace_path for group in groups for d in group.documents} == {
        d.workspace_path for d in docs
    }
    assert {r for group in groups for d in group.documents for r in d.source_refs} == {
        "0",
        "1",
        "2",
        "3",
    }


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


async def test_actual_agent_calls_report_the_selected_conflict_rules(pipeline, agent_loop):
    from vikingbot.compile.pipeline_agent import agent_runner
    from vikingbot.providers.base import LLMResponse, ToolCallRequest

    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
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
        assert receipt["conflict_rule_stage"] == "map"
        assert receipt["conflict_rule_chars"] == len(MAP_CONFLICT_RULES)


def test_centered_comparison_accepts_iterable_document_ids():
    assert group_related_documents(iter("abc"), [("a", "b"), ("a", "c")], centered=True) == [
        ["a", "b", "c"]
    ]


async def test_opt_in_smoke_supplies_public_final_rules_without_model_calls(
    pipeline, agent_loop, tmp_path, monkeypatch
):
    import json

    from test_scripts import compile_conflict_smoke as smoke

    config = tmp_path / "synthetic.json"
    config.write_text(json.dumps({"model": "deepseek-synthetic", "api_key": "synthetic-not-a-key"}))
    monkeypatch.setenv("OV_TEST_VLM_CONFIG", str(config))
    monkeypatch.setattr(smoke, "OpenAIVLM", lambda cfg: object())
    monkeypatch.setattr(
        smoke, "VLMProviderAdapter", lambda *args, **kwargs: pipeline.model.provider
    )
    seen = []

    class StopProbe(Exception):
        pass

    async def runner(system, *args, **kwargs):
        seen.append(system)
        raise StopProbe("No model request is made")

    monkeypatch.setattr(smoke, "agent_runner", lambda *args, **kwargs: runner)
    with pytest.raises(StopProbe):
        await smoke.test_real_worker_comparison(pipeline, agent_loop, "compatible")
    assert seen
    assert "| `match_text` |" in seen[0]
    assert "用户手动解决内容冲突，修正后的内容如正文所示" in seen[0]
    pipeline.client.download_bytes.assert_not_awaited()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("prefix", ["", "> ", "- "])
def test_real_inline_conflict_comment_is_hidden_and_code_literal_remains(newline, prefix):
    comment = "<!-- CONFLICTS_CONTENT evidence -->"
    text = f"{prefix}`{comment}` is syntax. {comment}\n"
    visible = without_conflict_comments(text.replace("\n", newline))
    assert visible.count(comment) == 1 and f"`{comment}`" in visible
