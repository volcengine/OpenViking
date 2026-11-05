# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Issue #6713: native images must remain readable, cited evidence in Compile."""

import asyncio
import base64
import json
from dataclasses import asdict
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest
from PIL import Image, ImageDraw
from vikingbot.agent.loop import AgentLoop, _inline_media_bytes
from vikingbot.agent.tools.base import MultimodalToolResult, ToolContext
from vikingbot.agent.tools.compile import CompileImageReadTool, CompileSpawnTool
from vikingbot.agent.tools.ov_file import VikingMultiReadTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.bus.queue import MessageBus
from vikingbot.compile import entry, file_ops
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import (
    COMPILE_STAGING_ROOT,
    CompileFailure,
    CompileLimits,
    SanitizedCompileRequest,
)
from vikingbot.compile.ops import common
from vikingbot.compile.ops import finalize as finalize_op
from vikingbot.compile.ops.common import job, pack, payload
from vikingbot.compile.ops.reduce import reduce_group, resolve_files
from vikingbot.compile.pipeline import Pipeline
from vikingbot.compile.pipeline_agent import ChildProvider, agent_runner, complete_tool_result
from vikingbot.compile.pipeline_io import MAX_JOB_ATTEMPTS, ModelCallError
from vikingbot.compile.plan import DEFAULT_PLAN, Contract, Node, PlanProposal, Transform, parse_plan
from vikingbot.compile.results import (
    CombineResponse,
    FileDraft,
    FileResponse,
    Group,
    Record,
    RecordResponse,
)
from vikingbot.compile.sandbox import CompileSandboxManager
from vikingbot.compile.service import BotCompileService, _CompileProvider
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.compile.sources import read_source, split_source
from vikingbot.config import loader
from vikingbot.config.schema import Config, SandboxConfig, SessionKey
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend
from vikingbot.sandbox.base import SandboxFileInfo
from vikingbot.session.manager import SessionManager

from openviking.models.model_error import ModelError, ModelErrorKind

URI = "viking://resources/cedar/diagram.png"
TEXT = "viking://resources/cedar/notes.md"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["managed", "remote", "mismatched_mount", "backup_failure"])
async def test_compile_cleanup_preserves_evidence_before_releasing_sandbox(
    tmp_path, monkeypatch, mode
):
    workspace = tmp_path / "task"
    relative = f"{COMPILE_STAGING_ROOT}/sources/evidence.json"
    saved = workspace / relative
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"already persisted")
    mounted = workspace if mode == "managed" else tmp_path / "another-mount"
    backend = OpenSandboxBackend(
        SandboxConfig.model_validate(
            {"backends": {"opensandbox": {"managed": mode in {"managed", "mismatched_mount"}}}}
        ),
        SessionKey(type="compile", channel_id="test", chat_id="cleanup"),
        workspace,
        host_workspace=mounted,
    )
    listing = AsyncMock(return_value=[SandboxFileInfo(relative, 15)])
    reading = AsyncMock(return_value=b"remote evidence")
    if mode == "managed":
        listing.side_effect = AssertionError("A mounted workspace needs no remote enumeration")
    if mode == "backup_failure":
        reading.side_effect = OSError("remote copy unavailable")
    monkeypatch.setattr(backend, "list_files", listing)
    monkeypatch.setattr(backend, "read_file_bytes", reading)
    manager = create_autospec(CompileSandboxManager, instance=True)
    manager.get_sandbox.return_value = backend
    manager.get_workspace_path.return_value = workspace
    client = create_autospec(VikingClient, instance=True)
    service = object.__new__(BotCompileService)
    service.limits = CompileLimits(cleanup_grace_seconds=2)
    await service._cleanup_execution_resources(
        sandbox_manager=manager,
        session_key=SessionKey(type="compile", channel_id="test", chat_id="cleanup"),
        client=client,
        workspace_parent=workspace,
        preserve_workspace=True,
    )
    manager.clear_credentials.assert_awaited_once()
    client.close.assert_awaited_once()
    if mode == "backup_failure":
        manager.cleanup_session.assert_not_awaited()
        assert saved.read_bytes() == b"already persisted"
    else:
        manager.cleanup_session.assert_awaited_once()
        assert saved.read_bytes() == (
            b"already persisted" if mode == "managed" else b"remote evidence"
        )
    if mode == "managed":
        listing.assert_not_awaited()
        reading.assert_not_awaited()


