# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile execution recovery and exact planner feedback, without real model calls."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.tools.base import ToolContext
from vikingbot.compile import entry, file_ops
from vikingbot.compile.ops import common
from vikingbot.compile.ops.common import job
from vikingbot.compile.ops.reduce import reduce_group
from vikingbot.compile.pipeline_agent import agent_runner
from vikingbot.compile.pipeline_io import MAX_JOB_ATTEMPTS, ModelCallError
from vikingbot.compile.plan import DEFAULT_PLAN, Contract, Node, PlanProposal, Transform, parse_plan
from vikingbot.compile.results import (
    CombineResponse,
    FileDraft,
    FileResponse,
    Group,
    RecordResponse,
)
from vikingbot.config.schema import Config, SessionKey
from vikingbot.providers.base import LLMResponse, ToolCallRequest

from bot.tests.test_compile_sources import URI, seed
from bot.tests.test_compile_sources import runtime as runtime
from openviking.models.model_error import ModelError, ModelErrorKind


@pytest.mark.asyncio
async def test_compile_direct_output_exhaustion_falls_back_without_rejecting_entry_noop(runtime):
    pipeline, _ = runtime
    await seed(runtime)
    records = list(pipeline.records.values())
    pipeline.model.provider.chat.return_value = LLMResponse(content='{"files": []}')
    assert await entry.run(pipeline, records, {}) is None
    assert pipeline.model.provider.chat.await_count == 4
    assert pipeline.direct_output_format is None
    # The directory-plan entry owns its own no-op validation; ordinary file jobs cannot no-op.
    file_ops.validate_files(
        pipeline,
        entry.EntryResponse(
            files=[],
            needs_pipeline=False,
            output_format="files",
            no_changes_reason="No source changes",
        ),
        Group("entry", records),
        records,
        {},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recovers,bad_output",
    [
        (True, "no_emit"),
        (True, "missing_inputs"),
        (False, "missing_inputs"),
        (False, "foreign_inputs"),
    ],
)
async def test_compile_retries_exhausted_agent_output_without_accepting_invalid_records(
    runtime, recovers, bad_output
):
    pipeline, _ = runtime
    pipeline.files.sandbox = SimpleNamespace(write_file=AsyncMock())
    attempts = 0
    submissions = []

    def validate(result):
        if any(record.inputs != ["source"] for record in result.records):
            raise ValueError("Unknown source ID")

    async def run_child(**kwargs):
        nonlocal attempts
        attempts += 1
        submit = kwargs["tool_registry"].get("emit")
        submissions.append(submit)
        if recovers and attempts > 1:
            assert (
                await submit.execute(
                    ToolContext(), records=[{"inputs": ["source"], "payload": {"fact": "verified"}}]
                )
                == "Result accepted."
            )
            return
        if bad_output == "no_emit":
            return
        invalid = {"inputs": ["foreign"]} if bad_output == "foreign_inputs" else {}
        for _ in range(4):
            assert (await submit.execute(ToolContext(), records=[invalid])).startswith("Error:")
        assert not submit.can_retry
        assert submit.result is None

    loop = SimpleNamespace(
        config=Config(hooks=[]),
        tools=SimpleNamespace(get=lambda name: None),
        _run_agent_loop=run_child,
    )
    pipeline.model.agent_runner = agent_runner(
        loop,
        SessionKey(type="cli", channel_id="default", chat_id="compile-output-retry"),
        None,
        pipeline.limits,
    )

    async def work():
        result = await pipeline.model.ask(
            "map", "Extract assigned facts", {}, RecordResponse, validate, agent=True
        )
        return result.records

    if recovers:
        result = await job(pipeline, "map-output", [SimpleNamespace(record_id="source")], work)
        assert result[0].inputs == ["source"]
        assert attempts == 2
        assert pipeline.files.values["jobs/map-output"]["status"] == "completed"
    else:
        with pytest.raises(ModelCallError, match="without validated output"):
            await job(pipeline, "map-output", [SimpleNamespace(record_id="source")], work)
        assert attempts == MAX_JOB_ATTEMPTS
        assert pipeline.status["source"] == "failed"
        assert pipeline.files.values["jobs/map-output"]["status"] == "failed"
    assert pipeline.metrics["job_retries"] == attempts - 1
    assert len({id(submit) for submit in submissions}) == attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_compile_retries_exhausted_combine_output_with_missing_input_feedback(
    runtime, recovers
):
    pipeline, _ = runtime
    await seed(runtime)
    records = list(pipeline.records.values())
    ids = [record.record_id for record in records]

    def respond(**kwargs):
        number = pipeline.model.provider.chat.await_count
        if number % 4 != 1:
            previous_missing = ids[1] if (number - 1) % 2 else ids[0]
            assert previous_missing in kwargs["messages"][-1]["content"]
            assert "missing=" in kwargs["messages"][-1]["content"]
        else:
            assert len(kwargs["messages"]) == 2
        included = ids if recovers and number > 4 else (ids[:1] if number % 2 else ids[1:])
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    "submit",
                    "emit",
                    {"records": [{"inputs": included, "content": "Retry limit: 7"}]},
                    tokens=0,
                )
            ],
        )

    pipeline.model.provider.chat.side_effect = respond

    async def work():
        return await common.transform(pipeline, "combine", None, records, prompt=common._COMBINE)

    if recovers:
        result = await job(pipeline, "combine-output", records, work)
        assert set(result[0].source_refs) == set(ids)
        assert pipeline.model.provider.chat.await_count == 5
        assert pipeline.metrics["job_retries"] == 1
    else:
        with pytest.raises(ModelCallError, match="missing="):
            await job(pipeline, "combine-output", records, work)
        assert pipeline.model.provider.chat.await_count == 4 * MAX_JOB_ATTEMPTS
        assert pipeline.metrics["job_retries"] == MAX_JOB_ATTEMPTS - 1
        assert pipeline.files.values["jobs/combine-output"]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["misplaced_contract", "map_after_files"])
