"""Retain real SDK write responses at both Runtime publication entry points."""

# ruff: noqa: F811 - imported pytest fixtures are reused as parameters.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_compile_markdown import configure_pipeline, pipeline  # noqa: F401
from vikingbot.compile.service import BotCompileService
from vikingbot.compile.simple import CompileWriteTool

from openviking_cli.exceptions import NotFoundError


@pytest.mark.parametrize("task_id", ["task-content-exact", None])
async def test_complex_runtime_retains_the_batch_write_task(pipeline, monkeypatch, task_id):
    pipeline.client.batch_write.return_value = {
        "task_id": task_id,
        "created": [pipeline.target + "/topic.md"],
    }
    await configure_pipeline(pipeline)
    task = SimpleNamespace(status="running", meta={}, result=None)

    async def update(_, mutate):
        mutate(task)

    service = BotCompileService.__new__(BotCompileService)
    service.agent_loop = SimpleNamespace(
        provider=pipeline.model.provider,
        model="test-model",
        temperature=0,
        tools=SimpleNamespace(get=lambda _: None),
    )
    service._model_slots = asyncio.Semaphore(1)
    service.limits = pipeline.limits
    service.store = SimpleNamespace(
        get=AsyncMock(return_value=task), update=AsyncMock(side_effect=update)
    )
    service._set_state = AsyncMock()
    monkeypatch.setattr("vikingbot.compile.service.Pipeline", lambda **kwargs: pipeline)
    await service._run_pipeline(
        task_id="test",
        request=pipeline.request,
        client=pipeline.client,
        sandbox=pipeline.files.sandbox,
        skill_text=pipeline.skill,
        source_files=["viking://resources/source/one.md"],
        usage={},
        agent_runner=pipeline.model.agent_runner,
    )
    publication = task.result.publication
    assert publication["state"] == ("pending" if task_id else "unknown")
    assert (await pipeline.files.get("summary"))["publication"] == publication
    assert publication["receipts"] == (
        [{"task_id": task_id, "uri": pipeline.target, "kind": "content_batch_write"}]
        if task_id
        else []
    )
    assert pipeline.client.batch_write.await_args.kwargs["wait"] is False


async def test_simple_writer_retains_each_async_task_even_for_repeated_uri():
    uri = "viking://resources/output/topic.md"
    client = SimpleNamespace(
        read_raw=AsyncMock(side_effect=[NotFoundError("new"), "old"]),
        write=AsyncMock(
            side_effect=[
                {"uri": uri, "content_updated": True, "task_id": "write-1"},
                {"uri": uri, "content_updated": True, "task_id": "write-2"},
            ]
        ),
    )
    saved = []

    async def save(writer):
        saved.append(writer.publication.snapshot(uncertain=bool(writer.unconfirmed)))

    writer = CompileWriteTool(client, "viking://resources/output", save)
    await writer.execute(None, "topic.md", "first")
    await writer.execute(None, "topic.md", "second")
    assert [row["task_id"] for row in saved[-1]["receipts"]] == ["write-1", "write-2"]
    assert saved[-1]["state"] == "pending" and not saved[-1]["incomplete"]


async def test_unchanged_simple_write_does_not_claim_new_index_processing():
    client = SimpleNamespace(read_raw=AsyncMock(return_value="same"), write=AsyncMock())
    writer = CompileWriteTool(client, "viking://resources/output", AsyncMock())
    await writer.execute(None, "topic.md", "same")
    client.write.assert_not_awaited()
    assert writer.publication.snapshot()["state"] == "not_requested"