class MemoryFiles:
    def __init__(self):
        self.values = {}

    async def put(self, key, value):
        self.values[key] = value
        return key

    async def get(self, key):
        return self.values.get(key)


@pytest.fixture
def runtime():
    image = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(image).text((10, 30), "RETRY LIMIT: 7", fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    png = output.getvalue()
    client = SimpleNamespace(
        stat=AsyncMock(return_value={"size": len(png)}),
        download_bytes=AsyncMock(return_value=png),
        read_raw=AsyncMock(return_value="# Cedar\n![settings](diagram.png)\n"),
        read_content=AsyncMock(return_value="visible text"),
    )
    provider = SimpleNamespace(
        supports_tool_result_media=Mock(return_value=False), chat=AsyncMock()
    )
    pipeline = Pipeline(
        client=client,
        provider=provider,
        sandbox=SimpleNamespace(sandbox_cwd="/sandbox"),
        model="vision-test",
        temperature=0,
        limits=CompileLimits(),
        skill="Cite source facts.",
        usage={},
        request=SanitizedCompileRequest(
            **{
                "from": ["viking://resources/cedar"],
                "to": "viking://resources/wiki",
                "skill": "viking://agent/skills/llm-wiki",
                "instruction": "Use image-only facts.",
            }
        ),
    )
    pipeline.files = MemoryFiles()
    pipeline.model.files, pipeline.model.resources = pipeline.files, None
    return pipeline, png


async def seed(runtime):
    pipeline, _ = runtime
    service = SimpleNamespace(
        limits=pipeline.limits, agent_loop=SimpleNamespace(provider=pipeline.model.provider)
    )
    batches = await BotCompileService._prepare_source_batches(
        service, pipeline.client, [], files=[TEXT, URI]
    )

    async def inputs():
        for batch in batches:
            yield batch

    records = await pipeline.seed(inputs())
    return records[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("result_mode", ["files", "pipeline", "noop"])
async def test_preloaded_image_fact_reaches_output_with_source_identity(runtime, result_mode):
    pipeline, png = runtime
    image = await seed(runtime)
    if result_mode != "files":
        pipeline.changes = {"diff_dirs": [], "full_dirs": pipeline.request.from_, "head": "head"}
    stored = await pipeline.files.get(image.payload_ref)
    assert "text" not in stored and stored["hash"] == content_hash(png)
    assert (stored["start_line"], stored["end_line"], stored["start_char"], stored["end_char"]) == (
        0,
        0,
        0,
        0,
    )
    data = {"inputs": [await payload(pipeline, image)]}
    assert "base64," not in json.dumps(data)
    pipeline.client.read_raw.assert_awaited_once_with(TEXT)

    def respond(**kwargs):
        if pipeline.model.provider.chat.await_count == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        "read",
                        "read_evidence",
                        {"source_range": image.record_id},
                        0,
                    )
                ],
            )
        content = kwargs["messages"][-1]["content"]
        attached = next(p for p in content if p["type"] == "image_url")
        assert base64.b64decode(attached["image_url"]["url"].split(",")[1]) == png
        assert "RETRY LIMIT" not in json.dumps(data)
        if result_mode == "noop":
            return LLMResponse(content='{"files":[],"no_changes_reason":"No target changes."}')
        if result_mode == "pipeline":
            return LLMResponse(content='{"needs_pipeline":true,"files":[]}')
        # Simulated visual response: verifies transport and citation, not real-model OCR.
        return LLMResponse(
            content=json.dumps(
                {
                    "files": [
                        {
                            "path": "cedar.md",
                            "content": f"Retry limit: 7. [Source]({URI})",
                            "inputs": [image.record_id],
                        }
                    ]
                }
            )
        )

    pipeline.model.provider.chat.side_effect = respond
    if result_mode != "files":
        expected = [] if result_mode == "noop" else None
        assert await entry.run(pipeline, list(pipeline.records.values()), {}) == expected
        assert pipeline.noop == (result_mode == "noop")
    else:
        result = await pipeline.model.ask("compile", "Inspect original images.", data, FileResponse)
        assert "Retry limit: 7" in result.files[0].content and URI in result.files[0].content
        assert result.files[0].inputs == [image.record_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend,status", [("volcengine", 200), ("openai", 200), ("openai", 400)])
async def test_compile_image_reaches_chat_completion_wire(
    runtime, tmp_path, monkeypatch, backend, status
):
    """Load Bot configuration and serialize original PNG evidence through the SDK."""
    from openai import AsyncOpenAI
    from vikingbot.cli.commands import _make_provider
    from volcenginesdkarkruntime import AsyncArk

    pipeline, png = runtime
    model = "doubao-seed-2.1-lite"
    api_base = "https://ark.cn-beijing.volces.com/api/coding/v3"
    selected = {
        "provider": backend,
        "model": model,
        "api_base": api_base,
        "api_key": "test-key",
        "max_retries": 0,
    }
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps({"vlm": selected}))
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    pipeline.model.provider = _make_provider(loader.load_config(), langfuse_client=LangfuseClient())
    pipeline.model.model = model
    image = await seed(runtime)

    call = {
        "id": "read-image",
        "type": "function",
        "function": {
            "name": "read_evidence",
            "arguments": json.dumps({"source_range": image.record_id}),
        },
    }
    files = [{"path": "image.md", "content": URI, "inputs": [image.record_id]}]
    replies: list[dict[str, Any]] = [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "assistant", "content": json.dumps({"files": files})},
    ]
    requests = []

    def handle(request):
        body = json.loads(request.content)
        assert str(request.url) == f"{api_base}/chat/completions" and body["model"] == model
        requests.append(body)
        if len(requests) == 2 and status == 400:
            return httpx.Response(
                400, json={"error": {"message": "Model does not support image inputs."}}
            )
        reply = replies[len(requests) - 1]
        return httpx.Response(
            200,
            json={
                "id": "compile-test",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": reply,
                        "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop",
                    }
                ],
            },
        )

    vlm = pipeline.model.provider._vlm.inner
    sdk_type = AsyncArk if backend == "volcengine" else AsyncOpenAI
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http,
        sdk_type(
            api_key=vlm.api_key, base_url=vlm.api_base, http_client=http, max_retries=0
        ) as sdk,
    ):
        monkeypatch.setattr(vlm, "get_async_client", lambda: sdk)
        response = pipeline.model.ask(
            "compile",
            "Inspect the image.",
            {"inputs": [await payload(pipeline, image)]},
            FileResponse,
        )
        if status == 400:
            with pytest.raises(ModelCallError, match="does not support image"):
                await response
        else:
            await response

    sent = requests[-1]["messages"]
    serialized = json.dumps(sent)
    assert URI in serialized and serialized.count(base64.b64encode(png).decode()) == 1
    images = [
        base64.b64decode(p["image_url"]["url"].split(",", 1)[1])
        for m in sent
        if m["role"] == "user" and isinstance(m["content"], list)
        for p in m["content"]
        if p["type"] == "image_url"
    ]
    assert images == [png]


