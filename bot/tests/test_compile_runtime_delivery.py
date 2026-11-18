"""Runtime reports delivery gaps without equating natural completion with coverage."""

# ruff: noqa: F811 - imported shared pytest fixtures are reused as parameters.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_compile_markdown import configure_pipeline, pipeline  # noqa: F401
from vikingbot.compile import simple
from vikingbot.compile.models import CompileResult, CompileTask
from vikingbot.compile.service import BotCompileService
from vikingbot.config.schema import Config, SessionKey

from openviking_cli.exceptions import NotFoundError


@pytest.mark.parametrize("failure", ["read", "worker", "conflict", "none"])
async def test_actual_pipeline_publication_exposes_delivery_status(pipeline, monkeypatch, failure):
    await configure_pipeline(pipeline, fail_second=failure == "worker")
    if failure == "read":

        async def read(uri):
            if uri.endswith("two.md"):
                raise ConnectionError("synthetic source unavailable")
            return "synthetic source"

        pipeline.client.read_raw = read
    published = [pipeline.target + "/topic.md", pipeline.target + "/independent.md"]
    pipeline.client.batch_write.return_value = {
        "created": published[:1] if failure != "none" else published,
        "updated": [],
        "unchanged": [],
        "conflicts": [{"uri": published[1], "code": "ALREADY_EXISTS", "message": "kept existing"}]
        if failure == "conflict"
        else [],
    }
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
    await service._run_pipeline(
        task_id="partial",
        request=pipeline.request,
        client=pipeline.client,
        sandbox=pipeline.files.sandbox,
        skill_text=pipeline.skill,
        source_files=["viking://resources/source/one.md", "viking://resources/source/two.md"],
        usage={},
        agent_runner=pipeline.model.agent_runner,
    )
    assert task.status == "completed"
    result = task.result.model_dump(exclude_none=True)
    assert result.get("completion_status") == ("partial" if failure != "none" else None)
    assert ("completion_status" in result) == (failure != "none")
    assert task.result.created == (published if failure == "none" else published[:1])
    assert CompileResult.model_validate(result) == task.result


@pytest.mark.parametrize("ending", ["normal", "budget", "incomplete", "unconfirmed"])
async def test_actual_simple_entry_marks_known_incomplete_and_otherwise_unknown(ending):
    target = "viking://resources/output"
    client = SimpleNamespace(read_raw=AsyncMock(side_effect=NotFoundError("missing")))
    client.write = AsyncMock(return_value={"uri": target + "/kept.md", "content_updated": True})

    async def execute_agent(**kwargs):
        writer = kwargs["tool_registry"].get("openviking_write")
        await writer.execute(None, "kept.md", "# Synthetic retained output")
        if ending == "unconfirmed":
            client.write.return_value = {}
            with pytest.raises(ValueError, match="Unconfirmed"):
                await writer.execute(None, "uncertain.md", "# Uncertain")
        final = (
            None
            if ending == "budget"
            else "INCOMPLETE: one input remains"
            if ending == "incomplete"
            else "Done"
        )
        return final, [], [], {}, None

    loop = SimpleNamespace(config=Config(), max_iterations=60, _run_agent_loop=execute_agent)
    request = SimpleNamespace(
        from_=["viking://resources/source"],
        to=target,
        instruction="Compile synthetic inputs",
        skill="viking://agent/skills/example",
        changes=None,
    )
    result = await simple.run(
        loop,
        client,
        request,
        request.from_,
        "Generic rules",
        SessionKey(type="test", channel_id="channel", chat_id="chat"),
        {},
        AsyncMock(),
        {},
    )
    assert result.completion_status == (None if ending == "normal" else "partial")
    assert result.created == [target + "/kept.md"]


@pytest.mark.parametrize("completion_status", [None, "partial"])
def test_runtime_status_only_includes_partial_marker(completion_status):
    result = CompileResult(
        from_=["viking://resources/source"],
        to="viking://resources/output",
        skill="viking://agent/skills/example",
        completion_status=completion_status,
    )
    task = CompileTask.model_construct(result=result)
    payload = task.public_dict()["result"]
    assert ("completion_status" in payload) == (completion_status == "partial")
    assert payload.get("completion_status") == completion_status
