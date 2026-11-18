"""Markdown compile stage selection, isolated file workers and publication boundaries."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.compile import CompileChildTool
from vikingbot.agent.tools.filesystem import WriteFileTool
from vikingbot.bus.queue import MessageBus
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
from vikingbot.config.schema import Config, SessionKey
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
        assert all(
            "scope" not in d and "routing_text" not in d and "path" not in d
            for d in data["documents"]
        )
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
    groups = await Shuffle(pipeline).run(node, docs)
    assert [group.documents for group in groups] == [[docs[0]], [docs[1]]]
    assert "Related documents must be supplied candidates" in pipeline.warnings[-1]


@pytest.mark.parametrize("fail_all", [False, True])
async def test_failed_routes_keep_completed_drafts_through_finalization(
    pipeline, monkeypatch, agent_loop, fail_all
):
    from vikingbot.compile.ops import reduce as reduce_op

    pipeline.contract = contract(True)
    pipeline.limits = pipeline.limits.model_copy(update={"shuffle_batch_size": 1})
    seeded = await pipeline.seed(sources(3))
    docs = [
        Document(f"{i}.md", f"drafts/{i}.md", [source.source_id]) for i, source in enumerate(seeded)
    ]
    for i, doc in enumerate(docs):
        await pipeline.files.sandbox.write_file(doc.workspace_path, f"# Fact {i}\n")
        pipeline.status[doc.source_refs[0]] = "prepared"
    monkeypatch.setattr(Shuffle, "vectors", AsyncMock(return_value=[[1.0, 0.0]] * 3))

    async def ask(stage, system, data, schema, validate, **kwargs):
        item = data["assignments"][0]
        if fail_all or item["document"] == docs[0].workspace_path:
            raise ValueError("route: Return exactly one decision per assigned document")
        response = RouteResponse(
            decisions=[{"document": item["document"], "related": item["candidates"]}]
        )
        validate(response)
        return response

    pipeline.model.ask = ask
    nodes = parse_plan(MERGE, pipeline.contract)
    groups = await Shuffle(pipeline).run(nodes[1], docs)
    expected = [[doc] for doc in docs] if fail_all else [[docs[0]], docs[1:]]
    assert [group.documents for group in groups] == expected
    assert all(not group.target_uris for group in groups)
    assert "processed independently" in pipeline.warnings[-1]
    assert "Return exactly one decision" in pipeline.warnings[-1]
    assert not pipeline.failures

    loop, key, _ = agent_loop
    current_path = None

    async def chat(**kwargs):
        nonlocal current_path
        tools = [m["content"] for m in kwargs["messages"] if m["role"] == "tool"]
        if not tools:
            calls = [ToolCallRequest("assignment", "read_file", {"path": "assignment.json"}, 0)]
        elif tools[-1].startswith("{"):
            assignment = json.loads(tools[-1])
            current_path = assignment["documents"][0]["path"]
            calls = [
                ToolCallRequest(str(i), "read_file", {"path": d["content_file"]}, i)
                for i, d in enumerate(assignment["documents"])
            ]
        elif tools[-1].startswith("# Fact"):
            content = "\n".join(t for t in tools if t.startswith("# Fact"))
            calls = [
                ToolCallRequest(
                    "write", "write_file", {"path": current_path, "content": content}, 0
                )
            ]
        else:
            return LLMResponse(content="Done", finish_reason="stop")
        return LLMResponse(content="", tool_calls=calls)

    pipeline.model.provider.chat = AsyncMock(side_effect=chat)
    pipeline.model.agent_runner = agent_runner(loop, key, None, pipeline.limits)
    outputs = await reduce_op.run(pipeline, nodes[2], groups)
    bundle = await finalize.run(pipeline, outputs)
    assert all(
        f"# Fact {i}" in "\n".join(op["content"] for op in bundle.operations) for i in range(3)
    )
    await pipeline.write_coverage([op["uri"] for op in bundle.operations])
    assert (await pipeline.files.get("coverage"))["counts"] == {"delivered": 3}


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


@pytest.fixture
async def routing_embeddings(pipeline, monkeypatch):
    """Run the Bot adapter against the HTTP route and a real SDK on synthetic HTTP."""
    import httpx
    import openai
    from fastapi import FastAPI
    from vikingbot.openviking_mount.ov_server import VikingClient

    from openviking.models.embedder.base import FailoverEmbedder
    from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
    from openviking.models.embedder.retry_embedder import RetryEmbedder
    from openviking.server.auth import get_request_context
    from openviking.server.identity import RequestContext, Role
    from openviking.server.routers import compile as api
    from openviking_cli.client.http import AsyncHTTPClient
    from openviking_cli.session.user_id import UserIdentifier

    calls = []
    outcomes: dict[str, tuple[int | None, Any]] = {}
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def respond(request):
        text = json.loads(request.content)["input"]
        calls.append(text)
        status, body = outcomes.get(text, (200, [1.0, 0.0]))
        if status is None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        assert status is not None
        if status == 200:
            body = {
                "data": [{"embedding": body, "index": 0, "object": "embedding"}],
                "model": "test",
                "object": "list",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        return httpx.Response(status, json=body)

    inner = OpenAIDenseEmbedder(model_name="test", api_key="test", dimension=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider_http:
        sdk = openai.AsyncOpenAI(api_key="test", max_retries=0, http_client=provider_http)
        monkeypatch.setattr(inner, "_get_async_client", lambda: sdk)
        backup = OpenAIDenseEmbedder(model_name="test", api_key="backup", dimension=2)
        backup_request = AsyncMock(side_effect=AssertionError("No credential switch"))
        monkeypatch.setattr(backup, "embed_async", backup_request)
        embedder = FailoverEmbedder([RetryEmbedder(inner), backup], ["primary", "backup"])
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role(Role.USER))
        fs = SimpleNamespace(stat=AsyncMock(), _get_embedder=lambda _: embedder)
        service = SimpleNamespace(
            viking_fs=fs,
            embedding_provider=SimpleNamespace(
                get_status=AsyncMock(return_value=SimpleNamespace(fingerprint="model-v1"))
            ),
        )
        monkeypatch.setattr(api, "get_service", lambda: service)
        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[get_request_context] = lambda: ctx
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http:
            client = AsyncHTTPClient(url="http://test")
            client._http = http
            adapter = VikingClient.__new__(VikingClient)
            adapter.client = client
            pipeline.client.compile_embeddings = adapter.compile_embeddings
            yield SimpleNamespace(
                http=http,
                app=app,
                calls=calls,
                outcomes=outcomes,
                fs=fs,
                inner=inner,
                backup=backup,
                started=started,
                cancelled=cancelled,
            )
        backup_request.assert_not_awaited()
        inner.close()
        backup.close()


async def test_endpoint_partial_451_preserves_positions(pipeline, routing_embeddings):
    env = routing_embeddings
    env.outcomes["refused"] = (451, {"error": {"message": "Unavailable"}})
    body = {"target_uri": pipeline.target, "texts": ["alpha", "refused", "beta"]}
    response = await env.http.post("/api/v1/compile/embeddings?allow_partial_451=true", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "model": "model-v1",
        "vectors": [[1.0, 0.0], None, [1.0, 0.0]],
    }
    assert sorted(env.calls) == ["alpha", "beta", "refused"]
    env.fs.stat.assert_awaited_once()
    # Legacy callers still receive an error; successful legacy responses keep their shape.
    assert (await env.http.post("/api/v1/compile/embeddings", json=body)).status_code == 500
    body["texts"] = ["alpha"]
    response = await env.http.post("/api/v1/compile/embeddings", json=body)
    assert response.json()["result"] == {"model": "model-v1", "vectors": [[1.0, 0.0]]}


async def test_shuffle_partial_451_retains_semantic_and_cached_candidates(
    pipeline, routing_embeddings
):
    from vikingbot.compile.hashing import digest

    pipeline.contract = contract(True)
    texts = ["alpha", "beta", "refused shared", "shared"]
    docs = [Document(f"{i}.md", f"drafts/{i}.md") for i in range(len(texts))]
    for doc, text in zip(docs, texts, strict=True):
        await pipeline.files.sandbox.write_file(doc.workspace_path, text)
    routing_embeddings.outcomes[texts[2]] = (451, {"error": {"message": "Unavailable"}})
    cache_key = f"embeddings/{digest(['model-v1', texts[1]])}"
    await pipeline.files.put(cache_key, [1.0, 0.0])
    assigned = {}

    async def ask(stage, system, data, schema, validate, **kwargs):
        assigned.update({a["document"]: a["candidates"] for a in data["assignments"]})
        response = RouteResponse.model_validate(
            {"decisions": [{"document": a["document"]} for a in data["assignments"]]}
        )
        validate(response)
        return response

    pipeline.model.ask = ask
    node = parse_plan(MERGE, pipeline.contract)[1]
    shuffle = Shuffle(pipeline)
    for _ in range(2):
        groups = await shuffle.run(node, docs)
        assert [g.documents for g in groups] == [[d] for d in docs]
        assert assigned[docs[0].workspace_path] == [docs[i].workspace_path for i in (1, 3)]
        assert assigned[docs[1].workspace_path] == [docs[i].workspace_path for i in (0, 3)]
        assert assigned[docs[2].workspace_path] == [docs[3].workspace_path]
        assert assigned[docs[3].workspace_path] == [docs[i].workspace_path for i in (0, 1)]
    assert sorted(routing_embeddings.calls) == sorted([texts[0], texts[2], texts[2], texts[3]])
    assert await pipeline.files.get(cache_key) == [1.0, 0.0]
    assert await pipeline.files.get(f"embeddings/{digest(['model-v1', texts[2]])}") is None
    assert pipeline.warnings


@pytest.mark.parametrize(
    "status,body,kind",
    [
        (451, {"error": {"code": "content_policy_violation"}}, "content_safety"),
        (451, {"error": {"code": "unrecognized_code"}}, "unknown"),
        (403, {"error": {"message": "Denied"}}, "permission_denied"),
        (200, [], None),
    ],
)
async def test_partial_451_keeps_other_errors(pipeline, routing_embeddings, status, body, kind):
    import httpx

    from openviking.models.embedder.retry_embedder import RetryEmbedder
    from openviking.models.model_error import ModelError
    from openviking_cli.exceptions import InvalidArgumentError

    env = routing_embeddings
    env.fs._get_embedder = lambda _: RetryEmbedder(env.inner)
    env.outcomes["bad"] = (status, body)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url="http://test"
    ) as http:
        with pytest.raises(ModelError if kind else InvalidArgumentError) as exc:
            await http.post(
                "/api/v1/compile/embeddings?allow_partial_451=true",
                json={"target_uri": pipeline.target, "texts": ["alpha", "bad"]},
            )
    if kind:
        assert isinstance(exc.value, ModelError)
        assert exc.value.kind.value == kind
        assert exc.value.status_code == status
    assert env.calls.count("bad") == 1


@pytest.mark.parametrize("texts", [["refused", "refused"], ["refused", "alpha", "refused"]])
async def test_partial_451_all_refused_and_duplicates(pipeline, routing_embeddings, texts):
    routing_embeddings.outcomes["refused"] = (451, {"error": {"message": "Unavailable"}})
    vectors = await Shuffle(pipeline).vectors(texts)
    assert [None if v is None else list(v) for v in vectors] == [
        None if t == "refused" else [1.0, 0.0] for t in texts
    ]
    assert sorted(routing_embeddings.calls) == sorted(set(texts))


@pytest.mark.parametrize(
    "result",
    [
        {"model": "different", "vectors": [None]},
        {"model": "model-v1", "vectors": []},
    ],
)
async def test_partial_451_rejects_unmatched_response(pipeline, result):
    pipeline.client.compile_embeddings = AsyncMock(
        side_effect=[{"model": "model-v1", "vectors": []}, result]
    )
    with pytest.raises(ValueError, match="Embedding batch/model mismatch"):
        await Shuffle(pipeline).vectors(["alpha"])


async def test_partial_451_cancellation_propagates(pipeline, routing_embeddings):
    env = routing_embeddings
    env.outcomes["waiting"] = (None, None)
    pipeline.contract = contract(True)
    doc = Document("waiting.md", "drafts/waiting.md")
    await pipeline.files.sandbox.write_file(doc.workspace_path, "waiting")
    pipeline.model.ask = AsyncMock()
    task = asyncio.create_task(
        Shuffle(pipeline).run(parse_plan(MERGE, pipeline.contract)[1], [doc])
    )
    try:
        await asyncio.wait_for(env.started.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert env.cancelled.is_set()
    assert env.calls == ["waiting"]
    pipeline.model.ask.assert_not_awaited()
    assert not pipeline.warnings