async def test_pipeline_inventory_combines_full_files_and_every_diff_page(runtime):
    pipeline, _ = runtime
    root, full = "viking://resources/cedar", "viking://resources/full"
    uris = [f"{root}/{i:03}.md" for i in range(101)]
    changes = {"diff_dirs": [root], "full_dirs": [full], "base": "base", "head": "head"}
    pipeline.client.diff_paths = AsyncMock(
        side_effect=lambda dirs, base, head, offset: {
            "changes": [{"uri": uri, "change_type": "added"} for uri in uris[:100]]
            if not offset
            else [{"uri": uri, "change_type": "modified"} for uri in uris[99:]]
            + [{"uri": f"{root}/deleted.md", "change_type": "deleted"}],
            "has_more": not offset,
            "next_offset": 100,
        }
    )
    directories = [full + "/nested" * depth for depth in range(5)]
    full_files = [f"{full}/{i:03}.md" for i in range(500)] + [f"{directories[-1]}/notes.md"]
    listings = {
        parent: [{"uri": child, "isDir": True}]
        for parent, child in zip(directories[:-1], directories[1:], strict=True)
    }
    listings[full] += [{"uri": uri} for uri in full_files[:-1]]
    listings[directories[-1]] = [{"uri": full_files[-1]}]
    pipeline.client.stat = AsyncMock(return_value={"isDir": True})
    pipeline.client.list_resources = AsyncMock(
        side_effect=lambda uri, node_limit, offset: listings[uri][offset : offset + node_limit]
    )
    service = BotCompileService.__new__(BotCompileService)
    request = pipeline.request.model_copy(update={"changes": changes})
    files = await service._list_pipeline_source_files(pipeline.client, request)
    assert files == sorted([*uris, *full_files])
    assert pipeline.client.diff_paths.await_args_list == [
        call([root], base="base", head="head", offset=offset) for offset in (0, 100)
    ]


