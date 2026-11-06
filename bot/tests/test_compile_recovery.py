# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile output recovery and contract validation without real model calls."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.tools.base import ToolContext
from vikingbot.compile import entry, file_ops
from vikingbot.compile.ops import common
from vikingbot.compile.ops.common import job
from vikingbot.compile.ops.reduce import resolve_files
from vikingbot.compile.pipeline_agent import agent_runner
from vikingbot.compile.pipeline_io import MAX_JOB_ATTEMPTS, ModelCallError
from vikingbot.compile.plan import PlanProposal
from vikingbot.compile.results import FileDraft, FileResponse, Group, RecordResponse
from vikingbot.config.schema import Config, SessionKey
from vikingbot.providers.base import LLMResponse, ToolCallRequest

from bot.tests.test_compile_sources import runtime as runtime
from bot.tests.test_compile_sources import seed


@pytest.mark.asyncio
async def test_compile_direct_output_exhaustion_falls_back(runtime):
    pipeline, _ = runtime
    await seed(runtime)
    records = list(pipeline.records.values())
    pipeline.model.provider.chat.return_value = LLMResponse(content='{"files": []}')
    assert await entry.run(pipeline, records, {}) is None
    assert pipeline.model.provider.chat.await_count == 4
    assert pipeline.direct_output_format is None


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [True, False])
async def test_compile_retries_agent_that_exits_without_emitting(runtime, recovers):
    pipeline, _ = runtime
    pipeline.files.sandbox = SimpleNamespace(write_file=AsyncMock())
    attempts = 0

    async def run_child(**kwargs):
        nonlocal attempts
        attempts += 1
        if recovers and attempts > 1:
            submit = kwargs["tool_registry"].get("emit")
            await submit.execute(
                ToolContext(), records=[{"inputs": ["source"], "payload": {"fact": "verified"}}]
            )

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
            "map", "Extract assigned facts", {}, RecordResponse, agent=True
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


def test_compile_rejects_misplaced_contract_settings_without_rejecting_extensions():
    proposal = {"contract": {"extract": {"instructions": "Extract facts", "execution": "direct"}}}
    with pytest.raises(ValueError, match="output_format -> contract.output_format"):
        PlanProposal.model_validate({**proposal, "output_format": "wiki"})
    assert PlanProposal.model_validate({**proposal, "comment": "planner note"}).contract


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_failure", [False, True])
async def test_collision_retry_refreshes_reservations_after_output_exhaustion(
    runtime, provider_failure
):
    pipeline, _ = runtime
    await seed(runtime)
    sources = dict(zip(("a", "b"), pipeline.records, strict=True))
    pipeline.limits = pipeline.limits.model_copy(update={"merge_concurrency": 2})
    a_started, b_reserved = asyncio.Event(), asyncio.Event()
    original_put = pipeline.files.put

    async def put(key, value):
        result = await original_put(key, value)
        if key.startswith("artifacts/") and value["path"] == "c.md":
            b_reserved.set()
        return result

    pipeline.files.put = put
    references = []
    for group, source in sources.items():
        records = [pipeline.records[source]]
        for index in range(2):
            draft = FileDraft(path=f"{group}.md", content=f"{group} fact {index}", inputs=[source])
            references += await file_ops.save_files(
                pipeline,
                FileResponse(files=[draft]),
                Group(f"{group}-{index}", records),
                records,
                {},
            )

    async def respond(**kwargs):
        request = json.loads(kwargs["messages"][1]["content"])
        group = request["candidates"][0]["path"][0]
        if group == "a":
            a_started.set()
            await asyncio.wait_for(b_reserved.wait(), timeout=5)
            if provider_failure:
                raise OSError("provider unavailable")
            path = "d.md" if "c.md" in request["reserved_paths"] else "c.md"
        else:
            await asyncio.wait_for(a_started.wait(), timeout=5)
            path = "c.md"
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    "submit",
                    "emit",
                    {
                        "files": [
                            {"path": path, "content": f"{group} facts", "inputs": [sources[group]]}
                        ]
                    },
                    0,
                )
            ],
        )

    pipeline.model.provider.chat.side_effect = respond
    result = await resolve_files(pipeline, references)
    paths = sorted(pipeline.files.values[ref]["path"] for ref in result)
    if provider_failure:
        assert paths == ["c.md"]
        assert pipeline.status[sources["a"]] == "failed"
        assert "provider unavailable" in pipeline.failures[0]
    else:
        assert paths == ["c.md", "d.md"]
        assert all(pipeline.status[source] == "prepared" for source in sources.values())
        assert pipeline.failures == []
    assert pipeline.model.provider.chat.await_count == (2 if provider_failure else 6)
