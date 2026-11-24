"""Source stragglers do not form read waves or reorder model-facing inputs."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.renderer import RenderedBundle
from vikingbot.compile.service import BotCompileService

from bot.tests.test_compile_markdown import pipeline as pipeline


async def make_service(pipeline, monkeypatch):
    task = SimpleNamespace(status="running", meta={}, result=None)

    async def update(task_id, mutate):
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
    captured = []

    async def consume(batches):
        captured.extend(await pipeline.seed(batches))
        await pipeline.files.put("summary", {"prepared": True})
        return RenderedBundle(unchanged=[pipeline.target + "/existing.md"])

    pipeline.run = consume
    return service, captured, task


def execute(service, pipeline, uris):
    return service._run_pipeline(
        task_id="source-prefetch",
        request=pipeline.request,
        client=pipeline.client,
        sandbox=pipeline.files.sandbox,
        skill_text=pipeline.skill,
        source_files=uris,
        usage={},
    )


async def test_service_refills_before_the_slowest_read_without_reordering_inputs(
    pipeline, monkeypatch
):
    service, captured, task = await make_service(pipeline, monkeypatch)
    uris = [f"viking://resources/source/{i:02}.md" for i in range(17)]
    release, refilled = asyncio.Event(), asyncio.Event()
    active = peak = 0

    async def read(client, uri):
        nonlocal active, peak
        index = uris.index(uri)
        active += 1
        peak = max(peak, active)
        try:
            if index == 0:
                await release.wait()
            if index == 8:
                refilled.set()
            await asyncio.sleep(0)
            return "# Synthetic source\n" + str(index)
        finally:
            active -= 1

    monkeypatch.setattr("vikingbot.compile.service.read_source", read)
    running = asyncio.create_task(execute(service, pipeline, uris))
    try:
        # The original fixed gather waits forever at source zero before starting eight.
        await asyncio.wait_for(refilled.wait(), 1)
    finally:
        release.set()
        await running
    assert peak <= 8
    assert [source.metadata["uri"] for source in captured] == uris
    assert len({source.source_id for source in captured}) == len(uris)
    assert task.status == "completed"
    assert active == 0


async def test_service_cancellation_drains_every_started_source_read(pipeline, monkeypatch):
    service, _, _ = await make_service(pipeline, monkeypatch)
    uris = [f"viking://resources/source/{i}.md" for i in range(25)]
    started, cancelled, ready = set(), set(), asyncio.Event()

    async def read(client, uri):
        started.add(uri)
        if len(started) == 8:
            ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.add(uri)

    monkeypatch.setattr("vikingbot.compile.service.read_source", read)
    running = asyncio.create_task(execute(service, pipeline, uris))
    await asyncio.wait_for(ready.wait(), 1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, 1)
    assert started == cancelled and len(started) == 8
