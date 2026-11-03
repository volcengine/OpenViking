# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Issue #6713: native images must remain readable, cited evidence in Compile."""

import asyncio
import base64
import json
from dataclasses import asdict
from io import BytesIO
from types import SimpleNamespace
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
from vikingbot.compile import entry
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import CompileFailure, CompileLimits, SanitizedCompileRequest
from vikingbot.compile.ops.common import pack, payload
from vikingbot.compile.pipeline import Pipeline
from vikingbot.compile.pipeline_agent import ChildProvider, complete_tool_result
from vikingbot.compile.pipeline_io import ModelCallError
from vikingbot.compile.results import CombineResponse, FileResponse, RecordResponse
from vikingbot.compile.service import BotCompileService, _CompileProvider
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.compile.sources import read_source, split_source
from vikingbot.config import loader
from vikingbot.config.schema import Config, SessionKey
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from vikingbot.providers.vlm_adapter import VLMProviderAdapter
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.session.manager import SessionManager

from openviking.models.model_error import ModelErrorKind
from openviking.models.vlm import GLMVLM, KimiVLM, OpenAIVLM, VolcEngineVLM
from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.retry_vlm import RetryVLM

URI = "viking://resources/cedar/diagram.png"
TEXT = "viking://resources/cedar/notes.md"


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
    provider = SimpleNamespace(supports_tool_result_media=Mock(return_value=True), chat=AsyncMock())
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


async def seed(runtime, *, roots=None):
    pipeline, _ = runtime
    service = SimpleNamespace(
        limits=pipeline.limits, agent_loop=SimpleNamespace(provider=pipeline.model.provider)
    )
    files = (
        await BotCompileService._list_source_files(service, pipeline.client, roots)
        if roots
        else [TEXT, URI]
    )
    batches = await BotCompileService._prepare_source_batches(
        service, pipeline.client, [], files=files
    )

    async def inputs():
        for batch in batches:
            yield batch

    records = await pipeline.seed(inputs())
    return next(record for record in records if pipeline.evidence[record.record_id]["uri"] == URI)


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
@pytest.mark.parametrize(
    "backend,plan,bot_override,accepts_images",
    [
        pytest.param("volcengine", "plan", False, True, id="doubao-agent-plan"),
        pytest.param("volcengine", "coding", False, True, id="doubao-coding-plan"),
        pytest.param("volcengine", "coding", True, True, id="doubao-bot-override"),
        pytest.param("volcengine", "", False, True, id="doubao-payg"),
        pytest.param("openai", "coding", False, True, id="doubao-openai-compatible"),
        pytest.param("kimi", "", False, True, id="kimi"),
        pytest.param("glm", "", False, True, id="glm"),
        pytest.param("openai", "coding", False, False, id="image-rejection"),
    ],
)
async def test_compile_image_reaches_chat_completion_wire(
    runtime, tmp_path, monkeypatch, backend, plan, bot_override, accepts_images
):
    """Load actual Bot configuration and serialize image evidence through the SDK."""
    from openai import AsyncOpenAI
    from vikingbot.cli.commands import _make_provider
    from volcenginesdkarkruntime import AsyncArk

    pipeline, png = runtime
    model = "doubao-seed-2.1-lite" if plan else "doubao-seed-2-1-lite-260915"
    api_base = f"https://ark.cn-beijing.volces.com/api/{plan + '/' if plan else ''}v3"
    if backend in {"kimi", "glm"}:
        api_base = "https://compile.invalid/v1"
    selected = {"provider": backend, "model": model, "api_base": api_base, "api_key": "test-key"}
    data = {"vlm": selected}
    if bot_override:
        data = {
            "vlm": {"provider": "openai", "model": "unused-root", "api_key": "unused-key"},
            "bot": {"agents": selected},
        }
    config_path = tmp_path / "ov.conf"
    config_path.write_text(json.dumps(data))
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    config = loader.load_config()
    provider = _make_provider(config, langfuse_client=SimpleNamespace(enabled=False))
    assert config.inherits_root_vlm() is (not bot_override)
    assert isinstance(provider._vlm, RetryVLM)
    vlm = provider._vlm.inner
    assert isinstance(
        vlm,
        {"volcengine": VolcEngineVLM, "openai": OpenAIVLM, "kimi": KimiVLM, "glm": GLMVLM}[backend],
    )
    assert (vlm.model, vlm.api_base) == (model, api_base)
    requests = []

    def handle(request):
        assert request.method == "POST"
        assert str(request.url) == f"{api_base}/chat/completions"
        requests.append(json.loads(request.content))
        assert requests[-1]["model"] == model
        if len(requests) > 1 and not accepts_images:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "unsupported_image",
                        "message": "This model does not support image inputs.",
                    }
                },
            )
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "read-image",
                        "type": "function",
                        "function": {
                            "name": "read_evidence",
                            "arguments": json.dumps({"source_range": image.record_id}),
                        },
                    }
                ],
            }
        else:
            message = {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "files": [
                            {
                                "path": "cedar.md",
                                "content": f"Retry limit: 7. [Source]({URI})",
                                "inputs": [image.record_id],
                            }
                        ]
                    }
                ),
            }
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
                        "message": message,
                        "finish_reason": "tool_calls" if len(requests) == 1 else "stop",
                    }
                ],
            },
        )

    sdk_type = AsyncArk if backend == "volcengine" else AsyncOpenAI
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with sdk_type(
            api_key=vlm.api_key,
            base_url=vlm.api_base,
            http_client=http,
            max_retries=0,
        ) as sdk:
            monkeypatch.setattr(vlm, "get_async_client", lambda: sdk)
            pipeline.model.provider = provider
            root, directory = "viking://resources", URI.rsplit("/", 1)[0]
            pipeline.client.stat.side_effect = lambda uri: (
                {"isDir": True} if uri == root else {"size": len(png)}
            )
            pipeline.client.list_resources = AsyncMock(
                side_effect=lambda uri, **kwargs: (
                    [{"uri": directory, "isDir": True}]
                    if uri == root
                    else [{"uri": TEXT}, {"uri": URI}]
                )
            )
            image = await seed(runtime, roots=[root])
            pipeline.model.provider = _CompileProvider(provider, asyncio.Semaphore(1))
            pipeline.model.model = model
            response = pipeline.model.ask(
                "compile",
                "Inspect original images.",
                {"inputs": [await payload(pipeline, image)]},
                FileResponse,
            )
            if accepts_images:
                result = await response
                assert result.files[0].inputs == [image.record_id]
                assert URI in result.files[0].content
            else:
                with pytest.raises(ModelCallError, match="does not support image"):
                    await response

    assert len(requests) == 2
    messages = requests[-1]["messages"]
    tool = next(m for m in messages if m["role"] == "tool")
    assert tool["tool_call_id"] == "read-image" and URI in tool["content"]
    assert isinstance(tool["content"], str) and "base64," not in tool["content"]
    images = [
        p
        for m in messages
        if m["role"] == "user" and isinstance(m["content"], list)
        for p in m["content"]
        if p["type"] == "image_url"
    ]
    assert len(images) == 1
    assert base64.b64decode(images[0]["image_url"]["url"].split(",")[1]) == png
    pipeline.client.read_raw.assert_awaited_once_with(TEXT)


