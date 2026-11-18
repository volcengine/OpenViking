"""Source stragglers do not form read waves or reorder model-facing inputs."""

# ruff: noqa: F811 - pytest fixture parameters reuse the imported shared fixture.

import asyncio
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_compile_markdown import pipeline  # noqa: F401
from vikingbot.compile.renderer import RenderedBundle
from vikingbot.compile.service import BotCompileService


async def make_service(pipeline, monkeypatch):  # noqa: F811
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


def execute(service, pipeline, uris):  # noqa: F811
    return service._run_pipeline(
        task_id="source-prefetch",
        request=pipeline.request,
        client=pipeline.client,
        sandbox=pipeline.files.sandbox,
        skill_text=pipeline.skill,
        source_files=uris,
        usage={},
    )


@pytest.mark.asyncio
async def test_service_refills_before_the_slowest_read_without_reordering_inputs(
    pipeline, monkeypatch
):  # noqa: F811
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


@pytest.mark.asyncio
async def test_service_retains_failed_read_and_visits_other_sources(pipeline, monkeypatch):  # noqa: F811
    service, captured, _ = await make_service(pipeline, monkeypatch)
    uris = [f"viking://resources/source/{i}.md" for i in range(19)]
    visited = []

    async def read(client, uri):
        visited.append(uri)
        if uri == uris[2]:
            raise ConnectionError("synthetic source unavailable")
        return "source " + uri.rsplit("/", 1)[-1]

    monkeypatch.setattr("vikingbot.compile.service.read_source", read)
    await execute(service, pipeline, uris)
    assert set(visited) == set(uris)
    assert [s.metadata["uri"] for s in captured] == [uri for uri in uris if uri != uris[2]]
    assert (await pipeline.files.get("inputs"))[uris[2]] == "failed"
    assert pipeline.failures


@pytest.mark.asyncio
async def test_service_cancellation_drains_every_started_source_read(pipeline, monkeypatch):  # noqa: F811
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


@pytest.mark.asyncio
async def test_completed_reader_applies_backpressure_and_cleans_up_early_exit():
    from vikingbot.compile.pipeline_io import completed_jobs

    started = set()

    async def read(index):
        started.add(index)
        return "synthetic"

    reads = completed_jobs(range(100), read, concurrency=3)
    async with aclosing(reads):
        await anext(reads)
        await asyncio.sleep(0)
        # Consuming no further result cannot grow retained work to a full collection.
        assert started == {0, 1, 2}
    assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [0, -1, True, 1.5])
async def test_completed_reader_rejects_invalid_capacity(concurrency):
    from vikingbot.compile.pipeline_io import completed_jobs

    with pytest.raises(ValueError):
        await anext(completed_jobs([], AsyncMock(), concurrency=concurrency))


@pytest.mark.asyncio
async def test_source_shard_failure_closes_pending_readers(pipeline, monkeypatch):  # noqa: F811
    service, _, _ = await make_service(pipeline, monkeypatch)
    uris = [f"viking://resources/source/{i}.md" for i in range(20)]
    started, closed = set(), set()
    all_started = asyncio.Event()

    async def read(client, uri):
        started.add(uri)
        if len(started) == 8:
            all_started.set()
        try:
            if uri == uris[0]:
                await all_started.wait()
                return "synthetic source"
            await asyncio.Event().wait()
        finally:
            closed.add(uri)

    put = pipeline.files.put

    async def failing_put(path, value):
        if path.startswith("sources/"):
            raise OSError("synthetic shard failure")
        return await put(path, value)

    monkeypatch.setattr("vikingbot.compile.service.read_source", read)
    monkeypatch.setattr(pipeline.files, "put", failing_put)
    with pytest.raises(ValueError, match="pipeline jobs failed"):
        await asyncio.wait_for(execute(service, pipeline, uris), 2)
    assert started == closed and len(started) == 8
    pipeline.client.batch_write.assert_not_awaited()