@pytest.mark.parametrize("fallback", ["requested", "budget", "model_limit"])
async def test_directory_plan_pipeline_seeds_every_file_before_planning(runtime, fallback):
    pipeline, _ = runtime
    uris = [f"viking://resources/cedar/{i}.md" for i in range(100)]
    pipeline.changes = {"diff_dirs": [], "full_dirs": pipeline.request.from_, "head": "head"}
    pipeline.client.list_resources = AsyncMock(return_value=[])
    pipeline.model.budget = 200_000
    reply = LLMResponse(
        content='{"needs_pipeline":true,"files":[]}',
        finish_reason="error" if fallback == "model_limit" else "stop",
        error_kind=ModelErrorKind.INPUT_TOO_LARGE if fallback == "model_limit" else None,
    )
    pipeline.model.provider.chat.side_effect = ([] if fallback == "budget" else [reply]) + [
        CompileFailure("TEST_STOP", "Planning reached", stage="pipeline")
    ]

    async def batches():
        text = "fact" * (1000 if fallback == "budget" else 1)
        yield [part for uri in uris for part in split_source(uri, text, 40_000)]

    with pytest.raises(CompileFailure, match="Planning reached"):
        await pipeline.run(batches())
    assert {e["uri"] for e in pipeline.evidence.values()} == set(uris)
    calls = pipeline.model.provider.chat.await_args_list
    assert len(calls) == (1 if fallback == "budget" else 2)
    planned = json.loads(calls[-1].kwargs["messages"][1]["content"])
    assert planned["source_summary"]["file_count"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("combine", [False, True])
async def test_image_payload_and_packing_reuse_registered_metadata(runtime, combine):
    pipeline, _ = runtime
    image = await seed(runtime)
    stored = pipeline.files.values[image.payload_ref]
    pipeline.files.get = AsyncMock(wraps=pipeline.files.get)
    expected = {
        "id": image.record_id,
        "payload": {
            **{key: value for key, value in stored.items() if key != "image"},
            "source_ranges": [image.record_id],
        },
    }
    assert await payload(pipeline, image, combine=combine) == expected
    schema = CombineResponse if combine else RecordResponse
    assert await pack(pipeline, [image], "Read the image.", schema) == [[image]]
    pipeline.files.get.assert_not_awaited()
    assert "source_ranges" not in pipeline.evidence[image.record_id]

    text = next(record for record in pipeline.records.values() if record.routing_text == TEXT)
    result = await payload(pipeline, text, combine=combine)
    pipeline.files.get.assert_awaited_once_with(text.payload_ref)
    if combine:
        assert result["content"]["text"] == pipeline.client.read_raw.return_value
    else:
        assert result["payload"]["text"].startswith("1: # Cedar\n")
    del pipeline.files.values[text.payload_ref]
    with pytest.raises(ValueError, match="Missing evidence/payload"):
        await payload(pipeline, text, combine=combine)
    del pipeline.files.values[image.payload_ref]
    with pytest.raises(ValueError, match="Assigned source range is missing"):
        await EvidenceReader(pipeline.files, {"inputs": [expected]}).execute(
            source_range=image.record_id
        )


@pytest.mark.asyncio
async def test_image_evidence_view_reads_snapshot_once(runtime):
    pipeline, png = runtime
    image = await seed(runtime)
    data = {"inputs": [await payload(pipeline, image)]}
    get = AsyncMock(wraps=pipeline.files.get)
    reader = EvidenceReader(SimpleNamespace(get=get), data)

    result = await reader.execute(source_range=image.record_id)

    get.assert_awaited_once_with(f"sources/{image.record_id}")
    assert isinstance(result, MultimodalToolResult)
    assert _inline_media_bytes([{"content": result.content}]) == len(png)
    attached = next(part for part in result.content if part["type"] == "image_url")
    assert base64.b64decode(attached["image_url"]["url"].split(",", 1)[1]) == png
    assert "base64," not in result.text
    assert "image" not in json.loads(result.text)
    assert (await pipeline.files.get(image.payload_ref))["image"] == attached

    get.reset_mock()
    metadata = await reader.read(image.record_id)
    get.assert_awaited_once_with(f"sources/{image.record_id}")
    assert "image" not in metadata and metadata["complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage,schema",
    [("map", RecordResponse), ("reduce", FileResponse), ("combine", CombineResponse)],
)
async def test_pipeline_stages_can_reread_original_images(runtime, stage, schema):
    pipeline, png = runtime
    image = await seed(runtime)
    data = {"inputs": [await payload(pipeline, image, combine=stage == "combine")]}
    result = {"files": []} if schema is FileResponse else {"records": []}
    if schema is CombineResponse:
        result = {"records": [{"inputs": [image.record_id], "content": "Retry limit: 7."}]}
    pipeline.model.provider.chat.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest("read", "read_evidence", {"source_range": image.record_id}, 0)
            ],
        ),
        LLMResponse(content=json.dumps(result)),
    ]
    await pipeline.model.ask(stage, "Read the image.", data, schema)
    messages = pipeline.model.provider.chat.call_args.kwargs["messages"]
    assert _inline_media_bytes(messages) == len(png)
    reader = EvidenceReader(pipeline.files, data)
    with pytest.raises(ValueError, match="no text line ranges"):
        await reader.execute(source_range=image.record_id, start_line=1, end_line=1)
    with pytest.raises(ValueError, match="outside this assignment"):
        await reader.execute(source_range="foreign")
    result = await reader.execute(source_range=image.record_id)
    assert ChildProvider(pipeline.model, schema).should_inline_tool_result_media()
    pipeline.model.provider.supports_tool_result_media.assert_not_called()
    assert await complete_tool_result(result, "session", preserve_complete=True) is result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem,code",
    [
        ("format", "INVALID_ARGUMENT"),
        ("size", "SOURCE_READ_FAILED"),
    ],
)
async def test_image_limitations_never_fall_back_to_binary_text(runtime, problem, code):
    pipeline, _ = runtime
    uri = URI
    if problem == "format":
        uri = URI.replace(".png", ".svg")
    else:
        pipeline.client.stat.return_value = {
            "size": VikingMultiReadTool._MAX_INLINE_MEDIA_BYTES + 1
        }
    with pytest.raises(CompileFailure) as caught:
        await read_source(pipeline.client, uri)
    assert caught.value.code == code
    pipeline.client.read_raw.assert_not_awaited()
    pipeline.client.download_bytes.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["notes.md", "config.ts"])
