"""Markdown compile stage selection, isolated file workers and publication boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.compile import CompileChildTool
from vikingbot.agent.tools.filesystem import WriteFileTool
from vikingbot.bus.queue import MessageBus
from vikingbot.compile import simple
from vikingbot.compile.file_ops import DocumentReader
from vikingbot.compile.models import (
    CompileFailure,
    CompileLimits,
    CompileTask,
    SanitizedCompileRequest,
)
from vikingbot.compile.ops import finalize
from vikingbot.compile.ops import reduce as reduce_op
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

from openviking.utils.compile_recovery import source_set_digest
from openviking_cli.exceptions import NotFoundError

MAP_ONLY = "drafts = p.map(sources, task=contract.extract)\np.finalize(drafts, into=target)"
INVALID_FRONTMATTER = "---\ntype: concept\ntitle: Rules\n description: invalid\n---\n"
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
        git_show_bytes=AsyncMock(),
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


async def configure_pipeline(pipeline, merge=False, fail_second=False, invalid_sibling=False):
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
        if invalid_sibling:
            await model.files.sandbox.write_file(f"{root}/bad.md", INVALID_FRONTMATTER)
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
    if ending == "success":
        replies.insert(
            0,
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("assignment", "read_file", {"path": "assignment.json"}, 0)
                ],
            ),
        )
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
        source_refs = [f"{index:064x}" for index in range(138)]
        inputs = [{"payload": {"source_ranges": source_refs}}]
        result = await pipeline.generate(
            "reduce-0", "Generate Markdown", {"inputs": inputs}, source_refs
        )
        messages = pipeline.model.provider.chat.await_args_list[1].kwargs["messages"]
        assignment = json.loads(next(msg["content"] for msg in messages if msg["role"] == "tool"))
        assert assignment["inputs"] == inputs
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
    assert (await pipeline.files.get("coverage"))["counts"] == {"published": 3}


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


def compile_service_for_test(pipeline, task, monkeypatch):
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
    return service


@pytest.mark.parametrize("case", ["normal", "cancelled", "conflict", "source", "worker", "sibling"])
async def test_service_publishes_final_markdown_with_batch_write_and_honors_cancel(
    pipeline, monkeypatch, case
):
    source_files = [pipeline.request.from_[0] + "/one.md"]
    output_files = [pipeline.target + "/topic.md"]
    if case in {"source", "worker"}:
        source_files.append(pipeline.request.from_[0] + "/two.md")
    if case == "conflict":
        pipeline.client.batch_write.return_value = {
            "created": [],
            "conflicts": [
                {
                    "uri": output_files[0],
                    "code": "ALREADY_EXISTS",
                    "message": "Exists",
                }
            ],
        }
    await configure_pipeline(
        pipeline, fail_second=case == "worker", invalid_sibling=case == "sibling"
    )
    if case == "source":
        pipeline.client.read_raw.side_effect = [
            "source fact",
            ConnectionError("Source unavailable"),
        ]
    task = SimpleNamespace(
        status="cancelling" if case == "cancelled" else "running", meta={}, result=None
    )
    service = compile_service_for_test(pipeline, task, monkeypatch)
    call = service._run_pipeline(
        task_id="test",
        request=pipeline.request,
        client=pipeline.client,
        sandbox=pipeline.files.sandbox,
        skill_text=pipeline.skill,
        source_files=source_files,
        usage={},
        agent_runner=pipeline.model.agent_runner,
    )
    if case == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await call
        pipeline.client.batch_write.assert_not_awaited()
    else:
        await call
        write = pipeline.client.batch_write.await_args.kwargs
        assert write["root_uri"] == pipeline.target and write["skip_conflicts"] is True
        assert write["operations"] == [
            {
                "uri": output_files[0],
                "content": "# Topic\n\n[Source](viking://resources/source/one.md)\n",
                "mode": "create",
            }
        ]
        assert task.status == ("failed" if case == "conflict" else "completed")
        assert task.result.created == ([] if case == "conflict" else output_files)
        assert task.result.page_count == len(task.result.created)
        assert bool(task.result.conflicts) == (case == "conflict")
        assert task.result.completion_status == (None if case == "normal" else "partial")


def incremental_request(request, **changes):
    return request.model_copy(
        update={
            "changes": {
                "execution": {"mode": "incremental"},
                "base": "base",
                "head": "head",
                "diff_dirs": request.from_,
                "full_dirs": [],
                **changes,
            }
        }
    )


@pytest.mark.parametrize("read_all", [True, False])
async def test_complex_recovery_requires_read_evidence_for_unchanged_drafts(
    pipeline, monkeypatch, read_all
):
    await configure_pipeline(pipeline)
    original_worker = pipeline.model.agent_runner
    source_files = [pipeline.request.from_[0] + "/" + name for name in ("one.md", "two.md")]
    expected = source_set_digest(source_files)
    pipeline.request = incremental_request(pipeline.request, recovery_sources=expected)
    pipeline.changes = pipeline.request.changes
    page = "# Topic\n\n[Source](viking://resources/source/one.md)\n"
    pipeline.old.update({"topic.md": page, "independent.md": page})
    pipeline.client.download_bytes.return_value = page.encode()
    pipeline.client.git_show_bytes = AsyncMock(return_value=b"source fact")

    async def worker(system, data, model, *, stage, runtime):
        assigned = data["inputs"][0]
        if read_all or assigned["payload"]["uri"] == source_files[0]:
            runtime.recovery_evidence.add(assigned["id"])
        return await original_worker(system, data, model, stage=stage, runtime=runtime)

    task = SimpleNamespace(status="running", meta={}, result=None)
    service = compile_service_for_test(pipeline, task, monkeypatch)
    await service._run_pipeline(
        task_id="test",
        request=pipeline.request,
        client=pipeline.client,
        sandbox=pipeline.files.sandbox,
        skill_text=pipeline.skill,
        source_files=source_files,
        usage={},
        agent_runner=worker,
    )
    assert task.status == "completed"
    assert task.result.completion_status is None
    assert len(task.result.unchanged) == 2
    pipeline.client.batch_write.assert_not_awaited()
    assert task.result.recovery_proof == (expected if read_all else None)


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


@pytest.mark.parametrize(
    ("collision", "names"),
    [
        ("same_worker", ("topic/Retry.md", "TOPIC/retry.md")),
        ("other_worker", ("topic/Café.md", "topic/Cafe\u0301.md")),
    ],
)
async def test_alias_conflicts_retry_without_blocking_unrelated_publication(
    pipeline, collision, names
):
    paths = ["a.md", "a.md"] + (["b.md", "b.md"] if collision == "other_worker" else [])
    docs = [Document(path, f"candidate-{i}.md", [f"source-{i}"]) for i, path in enumerate(paths)]
    unrelated = Document("unrelated.md", "unrelated.md", ["other-source"])
    first = Document(names[0], "first.md")
    alias = Document(names[1], "alias.md")
    merged = Document("merged.md", "merged.md")
    for doc in [unrelated, first, alias, merged]:
        await pipeline.files.sandbox.write_file(doc.workspace_path, "# Facts\n\nPreserved.")
    results = (
        [[first], [alias], [merged]] if collision == "other_worker" else [[first, alias], [merged]]
    )
    worker = AsyncMock(side_effect=results)
    pipeline.generate = pipeline.generate_once = worker
    outputs = await reduce_op.resolve_files(pipeline, [*docs, unrelated])
    assert pipeline.generate.await_count == len(results)
    assert outputs == (
        [unrelated, first, merged] if collision == "other_worker" else [unrelated, merged]
    )
    bundle = await finalize.run(pipeline, outputs)
    assert {item["uri"] for item in bundle.operations} == {
        pipeline.target + "/" + doc.path for doc in outputs
    }
    assert any(item["uri"].endswith("/unrelated.md") for item in bundle.operations)


@pytest.mark.parametrize(
    "content,expected_pattern",
    [
        (
            "---\ntype: concept\ntitle: Rules\n description: leading space breaks YAML\n---\n\nBody\n",
            r"invalid YAML frontmatter: .*mapping values are not allowed here \(line 4, column 13\)",
        ),
        (
            "---\n type: concept\ntitle: Rules\n---\n\nBody\n",
            r"invalid YAML frontmatter: .*line 3, column 1",
        ),
    ],
)
async def test_compile_write_tool_rejects_invalid_okf_frontmatter(content, expected_pattern):
    from vikingbot.compile.simple import CompileWriteTool

    client = SimpleNamespace(
        read_raw=AsyncMock(side_effect=NotFoundError("not found")),
        write=AsyncMock(),
    )
    tool = CompileWriteTool(client, "viking://resources/distill", AsyncMock())
    ctx = ToolContext(session_key=SessionKey(type="cli", channel_id="test", chat_id="fm"))

    with pytest.raises(ValueError, match=expected_pattern):
        await tool.execute(ctx, path="selection-rules.md", content=content)
    client.write.assert_not_awaited()
    assert tool.outcomes == {}


async def test_compile_write_tool_allows_valid_okf_frontmatter():
    from vikingbot.compile.simple import CompileWriteTool

    uri = "viking://resources/distill/selection-rules.md"
    client = SimpleNamespace(
        read_raw=AsyncMock(side_effect=NotFoundError("not found")),
        write=AsyncMock(return_value={"uri": uri, "content_updated": True}),
    )
    tool = CompileWriteTool(client, "viking://resources/distill", AsyncMock())
    ctx = ToolContext(session_key=SessionKey(type="cli", channel_id="test", chat_id="fm"))
    valid = "---\ntype: concept\ntitle: Rules\ndescription: Valid YAML\n---\n\nBody\n"

    outcome = await tool.execute(ctx, path="selection-rules.md", content=valid)
    assert outcome == f"created: {uri}"
    client.write.assert_awaited_once()


async def test_simple_compile_reports_rejected_output_when_no_page_was_written(
    pipeline, agent_loop
):
    loop, session_key, _ = agent_loop
    pipeline.client.write = AsyncMock()
    pipeline.model.provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        "write",
                        "openviking_write",
                        {
                            "path": "selection-rules.md",
                            "content": "---\ntype: concept\ntitle: Rules\n description: invalid\n---\n",
                        },
                        0,
                    )
                ],
            ),
            LLMResponse(content="Done"),
        ]
    )

    with pytest.raises(CompileFailure, match=r"selection-rules\.md.*line 4, column 13") as caught:
        await simple.run(
            loop,
            pipeline.client,
            pipeline.request,
            pipeline.request.from_,
            pipeline.skill,
            session_key,
            None,
            AsyncMock(),
            {},
        )

    assert caught.value.code == "COMPILE_INCOMPLETE"
    pipeline.client.write.assert_not_awaited()


async def run_simple_incremental(pipeline, agent_loop, request, calls, *replies):
    loop, session_key, _ = agent_loop
    pipeline.model.provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(content="", tool_calls=[ToolCallRequest(str(index), name, args, 0)])
            for index, (name, args) in enumerate(calls)
        ]
        + list(replies)
    )
    return await simple.run(
        loop,
        pipeline.client,
        request,
        request.from_,
        pipeline.skill,
        session_key,
        None,
        AsyncMock(),
        {},
    )


@pytest.mark.parametrize("recovered", [False, True])
async def test_simple_incremental_delivery_preserves_rejections(pipeline, agent_loop, recovered):
    pipeline.client.write = AsyncMock(
        side_effect=lambda uri, *args, **kwargs: {"uri": uri, "content_updated": True}
    )
    pipeline.client.diff_paths = AsyncMock(return_value={"changes": [], "has_more": False})
    calls = [
        ("openviking_diff_paths", {}),
        ("openviking_write", {"path": "good.md", "content": "# Good"}),
        ("openviking_write", {"path": "bad.md", "content": INVALID_FRONTMATTER}),
    ]
    if recovered:
        calls.append(("openviking_write", {"path": "bad.md", "content": "# Rules"}))
    result = await run_simple_incremental(
        pipeline,
        agent_loop,
        incremental_request(pipeline.request),
        calls,
        LLMResponse(content="Done"),
    )
    assert pipeline.target + "/good.md" in result.updated
    public = CompileTask.model_construct(result=result).public_dict()["result"]
    if recovered:
        assert "completion_status" not in public
    else:
        assert public["completion_status"] == "partial"


@pytest.mark.parametrize(
    "retry_args,expected_status",
    [
        ({"offset": 40, "limit": 20}, "partial"),
        ({"offset": 20, "limit": 10}, "partial"),
        ({"offset": 20}, None),
    ],
)
async def test_simple_incremental_diff_failure_tracks_page(
    pipeline, agent_loop, retry_args, expected_status
):
    pipeline.client.write = AsyncMock(
        side_effect=lambda uri, *args, **kwargs: {"uri": uri, "content_updated": True}
    )
    pipeline.client.diff_paths = AsyncMock(
        side_effect=[
            {"changes": [], "has_more": True, "next_offset": 20},
            ConnectionError("Source diff page unavailable"),
            {"changes": [], "has_more": False},
        ]
    )
    calls = [
        ("openviking_diff_paths", {}),
        ("openviking_diff_paths", {"offset": 20, "limit": 20}),
        ("openviking_diff_paths", retry_args),
        ("openviking_write", {"path": "good.md", "content": "# Good"}),
    ]
    result = await run_simple_incremental(
        pipeline,
        agent_loop,
        incremental_request(pipeline.request),
        calls,
        LLMResponse(content="Done"),
    )
    assert result.updated == [pipeline.target + "/good.md"]
    assert result.completion_status == expected_status


@pytest.mark.parametrize("case", ["reminded", "omitted", "sliced", "stale"])
async def test_simple_recovery_requires_each_changed_source_and_its_frozen_content(
    pipeline, agent_loop, case
):
    alpha, beta = (pipeline.request.from_[0] + "/" + name for name in ("alpha.md", "beta.md"))
    target = pipeline.target + "/topic.md"
    expected = source_set_digest([alpha, beta])
    request = incremental_request(pipeline.request, recovery_sources=expected)
    pipeline.client.diff_paths = AsyncMock(
        return_value={
            "changes": [{"uri": uri, "change_type": "modified"} for uri in (alpha, beta)],
            "has_more": False,
        }
    )
    pipeline.client.download_bytes = AsyncMock(side_effect=lambda uri: uri.encode())
    pipeline.client.read_content = AsyncMock(return_value="A window, not the whole file")
    pipeline.client.git_show_bytes = AsyncMock(
        side_effect=lambda uri, _: (
            b"Different bytes" if uri == beta and case == "stale" else uri.encode()
        )
    )
    beta_read = {"uris": [beta]}
    if case == "sliced":
        beta_read["limit"] = 1
    calls = [
        ("openviking_diff_paths", {}),
        ("openviking_multi_read", {"uris": [alpha, target]}),
        ("openviking_multi_read", beta_read),
    ]
    confirmed = {alpha: [target]}
    if case != "omitted":
        confirmed[beta] = [target]
    confirmation = LLMResponse(
        content="The targets already match.\nUNCHANGED_SOURCES: " + json.dumps(confirmed)
    )
    first_reply = (
        LLMResponse(content="Done without changes.") if case == "reminded" else confirmation
    )
    result = await run_simple_incremental(
        pipeline, agent_loop, request, calls, first_reply, confirmation
    )
    assert result.recovery_proof == (expected if case == "reminded" else None)
    task = CompileTask.model_construct(result=result)
    assert "recovery_proof" not in task.public_dict()["result"]
    assert task.public_dict(include_recovery_proof=True)["result"].get("recovery_proof") == (
        result.recovery_proof
    )


async def test_finalize_skips_invalid_okf_frontmatter_and_publishes_valid_pages(pipeline):
    pipeline.evidence["input"] = {"uri": "viking://resources/source/a.md"}
    pipeline.status["input"] = "prepared"
    valid_draft = "__compile_staging__/drafts/valid/valid.md"
    invalid_draft = "__compile_staging__/drafts/invalid/selection-rules.md"
    await pipeline.files.sandbox.write_file_bytes(
        valid_draft,
        b"---\ntype: concept\ntitle: Valid\ndescription: Clean YAML\n---\n\nBody\n",
    )
    await pipeline.files.sandbox.write_file_bytes(
        invalid_draft,
        b"---\ntype: concept\ntitle: Rules\n description: leading space\n---\n\nBody\n",
    )

    bundle = await finalize.run(
        pipeline,
        [
            Document("valid.md", valid_draft, ["input"]),
            Document("selection-rules.md", invalid_draft, ["input"]),
        ],
    )
    assert [op["uri"] for op in bundle.operations] == ["viking://resources/wiki/valid.md"]
    assert bundle.created == ["viking://resources/wiki/valid.md"]
    assert len(pipeline.warnings) == 1
    assert "selection-rules.md" in pipeline.warnings[0]
    assert "mapping values are not allowed here (line 4, column 13)" in pipeline.warnings[0]

    pipeline.warnings.clear()
    empty_bundle = await finalize.run(
        pipeline,
        [Document("selection-rules.md", invalid_draft, ["input"])],
    )
    assert empty_bundle.operations == []
    assert len(pipeline.warnings) == 1
    assert "selection-rules.md" in pipeline.warnings[0]