@pytest.mark.asyncio
async def test_undeclared_image_protocol_fails_before_download(runtime):
    pipeline, _ = runtime
    vlm = LiteLLMVLMProvider(
        {
            "provider": "litellm",
            "model": "future-claude-vision",
            "api_base": "https://compat.invalid/v1",
        }
    )
    provider = VLMProviderAdapter(vlm, vlm.model, langfuse_client=SimpleNamespace(enabled=False))
    with pytest.raises(CompileFailure, match="provider/model") as caught:
        await read_source(pipeline.client, URI, provider)
    assert caught.value.code == "UNSUPPORTED_MEDIA"
    pipeline.client.read_raw.assert_not_awaited()
    pipeline.client.download_bytes.assert_not_awaited()


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
    assert ChildProvider(pipeline.model, schema).supports_tool_result_media()
    assert await complete_tool_result(result, "session", preserve_complete=True) is result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem,code",
    [
        ("provider", "UNSUPPORTED_MEDIA"),
        ("format", "INVALID_ARGUMENT"),
        ("size", "SOURCE_READ_FAILED"),
    ],
)
async def test_image_limitations_never_fall_back_to_binary_text(runtime, problem, code):
    pipeline, _ = runtime
    uri = URI
    if problem == "provider":
        pipeline.model.provider.supports_tool_result_media.return_value = False
    elif problem == "format":
        uri = URI.replace(".png", ".svg")
    else:
        pipeline.client.stat.return_value = {
            "size": VikingMultiReadTool._MAX_INLINE_MEDIA_BYTES + 1
        }
    with pytest.raises(CompileFailure) as caught:
        await read_source(pipeline.client, uri, pipeline.model.provider)
    assert caught.value.code == code
    pipeline.client.read_raw.assert_not_awaited()
    pipeline.client.download_bytes.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["notes.md", "config.ts"])
async def test_text_reads_remain_exact_even_without_media_support(runtime, name):
    pipeline, _ = runtime
    pipeline.model.provider.supports_tool_result_media.return_value = False
    text = await read_source(pipeline.client, f"viking://resources/{name}", pipeline.model.provider)
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
            return True

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
        provider=provider,
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