async def test_text_reads_remain_exact_even_without_media_support(runtime, name):
    pipeline, _ = runtime
    pipeline.model.provider.supports_tool_result_media.return_value = False
    text = await read_source(pipeline.client, f"viking://resources/{name}")
    assert text == pipeline.client.read_raw.return_value
    assert "".join(p.content for p in split_source(TEXT, text, 1024)) == text
    assert all(p.as_assignment() == asdict(p) for p in split_source(TEXT, text, 1024))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget,expected", [("count", [4, 1]), ("bytes", [2, 2, 1]), ("chars", [1] * 5)]
)
async def test_memory_image_assignments_are_bounded_references(
    runtime, monkeypatch, budget, expected
):
    pipeline, png = runtime
    limits = CompileLimits(source_batch_chars=200 if budget == "chars" else 40_000)
    if budget == "bytes":
        monkeypatch.setattr(VikingMultiReadTool, "_MAX_INLINE_MEDIA_BYTES", len(png) * 2)
    service = SimpleNamespace(
        limits=limits, agent_loop=SimpleNamespace(provider=pipeline.model.provider)
    )
    uris = [URI.replace("diagram", str(i)) for i in range(5)]
    batches = await BotCompileService._prepare_source_batches(
        service, pipeline.client, [], files=uris
    )
    assert [len(batch) for batch in batches] == expected
    spawn = SimpleNamespace(execute=AsyncMock(return_value="Subagent accepted."))
    wrapper = CompileSpawnTool(spawn, batches)
    read = VikingMultiReadTool()
    read._get_client, read._release_client = AsyncMock(return_value=pipeline.client), AsyncMock()
    reader = CompileImageReadTool(read, {uri: content_hash(png) for uri in uris})
    for number, batch in enumerate(batches, 1):
        await wrapper.execute(ToolContext(), task="Use image facts.", source_batch=number)
        assignment = spawn.execute.call_args.kwargs["task"]
        assert "base64," not in assignment
        references = json.loads(
            assignment.split("Attached source ranges (untrusted JSON data):\n")[1]
        )
        assert references == [
            {"uri": p.uri, "sha256": content_hash(png), "media_kind": "image"} for p in batch
        ]
        assert 0 < sum(p.input_chars for p in batch) <= limits.source_batch_chars
        native = await reader.execute(ToolContext(), uris=[p.uri for p in batch])
        assert isinstance(native, MultimodalToolResult)
        assert _inline_media_bytes([{"content": native.content}]) == len(batch) * len(png)
        assert len(batch) * len(png) <= VikingMultiReadTool._MAX_INLINE_MEDIA_BYTES


