"""Markdown compile stage selection, isolated file workers and publication boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.compile import CompileChildTool
from vikingbot.agent.tools.filesystem import WriteFileTool
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.openapi import OpenAPIChannel
from vikingbot.compile.file_ops import DocumentReader
from vikingbot.compile.models import CompileLimits, SanitizedCompileRequest
from vikingbot.compile.ops import finalize
from vikingbot.compile.ops.shuffle import Shuffle
from vikingbot.compile.pipeline import Pipeline
from vikingbot.compile.pipeline_agent import TargetReader, agent_runner
from vikingbot.compile.plan import Contract, PlanProposal, Transform, parse_plan
from vikingbot.compile.results import Document, Group, RouteResponse
from vikingbot.compile.service import BotCompileService
from vikingbot.compile.sources import split_source
from vikingbot.config.schema import Config, OpenAPIChannelConfig, SessionKey
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.session.manager import SessionManager

from openviking_cli.exceptions import NotFoundError

MAP_ONLY = "drafts = p.map(sources, task=contract.extract)\np.finalize(drafts, into=target)"
MERGE = (
    "drafts = p.map(sources, task=contract.extract)\n"
    "groups = p.shuffle(drafts, by=contract.routing)\n"
    "files = p.reduce(groups, task=contract.reduce)\np.finalize(files, into=target)"
)


def contract(merge=False):
    return Contract.model_validate(
        {
            "extract": {"instructions": "Write source facts as Markdown."},
            **(
                {
                    "routing": {
                        "mode": "semantic",
                        "instructions": "Group complementary facts about one topic.",
                    },
                    "reduce": {"instructions": "Merge Markdown, preserving source references."},
                }
                if merge
                else {}
            ),
        }
    )


class Provider(LLMProvider):
    def get_default_model(self):
        return "test-model"

    async def chat(self, **kwargs):
        raise AssertionError("A test response must be configured")


@pytest.fixture
async def pipeline(tmp_path):
    config = Config()
    key = SessionKey(type="cli", channel_id="test", chat_id="markdown")
    sandbox = DirectBackend(config.sandbox, key, tmp_path)
    await sandbox.start()
    provider = Provider()
    client = SimpleNamespace(
        stat=AsyncMock(side_effect=NotFoundError("file")),
        list_resources=AsyncMock(return_value=[]),
        read_raw=AsyncMock(return_value="source fact"),
        download_bytes=AsyncMock(),
        batch_write=AsyncMock(return_value={"created": ["viking://resources/wiki/topic.md"]}),
    )
    pipeline = Pipeline(
        client=client,
        sandbox=sandbox,
        provider=provider,
        model="test-model",
        temperature=0,
        limits=CompileLimits(source_concurrency=1, merge_concurrency=1),
        request=SanitizedCompileRequest(
            **{
                "from": ["viking://resources/source"],
                "to": "viking://resources/wiki",
                "skill": "viking://agent/skills/wiki",
                "instruction": "Compile facts.",
            }
        ),
        skill="Write Markdown with original source citations.",
        usage={},
    )
    pipeline.contract = contract()
    pipeline.timestamp = "2026-11-14T00:00:00Z"
    yield pipeline
    await sandbox.stop()


@pytest.fixture
def agent_loop(pipeline, tmp_path):
    """Provide agent tools only to tests that execute worker or file-tool calls."""
    sandbox = pipeline.files.sandbox
    key = sandbox.session_key
    manager = SimpleNamespace(
        get_sandbox=AsyncMock(return_value=sandbox), to_workspace_id=lambda key: "test"
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=pipeline.model.provider,
        workspace=tmp_path,
        model="test-model",
        config=Config(),
        sandbox_manager=manager,
        session_manager=SessionManager(tmp_path),
    )
    return loop, key, manager


def test_plan_selects_optional_routing_and_has_no_record_configuration():
    assert [n.op for n in parse_plan(MAP_ONLY, contract())] == ["map", "finalize"]
    assert [n.op for n in parse_plan(MERGE, contract(True))] == [
        "map",
        "shuffle",
        "reduce",
        "finalize",
    ]
    with pytest.raises(ValidationError):
        Transform(instructions="x", output="records")


@pytest.mark.parametrize(
    "program",
    [
        "import os\n" + MAP_ONLY,
        "drafts = p.map(sources, task=contract.extract)\ngroups = p.shuffle(drafts, by=contract.routing)\np.finalize(groups, into=target)",
        "drafts = p.map(sources, task=contract.extract)\nother = p.map(sources, task=contract.extract)\np.finalize(other, into=target)",
        "drafts = p.map(sources, task=contract.extract)\np.finalize(drafts, into=sources)",
    ],
)
def test_plan_rejects_unsafe_or_invalid_flows(program):
    with pytest.raises(ValueError):
        parse_plan(program, contract(True))


async def configure_pipeline(pipeline, merge=False, fail_second=False):
    """Use actual file collection with controlled agent jobs and JSON control responses."""
    proposal = PlanProposal(contract=contract(merge), plan=MERGE if merge else MAP_ONLY)

    async def chat(**kwargs):
        assert all(t["function"]["name"] != "emit" for t in kwargs["tools"])
        system = kwargs["messages"][0]["content"]
        if system.startswith("Choose"):
            return LLMResponse(content=proposal.model_dump_json())
        return LLMResponse(content='{"action":"continue"}')

    pipeline.model.provider.chat = AsyncMock(side_effect=chat)
    calls = []

    async def worker(system, data, model, *, stage, runtime):
        calls.append(stage)
        root = f"__compile_staging__/drafts/{stage}/output"
        name = "topic.md" if merge or len(calls) == 1 else "independent.md"
        content = "# Topic\n\n[Source](viking://resources/source/one.md)\n"
        if data.get("documents"):
            assert len(data["documents"]) == 2
            content += "Combined details from both drafts.\n"
        await model.files.sandbox.write_file(f"{root}/{name}", content)
        await model.files.sandbox.write_file(
            root.rsplit("/", 1)[0] + "/scratch.md", "private scratch"
        )
        if fail_second and len(calls) == 2:
            raise ValueError("Worker stopped after a partial write")
        return root

    pipeline.model.agent_runner = worker
    return calls


async def sources(count=2):
    for index in range(count):
        yield split_source(f"viking://resources/source/{index}.md", f"Fact {index}", 1000)


async def test_map_only_collects_only_completed_output_directories(pipeline):
    calls = await configure_pipeline(pipeline, fail_second=True)
    bundle = await pipeline.run(sources())
    assert len(calls) == 2 and all(c.startswith("drafts-") for c in calls)
    assert [op["uri"] for op in bundle.operations] == [pipeline.target + "/topic.md"]
    assert pipeline.failures and pipeline.warnings
    assert (await pipeline.files.get("summary"))["prepared"] is False


async def test_markdown_routing_reaches_reduce_without_publishing_map_drafts(pipeline, monkeypatch):
    calls = await configure_pipeline(pipeline, merge=True)

    async def route(self, node, documents):
        assert len(documents) == 2
        for doc in documents:
            assert "# Topic" in await pipeline.files.sandbox.read_file(doc.workspace_path)
        return [Group("topic", documents)]

    monkeypatch.setattr(Shuffle, "run", route)
    bundle = await pipeline.run(sources())
    assert len(calls) == 3 and calls[-1] == "files-topic"
    assert len(bundle.operations) == 1
    assert "Combined details" in bundle.operations[0]["content"]
    assert len(pipeline.artifacts) == 1


@pytest.mark.parametrize("ending", ["success", "budget", "truncated"])
async def test_file_worker_uses_existing_tools_without_emit(pipeline, agent_loop, ending):
    loop, key, _ = agent_loop
    replies = [
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
        )
    ]
    if ending != "budget":
        replies.extend(
            [
                LLMResponse(
                    content="Done", finish_reason="length" if ending == "truncated" else "stop"
                )
            ]
            * (4 if ending == "truncated" else 1)
        )
    pipeline.model.provider.chat = AsyncMock(side_effect=replies)
    pipeline.model.agent_runner = agent_runner(
        loop,
        key,
        None,
        pipeline.limits.model_copy(update={"subagent_iterations": 1 if ending == "budget" else 4}),
    )
    if ending == "success":
        result = await pipeline.generate("map-0", "Generate Markdown", {"inputs": []}, [])
        assert len(result) == 1 and result[0].path == "topic.md"
        assert (
            await pipeline.files.sandbox.read_file(result[0].workspace_path) == "# Topic\n\nFacts."
        )
    else:
        with pytest.raises(ValueError):
            await pipeline.generate("map-0", "Generate Markdown", {"inputs": []}, [])
    for call in pipeline.model.provider.chat.await_args_list:
        names = {t["function"]["name"] for t in call.kwargs["tools"]}
        assert {"read_file", "write_file", "edit_file"} <= names
        assert "emit" not in names


@pytest.mark.parametrize("input_path", ["assignment.json", "inputs/0.md"])
async def test_worker_output_paths_and_readonly_inputs(pipeline, agent_loop, input_path):
    loop, key, manager = agent_loop
    root = "__compile_staging__/drafts/private"
    aliases = {input_path: f"{root}/{input_path}"}
    options = {"draft_root": root + "/output", "merge_only": True, "readonly_paths": aliases}
    read, write, edit = [
        CompileChildTool(loop.tools.get(name), **options)
        for name in ("read_file", "write_file", "edit_file")
    ]
    context = ToolContext(session_key=key, sandbox_manager=manager)
    await pipeline.files.sandbox.write_file(aliases[input_path], "input")
    await write.execute(context, path="topic.md", content="before")
    await edit.execute(context, path="topic.md", old_text="before", new_text="after")
    assert await read.execute(context, path="topic.md") == "after"
    assert await pipeline.files.sandbox.read_file(root + "/output/topic.md") == "after"
    assert await read.execute(context, path=input_path) == "input"
    with pytest.raises(ValueError, match="read-only"):
        await write.execute(context, path=input_path, content="changed")


@pytest.mark.parametrize("path", ["../escape.md", "/tmp/escape.md", "viking://resources/escape.md"])
async def test_file_tool_rejects_paths_outside_private_worker(agent_loop, path):
    _, key, manager = agent_loop
    tool = CompileChildTool(WriteFileTool(), "__compile_staging__/drafts/private", merge_only=True)
    with pytest.raises(ValueError):
        await tool.execute(
            ToolContext(session_key=key, sandbox_manager=manager), path=path, content="x"
        )


async def test_markdown_routing_uses_content_and_checks_foreign_decisions(pipeline, monkeypatch):
    pipeline.contract = contract(True)
    docs = [Document("unrelated-name.md", "drafts/a.md"), Document("other-name.md", "drafts/b.md")]
    for doc in docs:
        await pipeline.files.sandbox.write_file(
            doc.workspace_path, "# Retry policy\n\nRetry limit: 7."
        )
    monkeypatch.setattr(Shuffle, "vectors", AsyncMock(return_value=[[1.0, 0.0], [1.0, 0.0]]))

    async def chat(**kwargs):
        data = json.loads(kwargs["messages"][1]["content"])
        assert all("Retry limit: 7" in d["markdown"] for d in data["documents"])
        assert all("scope" not in d and "routing_text" not in d for d in data["documents"])
        return LLMResponse(
            content=json.dumps(
                {
                    "decisions": [
                        {"document": a["document"], "related": a["candidates"], "history": []}
                        for a in data["assignments"]
                    ]
                }
            )
        )

    pipeline.model.provider.chat = AsyncMock(side_effect=chat)
    node = parse_plan(MERGE, pipeline.contract)[1]
    groups = await Shuffle(pipeline).run(node, docs)
    assert len(groups) == 1 and groups[0].documents == docs
    reader = DocumentReader(pipeline, docs)
    with pytest.raises(ValueError, match="outside"):
        await reader.execute("foreign.md")

    async def malicious(stage, system, data, schema, validate, **kwargs):
        validate(
            RouteResponse(
                decisions=[
                    {"document": data["assignments"][0]["document"], "related": ["foreign.md"]},
                    {"document": data["assignments"][1]["document"]},
                ]
            )
        )

    pipeline.model.ask = malicious
    with pytest.raises(ValueError, match="supplied candidates"):
        await Shuffle(pipeline).run(node, docs)


async def test_finalization_requires_unique_paths_and_prior_target_read(pipeline):
    doc = Document("topic.md", "draft.md")
    await pipeline.files.sandbox.write_file(doc.workspace_path, "# Topic\n\nNew facts.")
    with pytest.raises(ValueError, match="Multiple output writers"):
        await finalize.run(pipeline, [doc, doc])
    pipeline.old.clear()
    pipeline.client.stat = AsyncMock(return_value={"isDir": False})
    pipeline.client.download_bytes.return_value = b"# Existing facts"
    assert (await finalize.run(pipeline, [doc])).operations[0]["mode"] == "create"
    pipeline.old.clear()
    await TargetReader(pipeline).execute(path="topic.md")
    assert (await finalize.run(pipeline, [doc])).operations[0]["mode"] == "replace"
    pipeline.client.download_bytes.return_value = b"concurrent edit"
    with pytest.raises(ValueError, match="changed after reading"):
        await finalize.run(pipeline, [doc])


@pytest.mark.parametrize("cancelled,conflict", [(False, False), (False, True), (True, False)])
async def test_service_publishes_final_markdown_with_batch_write_and_honors_cancel(
    pipeline, monkeypatch, cancelled, conflict
):
    if conflict:
        pipeline.client.batch_write.return_value = {
            "created": [],
            "updated": [],
            "unchanged": [],
            "conflicts": [
                {
                    "uri": pipeline.target + "/topic.md",
                    "code": "ALREADY_EXISTS",
                    "message": "Exists",
                }
            ],
        }
    await configure_pipeline(pipeline)
    task = SimpleNamespace(status="cancelling" if cancelled else "running", meta={}, result=None)

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
    call = service._run_pipeline(
        task_id="test",
        request=pipeline.request,
        client=pipeline.client,
        sandbox=pipeline.files.sandbox,
        skill_text=pipeline.skill,
        source_files=["viking://resources/source/one.md"],
        usage={},
        agent_runner=pipeline.model.agent_runner,
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await call
        pipeline.client.batch_write.assert_not_awaited()
    else:
        await call
        write = pipeline.client.batch_write.await_args.kwargs
        assert write["root_uri"] == pipeline.target and write["skip_conflicts"] is True
        assert write["operations"] == [
            {
                "uri": pipeline.target + "/topic.md",
                "content": "# Topic\n\n[Source](viking://resources/source/one.md)\n",
                "mode": "create",
            }
        ]
        assert task.status == ("failed" if conflict else "completed")
        assert task.result.page_count == (0 if conflict else 1)
        assert bool(task.result.conflicts) == conflict


@pytest.mark.parametrize(
    "path,content",
    [(None, None), ("topic.md", b"   \n"), ("topic.json", b"{}"), ("topic.md", b"\xff\x00")],
)
async def test_file_collection_rejects_missing_empty_nonmarkdown_and_invalid_utf8(
    pipeline, path, content
):
    root = "__compile_staging__/drafts/invalid/output"

    async def worker(*args, **kwargs):
        if path is not None:
            await pipeline.files.sandbox.write_file_bytes(root + "/" + path, content)
        return root

    pipeline.model.agent_runner = worker
    with pytest.raises(ValueError):
        await pipeline.generate("map-0", "Write Markdown.", {}, [])
    assert pipeline.artifacts == []


async def test_routing_history_rejects_files_outside_to(pipeline):
    pipeline.client.find = AsyncMock(
        return_value={"resources": [{"uri": "viking://resources/other/doc.md"}]}
    )
    with pytest.raises(ValueError, match="out-of-scope"):
        await Shuffle(pipeline).candidates("# Topic\nFacts")
    pipeline.client.stat.assert_not_awaited()


async def test_reduce_failure_keeps_map_drafts_out_of_final_outputs(pipeline):
    pipeline.contract = contract(True)
    pipeline.evidence["input"] = {"uri": "viking://resources/source/a.md"}
    pipeline.status["input"] = "prepared"
    pipeline.generate = AsyncMock(side_effect=ValueError("Failed after writing partial Markdown"))
    from vikingbot.compile.ops import reduce

    outputs = await reduce.run(
        pipeline,
        parse_plan(MERGE, pipeline.contract)[2],
        [Group("topic", [Document("topic.md", "map/topic.md", ["input"])])],
    )
    assert outputs == [] and pipeline.status["input"] == "failed"
    assert pipeline.failures


@pytest.mark.parametrize("bad", ["true", "false", 0, 1, None, {}, []])
async def test_shared_compile_keeps_args_opaque_and_vikingbot_validates_the_flag(bad):
    from vikingbot.compile.models import CompileFailure, CompileRequest

    from openviking.service.compile_service import CompileInputs

    payload = {
        "from": ["viking://resources/source"],
        "to": "viking://resources/wiki",
        "skill": "viking://agent/skills/wiki",
        "args": {"global_conflict_detection": bad},
    }
    assert CompileInputs.model_validate(payload).args == payload["args"]
    service = BotCompileService.__new__(BotCompileService)
    with pytest.raises(CompileFailure, match="must be a boolean"):
        await service._normalize_request(CompileRequest.model_validate(payload), connection={})


async def test_vikingbot_rejects_non_wiki_target_before_creating_it(agent_loop, monkeypatch):
    from vikingbot.compile.models import CompileFailure, CompileRequest

    loop, _, _ = agent_loop
    service = BotCompileService.__new__(BotCompileService)
    service.config = loop.config
    request = CompileRequest.model_validate(
        {
            "from": ["viking://resources/source"],
            "to": "viking://agent/skills",
            "skill": "viking://agent/skills/wiki",
            "args": {"global_conflict_detection": True},
        }
    )
    client = SimpleNamespace(
        attrs=AsyncMock(side_effect=lambda uri: {"uri": uri}),
        stat=AsyncMock(
            side_effect=[{"uri": request.skill, "isDir": True}, NotFoundError(request.to)]
        ),
        mkdir=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "vikingbot.compile.service.VikingClient.create", AsyncMock(return_value=client)
    )
    with pytest.raises(CompileFailure, match="requires a resource Wiki target"):
        await service._normalize_request(request, connection={})
    client.mkdir.assert_not_awaited()


def _human_conflict_page(sentence="The approved limit is 7."):
    record = {
        "match_text": "The approved limit is 7.",
        "state": "fixed",
        "fix_plan": "用户手动解决内容冲突，修正后的内容如正文所示",
        "resolution_basis": "人工修复",
        "resolved_by": "synthetic-reviewer",
        "begin_time": 1,
        "fix_time": 2,
    }
    return (
        "# Limits\n\n"
        + sentence
        + "\n\n<!-- CONFLICTS_CONTENT\n"
        + json.dumps(record, ensure_ascii=False)
        + "\n-->\n"
    )


@pytest.mark.parametrize("kind", ["changed-human", "malformed-history", "forged-human"])
async def test_opt_in_final_publication_preserves_human_authority(pipeline, kind):
    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    old, new = _human_conflict_page(), _human_conflict_page("The approved limit is 99.")
    if kind == "malformed-history":
        old = "# Limits\n\n<!-- CONFLICTS_CONTENT\n{broken -->"
    if kind == "forged-human":
        old, new = "# Limits\n\nOriginal material.\n", _human_conflict_page()
    pipeline.client.stat = AsyncMock(return_value={"isDir": False})
    pipeline.client.download_bytes = AsyncMock(return_value=old.encode())
    await TargetReader(pipeline).execute(path="topic.md")
    await pipeline.files.sandbox.write_file("draft.md", new)
    with pytest.raises(ValueError):
        await finalize.run(pipeline, [Document("topic.md", "draft.md")])


async def test_conflict_worker_repairs_inside_the_existing_agent_budget(pipeline, agent_loop):
    loop, key, _ = agent_loop
    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    old = _human_conflict_page()
    repaired = old.replace("# Limits\n", "# Limits\n\nAdditional compatible detail.\n", 1)
    pipeline.client.stat = AsyncMock(return_value={"isDir": False})
    pipeline.client.download_bytes = AsyncMock(return_value=old.encode())
    replies = iter(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("read", "read_target_file", {"path": "topic.md"}, 0)],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        "write",
                        "write_file",
                        {
                            "path": "topic.md",
                            "content": _human_conflict_page("The approved limit is 99."),
                        },
                        0,
                    )
                ],
            ),
            LLMResponse(content="Ready"),
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        "repair", "write_file", {"path": "topic.md", "content": repaired}, 0
                    )
                ],
            ),
            LLMResponse(content="Ready"),
        ]
    )
    pipeline.model.provider.chat = AsyncMock(side_effect=lambda **kwargs: next(replies))
    pipeline.model.agent_runner = agent_runner(loop, key, None, pipeline.limits)
    await pipeline.files.sandbox.write_file("input.md", old)
    docs = await pipeline.generate(
        "reduce-0",
        "Merge evidence and protect human decisions",
        {"documents": [vars(Document("topic.md", "input.md"))]},
        [],
    )
    assert len(docs) == 1
    bundle = await finalize.run(pipeline, docs)
    assert bundle.operations[0]["content"] == repaired
    assert pipeline.model.provider.chat.await_count == 5


async def test_conflict_plan_and_snapshot_keep_comparison_on(pipeline):
    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    frozen = SanitizedCompileRequest.model_validate_json(pipeline.request.model_dump_json())
    assert frozen.global_conflict_detection is True
    pipeline.target_has_content = True
    with pytest.raises(ValueError, match="Shuffle and Reduce"):
        pipeline.validate_plan(parse_plan(MAP_ONLY, contract()))
    with pytest.raises(ValueError, match="against=target"):
        pipeline.validate_plan(parse_plan(MERGE, contract(True)))
    pipeline.validate_plan(
        parse_plan(
            MERGE.replace("by=contract.routing", "by=contract.routing, against=target"),
            contract(True),
        )
    )


@pytest.mark.parametrize("flag", [None, False, True])
async def test_conflict_admission_freezes_executor_without_changing_service_default(
    agent_loop, pipeline, tmp_path, monkeypatch, flag
):
    loop, _, _ = agent_loop
    loop.config.storage_workspace = str(tmp_path / "service")
    loop.config.compile.mode = "simple"
    client = SimpleNamespace(
        attrs=AsyncMock(side_effect=lambda uri: {"uri": uri}),
        stat=AsyncMock(side_effect=lambda uri: {"uri": uri, "isDir": True}),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "vikingbot.compile.service.VikingClient.create", AsyncMock(return_value=client)
    )
    service = BotCompileService(agent_loop=loop, limits=pipeline.limits)
    monkeypatch.setattr(service, "_openviking_auth_mode", lambda: "dev")
    # Admission and durable request are real; keep execution outside this assertion.
    monkeypatch.setattr(service, "_run_admitted_task", AsyncMock())
    config = SimpleNamespace(
        gateway=SimpleNamespace(host="127.0.0.1", token="synthetic-gateway"),
        ov_server=SimpleNamespace(
            server_url="http://127.0.0.1:1933", effective_auth_mode="dev", api_key_type="user"
        ),
    )
    channel = OpenAPIChannel(
        OpenAPIChannelConfig(),
        MessageBus(),
        workspace_path=tmp_path,
        global_config=config,
        compile_service=service,
    )
    monkeypatch.setattr(
        channel, "_assert_runtime_upstream_auth_mode", AsyncMock(return_value={"status": "ok"})
    )
    app = FastAPI()
    app.include_router(channel.get_gateway_router())
    payload = {
        "from": ["viking://resources/source"],
        "to": pipeline.target,
        "skill": pipeline.request.skill,
    }
    if flag is not None:
        payload["args"] = {"global_conflict_detection": flag}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as http:
            accepted = await http.post(
                "/runtime/v1/tasks",
                headers={"X-Gateway-Token": config.gateway.token},
                json={"task_type": "compile", "payload": payload},
            )
        assert accepted.status_code == 202, accepted.text
        task = await service.store.get(accepted.json()["task_id"])
        assert task.meta["mode"] == ("complex" if flag else "simple")
        assert task.sanitized_request.global_conflict_detection is bool(flag)
        assert loop.config.compile.mode == "simple"
    finally:
        await service.close()


async def test_global_comparison_runs_map_shuffle_reduce_and_publishes_evidence(
    pipeline, monkeypatch
):
    from vikingbot.compile.plan import Routing

    pipeline.request = pipeline.request.model_copy(update={"global_conflict_detection": True})
    proposal = PlanProposal(contract=contract(True), plan=MERGE)
    proposal.contract.routing = Routing(mode="all")

    async def chat(**kwargs):
        if kwargs["messages"][0]["content"].startswith("Choose"):
            return LLMResponse(content=proposal.model_dump_json())
        return LLMResponse(content='{"action":"continue"}')

    pipeline.model.provider.chat = AsyncMock(side_effect=chat)
    stages = []
    sentence = (
        "The same-version sources disagree: the limit is 7 or 9; no precedence is established."
    )
    record = {
        "match_text": sentence,
        "problem_description": "The same scope has two limits.",
        "reason": "Source 0 specifies 7; source 1 specifies 9 under the same conditions.",
        "severity": "medium",
        "state": "unfixed",
        "begin_time": 1794900000,
        "fix_plan": "",
        "fix_time": None,
        "resolution_basis": "",
        "conflict_type": "fact",
        "resolved_by": "",
    }

    async def worker(system, data, model, *, stage, runtime):
        stages.append(stage)
        root = f"__compile_staging__/drafts/{stage}/output"
        if data.get("documents"):
            assert len(data["documents"]) == 2
            for doc in data["documents"]:
                assert "Source limit" in await runtime.files.sandbox.read_file(
                    doc["workspace_path"]
                )
            body = (
                "# Limits\n\n"
                + sentence
                + "\n\n## Sources\n"
                + "\n".join(f"- [Source {i}](viking://resources/source/{i}.md)" for i in range(2))
                + "\n\n<!-- CONFLICTS_CONTENT\n"
                + json.dumps({**record, "begin_time": data["runtime"]["unix_seconds"]})
                + "\n-->\n"
            )
        else:
            ref = data["inputs"][0]["id"]
            source = await runtime.files.get("sources/" + ref)
            body = "# Source limit\n\n" + source["text"]
        await runtime.files.sandbox.write_file(root + "/limits.md", body)
        return root

    pipeline.model.agent_runner = worker

    async def contradictory_sources():
        for index, limit in enumerate((7, 9)):
            yield split_source(
                f"viking://resources/source/{index}.md",
                f"Source limit is {limit} under the same production conditions in v2.",
                1000,
            )

    bundle = await pipeline.run(contradictory_sources())
    assert len(stages) == 3 and stages[-1].startswith("files-")
    assert len(bundle.operations) == 1 and sentence in bundle.operations[0]["content"]
    ledger = await pipeline.files.get("runtime")
    assert ledger["global_conflict_detection"] is True
    outputs = (await pipeline.files.get("finalize"))["outputs"]
    assert len(outputs["limits.md"]["source_refs"]) == 2