async def test_compile_planner_repairs_invalid_plan_with_exact_paths(runtime, problem):
    pipeline, _ = runtime
    valid: dict[str, Any] = {
        "contract": {
            "extract": {"instructions": "Extract facts", "execution": "agent", "output": "records"},
            "reduce": {"instructions": "Write Wiki pages", "execution": "agent", "output": "files"},
            "routing": {"mode": "semantic", "instructions": "Group by project"},
            "output_format": "wiki",
        },
        "plan": DEFAULT_PLAN,
    }
    if problem == "misplaced_contract":
        invalid = {"contract": {"extract": valid["contract"]["extract"]}, "plan": DEFAULT_PLAN}
        invalid.update(
            {name: valid["contract"][name] for name in ("reduce", "routing", "output_format")}
        )
    else:
        invalid = {
            "contract": {
                **valid["contract"],
                "synthesize": {
                    "instructions": "Create the Wiki index",
                    "execution": "agent",
                    "output": "files",
                },
            },
            "plan": DEFAULT_PLAN.replace(
                "p.finalize(changes, into=target)",
                "index_pages = p.map(changes, task=contract.synthesize)\n"
                "p.finalize(index_pages, into=target)",
            ),
        }
    pipeline.model.provider.chat.side_effect = [
        LLMResponse(content=None, tool_calls=[ToolCallRequest("bad", "emit", invalid, 0)]),
        LLMResponse(content=None, tool_calls=[ToolCallRequest("fixed", "emit", valid, 0)]),
    ]
    proposal = await pipeline.model.ask(
        "plan",
        "Plan the compilation",
        {},
        PlanProposal,
        lambda result: parse_plan(result.plan, result.contract),
    )
    assert proposal.contract.output_format == "wiki"
    assert [node.op for node in parse_plan(proposal.plan, proposal.contract)] == [
        "map",
        "shuffle",
        "reduce",
        "finalize",
    ]
    feedback = pipeline.model.provider.chat.call_args_list[1].kwargs["messages"][-1]["content"]
    if problem == "misplaced_contract":
        for name in ("reduce", "routing", "output_format"):
            assert f"{name} -> contract.{name}" in feedback
    else:
        assert "plan line 4, node index_pages: p.map cannot consume files from changes" in feedback
        assert "p.finalize(changes, into=target)" in feedback
    assert pipeline.metrics["validation_failures"] == 1
    assert pipeline.metrics["repairs"] == 1
    # Unrelated extensions retain the stored-plan parser's compatibility behavior.
    assert (
        PlanProposal.model_validate({**valid, "comment": "planner note"}).contract
        == proposal.contract
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("combine_outcome", ["success", "failure", "cancelled"])
async def test_compile_reduce_aggregates_provider_overflow_without_losing_source_lineage(
    runtime, monkeypatch, combine_outcome
):
    pipeline, _ = runtime
    await seed(runtime)
    records = list(pipeline.records.values())
    original_ids = {record.record_id for record in records}
    pipeline.contract = Contract(
        extract=Transform(instructions="Extract facts", execution="direct"),
        reduce=Transform(instructions="Write Wiki pages", execution="agent", output="files"),
        output_format="wiki",
    )
    overflowed = False
    combined_inputs = []

    async def separate_chunks(*args):
        return [[record] for record in records]

    monkeypatch.setattr("vikingbot.compile.ops.common.pack", separate_chunks)

    async def respond(stage, system, data, schema, validate=None, **kwargs):
        nonlocal overflowed
        if stage == "reduce" and not overflowed:
            overflowed = True
            try:
                raise ModelError(ModelErrorKind.INPUT_TOO_LARGE, "Provider context exceeded")
            except ModelError as exc:
                raise ModelCallError(str(exc)) from exc
        ids = [item["id"] for item in data["inputs"]]
        result: CombineResponse | FileResponse
        if stage == "combine":
            combined_inputs.extend(ids)
            if combine_outcome == "failure":
                raise ModelCallError("Combine provider unavailable")
            if combine_outcome == "cancelled":
                raise asyncio.CancelledError("Combine cancelled")
            result = CombineResponse.model_validate(
                {"records": [{"inputs": ids, "content": f"Retry limit: 7. [{URI}]({URI})"}]}
            )
        else:
            result = FileResponse(
                files=[
                    FileDraft(
                        path="entity/cedar.md", inputs=ids, content="# Cedar\n\nRetry limit: 7.\n"
                    )
                ]
            )
        if validate:
            validate(result)
        return result

    monkeypatch.setattr(pipeline.model, "ask", AsyncMock(side_effect=respond))
    if combine_outcome != "success":
        expected = ModelCallError if combine_outcome == "failure" else asyncio.CancelledError
        with pytest.raises(expected, match="Combine provider unavailable|Combine cancelled"):
            await reduce_group(
                pipeline, Node("pages", "reduce", "grouped", "reduce"), Group("all", records)
            )
        assert not pipeline.artifacts
        return
    refs = await reduce_group(
        pipeline, Node("pages", "reduce", "grouped", "reduce"), Group("all", records)
    )
    assert overflowed and set(combined_inputs) == original_ids
    artifact = await pipeline.files.get(refs[0])
    assert set(artifact["source_refs"]) == original_ids
    assert "Retry limit: 7" in artifact["content"]