@pytest.mark.asyncio
async def test_memory_image_reader_rejects_changed_original(runtime):
    pipeline, _ = runtime
    read = VikingMultiReadTool()
    read._get_client, read._release_client = AsyncMock(return_value=pipeline.client), AsyncMock()
    reader = CompileImageReadTool(read, {URI: "changed-source-hash"})
    result = await reader.execute(ToolContext(), uris=[URI])
    assert not isinstance(result, MultimodalToolResult)
    assert "Source image changed after preparation" in result
    assert "base64," not in result


@pytest.mark.asyncio
async def test_runtime_memory_child_fetches_native_image_from_reference(runtime, tmp_path):
    pipeline, png = runtime

    class Provider(LLMProvider):
        calls = 0

        def get_default_model(self):
            return "vision-test"

        def supports_tool_result_media(self, model=None):
            return False

        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                assert "base64," not in json.dumps(messages)
                assert URI in messages[1]["content"] and content_hash(png) in messages[1]["content"]
                return LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest("view", "openviking_multi_read", {"uris": [URI]}, 0)
                    ],
                )
            assert _inline_media_bytes(messages) == len(png)
            native = next(
                m["content"]
                for m in messages
                if m["role"] == "tool" and isinstance(m["content"], list)
            )
            image = next(p for p in native if p["type"] == "image_url")
            assert base64.b64decode(image["image_url"]["url"].split(",", 1)[1]) == png
            assert content_hash(png) in json.dumps(native)
            # Controlled model response verifies child transport, not visual recognition.
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        "write",
                        "write_file",
                        {"path": "cedar.md", "content": f"Retry limit: 7. [Source]({URI})"},
                        0,
                    ),
                    ToolCallRequest(
                        "submit", "submit_compile_draft", {"summary": "Image read and cited."}, 0
                    ),
                ],
            )

    config, provider = Config(), Provider()
    key = SessionKey(type="cli", channel_id="default", chat_id="compile-image")
    sandbox = DirectBackend(config.sandbox, key, tmp_path)
    manager = SimpleNamespace(
        get_sandbox=AsyncMock(return_value=sandbox), to_workspace_id=lambda key: "compile-image"
    )
    await sandbox.start()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_CompileProvider(provider, asyncio.Semaphore(1)),
        workspace=tmp_path,
        model="vision-test",
        config=config,
        sandbox_manager=manager,
        session_manager=SessionManager(tmp_path),
    )
    registry = ToolRegistry(config)
    for name in ("spawn", "write_file"):
        registry.register(loop.tools.get(name))
    read = VikingMultiReadTool()
    read._get_client, read._release_client = AsyncMock(return_value=pipeline.client), AsyncMock()
    registry.register(read)
    registry.register(SimpleNamespace(name="submit_wiki_bundle"))
    service = SimpleNamespace(
        limits=CompileLimits(), agent_loop=loop, _build_prompts=BotCompileService._build_prompts
    )
    batches = await BotCompileService._prepare_source_batches(
        service, pipeline.client, [], files=[URI]
    )
    request = pipeline.request.model_copy(
        update={"to": "viking://user/default/memories/cedar", "changes": None}
    )
    try:
        children = BotCompileService._configure_compile_subagents(
            service,
            loop,
            registry,
            request=request,
            connection=None,
            usage={},
            skill_text="Cite original sources.",
            source_batches=batches,
        )
        await registry.get("spawn").execute(
            ToolContext(session_key=key), task="Use the image.", source_batch=1
        )
        outcome = await children.wait()
        assert outcome["results"][0]["status"] == "completed", outcome
        assert provider.calls == 2
        draft = await sandbox.read_file(outcome["results"][0]["files"][0])
        assert "Retry limit: 7" in draft and URI in draft
    finally:
        await loop.subagents.cancel_all()
        await sandbox.stop()


@pytest.mark.asyncio
async def test_multi_read_and_request_budget_still_preserve_native_images(runtime, monkeypatch):
    pipeline, png = runtime
    tool = VikingMultiReadTool()
    tool._get_client, tool._release_client = AsyncMock(return_value=pipeline.client), AsyncMock()
    result = await tool.execute(ToolContext(), [URI])
    assert isinstance(result, MultimodalToolResult)
    assert _inline_media_bytes([{"content": result.content}]) == len(png)
    monkeypatch.setattr("vikingbot.compile.pipeline_io.MAX_INLINE_TOOL_RESULT_MEDIA_BYTES", 1)
    with pytest.raises(CompileFailure, match="media budget"):
        await pipeline.model.call("compile", [{"content": result.content}], [], max_tokens=100)
    pipeline.model.provider.chat.assert_not_awaited()


def test_wiki_requires_body_but_metadata_files_remain_valid(runtime):
    pipeline, _ = runtime
    response = FileResponse(files=[FileDraft(path="index.md", content="---\ntype: index\n---\n")])
    group = Group("direct", [])
    pipeline.direct_output_format = "wiki"
    with pytest.raises(ValueError, match="requires a Markdown body"):
        file_ops.validate_files(pipeline, response, group, [], {})
    assert response.files[0].content is not None
    response.files[0].content += "# Knowledge index\n\n[Project 01](entity/project-01.md)\n"
    file_ops.validate_files(pipeline, response, group, [], {})
    pipeline.direct_output_format = "files"
    response.files[0].content = "---\nsetting: true\n---\n"
    file_ops.validate_files(pipeline, response, group, [], {})


def test_wiki_invalid_frontmatter_is_a_repairable_validation_error(runtime):
    pipeline, _ = runtime
    pipeline.direct_output_format = "wiki"
    response = FileResponse(
        files=[FileDraft(path="index.md", content="---\ntitle: [\n---\n# Knowledge index\n")]
    )
    with pytest.raises(ValueError, match="invalid YAML frontmatter"):
        file_ops.validate_files(pipeline, response, Group("direct", []), [], {})


@pytest.mark.parametrize(
    "link,valid",
    [
        ("[Project 01](entity/Project 01.md)", False),
        ("[Project 01](entity/Project\t01.md)", False),
        ("[Project 01](entity/Project%2001.md)", True),
        ("[Project 01](<entity/Project 01.md>)", True),
        ('[Project 01](entity/Project%2001.md "Project 01")', True),
        ('[Project 01](<entity/Project 01.md> "Project 01")', True),
        ("`[Project 01](entity/Project 01.md)`", True),
        ("```markdown\n[Project 01](entity/Project 01.md)\n```", True),
        ("<!-- [Project 01](entity/Project 01.md) -->", True),
    ],
)
def test_compile_wiki_navigation_requires_valid_markdown_destinations(runtime, link, valid):
    pipeline, _ = runtime
    pipeline.direct_output_format = "wiki"
    response = FileResponse(
        files=[FileDraft(path="index.md", content=f"---\ntype: index\n---\n# Index\n\n{link}\n")]
    )
    if valid:
        file_ops.validate_files(pipeline, response, Group("direct", []), [], {})
    else:
        with pytest.raises(ValueError, match="unescaped whitespace.*|Encode spaces"):
            file_ops.validate_files(pipeline, response, Group("direct", []), [], {})
    # Ordinary file output can preserve literal Markdown source unchanged.
    pipeline.direct_output_format = "files"
    file_ops.validate_files(pipeline, response, Group("direct", []), [], {})


@pytest.mark.asyncio
async def test_compile_finalize_rejects_invalid_stored_wiki_navigation(runtime):
    pipeline, _ = runtime
    pipeline.direct_output_format = "wiki"
    body = "# Index\n\n[Project 01](entity/Project 01.md)\n"
    await pipeline.files.put(
        "accepted/index",
        {
            "path": "index.md",
            "content": body,
            "sha256": content_hash(body),
            "owner": "direct",
            "base_hash": None,
            "source_refs": [],
        },
    )
    with pytest.raises(ValueError, match="unescaped whitespace"):
        await finalize_op.run(pipeline, ["accepted/index"])
    assert "finalize" not in pipeline.files.values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_kind", ["missing", "foreign", "duplicate", "empty_files", "link_space"]
)
async def test_compile_files_repair_invalid_provenance_and_revalidate_cache(runtime, bad_kind):
    pipeline, _ = runtime
    await seed(runtime)
    records = list(pipeline.records.values())
    ids = [record.record_id for record in records]
    group = Group("files", records)
    pipeline.direct_output_format = "wiki"
    valid: dict[str, Any] = {
        "path": "entity/cedar.md",
        "content": "# Cedar\n\nRetry limit: 7.\n",
        "inputs": ids,
    }
    invalid = {key: value for key, value in valid.items() if key != "inputs"}
    if bad_kind == "link_space":
        invalid = {**valid, "content": "# Cedar\n\n[Cedar](entity/Cedar manual.md)\n"}
    elif bad_kind not in {"missing", "empty_files"}:
        invalid["inputs"] = ["foreign"] if bad_kind == "foreign" else [ids[0], ids[0]]

    def response(file):
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    "submit", "emit", {"files": [] if file is None else [file]}, tokens=0
                )
            ],
        )

    pipeline.model.provider.chat.side_effect = [
        response(None if bad_kind == "empty_files" else invalid),
        response(valid),
        response(valid),
    ]
    data = {"inputs": [{"id": key, "payload": {"fact": "Retry limit: 7"}} for key in ids]}

    def validate(result):
        file_ops.validate_files(pipeline, result, group, records, {})

    result = await pipeline.model.ask(
        "reduce", "Write supported Wiki pages", data, FileResponse, validate
    )
    assert result.files[0].inputs == ids
    assert pipeline.model.provider.chat.await_count == 2
    definition = pipeline.model.provider.chat.call_args.kwargs["tools"][0]["function"][
        "parameters"
    ]["$defs"]["FileDraft"]
    assert "inputs" in definition["required"]
    assert definition["properties"]["inputs"]["minItems"] == 1
    assert definition["properties"]["inputs"]["items"]["enum"] == ids
    cached = next(value for key, value in pipeline.files.values.items() if key.startswith("cache/"))
    cached["result"]["files"] = [] if bad_kind == "empty_files" else [invalid]
    result = await pipeline.model.ask(
        "reduce", "Write supported Wiki pages", data, FileResponse, validate
    )
    assert pipeline.model.provider.chat.await_count == 3
    refs = await file_ops.save_files(pipeline, result, group, records, {})
    assert set((await pipeline.files.get(refs[0]))["source_refs"]) == set(ids)


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
async def test_compile_file_merge_keeps_contributions_and_lazy_source_access(runtime, monkeypatch):
    pipeline, _ = runtime
    await seed(runtime)
    source_ids = list(pipeline.evidence)
    derived = []
    references = []
    repeated = "intermediate-only payload " * 5000
    for index, source in enumerate(source_ids):
        record = Record(
            f"derived-{index}", f"payloads/derived-{index}", [source], "Cedar", {}, [source]
        )
        pipeline.register(record)
        await pipeline.files.put(
            record.payload_ref, {"fields": {"repeated": repeated}, "evidence_spans": []}
        )
        derived.append(record)
        response = FileResponse(
            files=[
                FileDraft(
                    path="entity/cedar.md",
                    content=f"# Cedar\n\nContribution {index}.\n",
                    inputs=[record.record_id],
                )
            ]
        )
        references.extend(
            await file_ops.save_files(
                pipeline, response, Group(f"part-{index}", [record]), [record], {}
            )
        )

    async def respond(stage, system, data, schema, validate, **kwargs):
        assert stage == "reduce_merge"
        assert all(f"Contribution {i}." in data["candidates"][i]["content"] for i in range(2))
        assert repeated not in json.dumps(data)
        assert {item["id"] for item in data["inputs"]} == {record.record_id for record in derived}
        assert EvidenceReader(pipeline.files, data).allowed == set(source_ids)
        result = FileResponse(
            files=[
                FileDraft(
                    path="entity/cedar.md",
                    content="# Cedar\n\nContribution 0. Contribution 1.\n",
                    inputs=[record.record_id for record in derived],
                )
            ]
        )
        validate(result)
        return result

    monkeypatch.setattr(pipeline.model, "ask", AsyncMock(side_effect=respond))
    merged = await resolve_files(pipeline, references)
    assert len(merged) == 1
    assert set((await pipeline.files.get(merged[0]))["source_refs"]) == set(source_ids)
    assert not pipeline.failures


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
    both_started = asyncio.Event()

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
            if len(combined_inputs) == len(records):
                both_started.set()
            # Independent chunks must overlap; serial execution cannot satisfy this barrier.
            await asyncio.wait_for(both_started.wait(), timeout=2)
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
