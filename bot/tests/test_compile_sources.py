# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Issue #6713: native images must remain readable, cited evidence in Compile."""

import asyncio
import base64
import json
import random
import threading
import time
from dataclasses import asdict
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel
from vikingbot.agent.loop import AgentLoop, _inline_media_bytes
from vikingbot.agent.tools.base import MultimodalToolResult, ToolContext
from vikingbot.agent.tools.compile import CompileImageReadTool
from vikingbot.agent.tools.ov_file import VikingMultiReadTool
from vikingbot.bus.queue import MessageBus
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import CompileFailure, CompileLimits, SanitizedCompileRequest
from vikingbot.compile.pipeline import Pipeline
from vikingbot.compile.pipeline_agent import ChildProvider, agent_runner
from vikingbot.compile.pipeline_io import ModelCallError
from vikingbot.compile.service import BotCompileService
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.compile.sources import pack_source_batches, read_source, split_source
from vikingbot.config import loader
from vikingbot.config.schema import Config, SessionKey
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.providers.base import LLMResponse, ToolCallRequest
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.session.manager import SessionManager

from openviking.utils.image_input import (
    MAX_COMPILE_IMAGE_DOWNLOAD_BYTES,
    MAX_IMAGE_INPUT_DOWNLOAD_BYTES,
)
from openviking.utils.media_limits import DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION
from openviking_cli.exceptions import NotFoundError

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
def runtime(request):
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
                "to": getattr(request, "param", "viking://resources/wiki"),
                "changes": {
                    "base": None,
                    "head": None,
                    "diff_dirs": [],
                    "full_dirs": ["viking://resources/cedar"],
                    "execution": {"mode": "full"},
                },
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

    async def inputs():
        materials = [(uri, await read_source(pipeline.client, uri)) for uri in (TEXT, URI)]
        for batch in pack_source_batches(materials, pipeline.limits):
            yield batch

    sources = await pipeline.seed(inputs())
    return sources[-1]


class ImageAnswer(BaseModel):
    answer: str


def image_assignment(source):
    return {
        "inputs": [
            {
                "id": source.source_id,
                "payload": {**source.metadata, "source_ranges": [source.source_id]},
            }
        ]
    }


@pytest.mark.asyncio
async def test_preloaded_image_fact_reaches_output_with_source_identity(runtime):
    pipeline, png = runtime
    image = await seed(runtime)
    stored = await pipeline.files.get(f"sources/{image.source_id}")
    assert "text" not in stored and stored["hash"] == content_hash(png)
    assert stored["start_char"] == stored["end_char"] == 0
    data = image_assignment(image)
    assert "base64," not in json.dumps(data)
    reader = EvidenceReader(pipeline.files, data)

    def respond(**kwargs):
        assert all(t["function"]["name"] != "emit" for t in kwargs["tools"])
        if pipeline.model.provider.chat.await_count == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("read", "read_evidence", {"source_range": image.source_id}, 0)
                ],
            )
        attached = next(p for p in kwargs["messages"][-1]["content"] if p["type"] == "image_url")
        assert base64.b64decode(attached["image_url"]["url"].split(",")[1]) == png
        return LLMResponse(content=json.dumps({"answer": f"Retry limit: 7. [Source]({URI})"}))

    pipeline.model.provider.chat.side_effect = respond
    result = await pipeline.model.ask(
        "inspect", "Inspect original images.", data, ImageAnswer, readers={reader.name: reader}
    )
    assert "Retry limit: 7" in result.answer and URI in result.answer


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
            "arguments": json.dumps({"source_range": image.source_id}),
        },
    }
    answer = {"answer": URI}
    replies: list[dict[str, Any]] = [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "assistant", "content": json.dumps(answer)},
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
            image_assignment(image),
            ImageAnswer,
            readers={"read_evidence": EvidenceReader(pipeline.files, image_assignment(image))},
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


async def test_directory_plan_pipeline_seeds_every_file_before_planning(runtime):
    pipeline, _ = runtime
    uris = [f"viking://resources/cedar/{i}.md" for i in range(100)]
    pipeline.changes = {"diff_dirs": [], "full_dirs": pipeline.request.from_, "head": "head"}
    pipeline.client.list_resources = AsyncMock(return_value=[])
    pipeline.model.provider.chat.side_effect = CompileFailure(
        "TEST_STOP", "Planning reached", stage="pipeline"
    )

    async def batches():
        yield [part for uri in uris for part in split_source(uri, "fact", 40_000)]

    with pytest.raises(CompileFailure, match="Planning reached"):
        await pipeline.run(batches())
    assert {e["uri"] for e in pipeline.evidence.values()} == set(uris)
    calls = pipeline.model.provider.chat.await_args_list
    assert len(calls) == 1
    planned = json.loads(calls[-1].kwargs["messages"][1]["content"])
    assert planned["source_summary"]["file_count"] == 100


@pytest.mark.asyncio
async def test_image_evidence_snapshot_preserves_worker_media_and_assignment_bounds(runtime):
    pipeline, png = runtime
    image = await seed(runtime)
    data = image_assignment(image)
    get = AsyncMock(wraps=pipeline.files.get)
    reader = EvidenceReader(SimpleNamespace(get=get), data)

    result = await reader.execute(source_range=image.source_id)

    get.assert_awaited_once_with(f"sources/{image.source_id}")
    assert isinstance(result, MultimodalToolResult)
    assert _inline_media_bytes([{"content": result.content}]) == len(png)
    attached = next(part for part in result.content if part["type"] == "image_url")
    assert base64.b64decode(attached["image_url"]["url"].split(",", 1)[1]) == png
    assert "base64," not in result.text
    assert "image" not in json.loads(result.text)
    assert (await pipeline.files.get(f"sources/{image.source_id}"))["image"] == attached
    assert ChildProvider(pipeline.model).should_inline_tool_result_media()
    pipeline.model.provider.supports_tool_result_media.assert_not_called()

    get.reset_mock()
    metadata = await reader.read(image.source_id)
    get.assert_awaited_once_with(f"sources/{image.source_id}")
    assert "image" not in metadata and metadata["complete"] is False
    with pytest.raises(ValueError, match="no text line ranges"):
        await reader.execute(source_range=image.source_id, start_line=1, end_line=1)
    with pytest.raises(ValueError, match="outside this assignment"):
        await reader.execute(source_range="foreign")


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
        pipeline.client.stat.return_value = {"size": MAX_COMPILE_IMAGE_DOWNLOAD_BYTES + 1}
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
    uris = [URI.replace("diagram", str(i)) for i in range(5)]
    materials = [(uri, await read_source(pipeline.client, uri)) for uri in uris]
    batches = pack_source_batches(materials, limits)
    assert [len(batch) for batch in batches] == expected
    read = VikingMultiReadTool()
    read._get_client, read._release_client = AsyncMock(return_value=pipeline.client), AsyncMock()
    reader = CompileImageReadTool(read, {uri: content_hash(png) for uri in uris})
    for batch in batches:
        references = [part.as_assignment() for part in batch]
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


@pytest.fixture(scope="module")
def large_png():
    image = Image.frombytes("RGB", (1800, 1000), random.Random(6713).randbytes(1800 * 1000 * 3))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("transparent", [False, True])
async def test_large_image_is_compressed_without_changing_source_identity(
    runtime, large_png, transparent
):
    pipeline, _ = runtime
    original = large_png
    if transparent:
        with Image.open(BytesIO(original)) as image:
            image = image.convert("RGBA")
            image.paste((0, 0, 0, 0), (0, 0, 100, 100))
            output = BytesIO()
            image.save(output, format="PNG")
            original = output.getvalue()
    assert len(original) > VikingMultiReadTool._MAX_INLINE_MEDIA_BYTES
    pipeline.client.stat.return_value = {"size": len(original)}
    pipeline.client.download_bytes.return_value = original

    result = await VikingMultiReadTool._read_image(pipeline.client, URI)

    assert result["success"]
    image_part = result["image"]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    rendition = base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1])
    assert len(rendition) < len(original)
    assert len(rendition) <= VikingMultiReadTool._MAX_INLINE_MEDIA_BYTES
    assert result["sha256"] == content_hash(original)
    with Image.open(BytesIO(rendition)) as rendition_image:
        assert rendition_image.size == (1800, 1000)
        if transparent:
            assert rendition_image.getpixel((50, 50)) == (255, 255, 255)


@pytest.mark.parametrize("case", ["long_edge", "download_size"])
async def test_compile_normalizes_images_on_preload_and_worker_reread(runtime, case):
    pipeline, _ = runtime
    limit = DEFAULT_IMAGE_PREVIEW_MAX_DIMENSION
    if case == "long_edge":
        image = Image.new("RGB", (limit * 2, 128), "white")
    else:
        width = limit + 52
        pixels = random.Random(6713).randbytes(width * width * 3)
        image = Image.frombytes("RGB", (width, width), pixels)
    with image, BytesIO() as output:
        image.save(output, format="PNG")
        original = output.getvalue()
    client = pipeline.client
    client.stat.return_value = {"size": len(original)}
    client.download_bytes.return_value = original

    native = await VikingMultiReadTool._read_image(client, URI)
    if case == "long_edge":
        assert len(original) < VikingMultiReadTool._MAX_INLINE_MEDIA_BYTES
        assert native["media_bytes"] == len(original)
    else:
        assert MAX_IMAGE_INPUT_DOWNLOAD_BYTES < len(original) <= MAX_COMPILE_IMAGE_DOWNLOAD_BYTES
        assert not native["success"]
        client.download_bytes.assert_not_awaited()

    source = await read_source(client, URI)
    url = source.image["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    rendition = base64.b64decode(url.split(",", 1)[1])
    with Image.open(BytesIO(rendition)) as preview:
        assert preview.size == (limit, 64 if case == "long_edge" else limit)
    assert len(rendition) <= VikingMultiReadTool._MAX_INLINE_MEDIA_BYTES
    assert source.sha256 == content_hash(original)

    reader = CompileImageReadTool(VikingMultiReadTool(), {URI: source.sha256})
    result = await reader._read_image(client, URI)
    assert result["image"] == source.image
    assert result["media_bytes"] == len(rendition)
    assert result["sha256"] == source.sha256


async def test_compile_resizes_when_full_resolution_encoding_exceeds_budget(
    runtime, large_png, monkeypatch
):
    pipeline, _ = runtime
    pipeline.client.stat.return_value = {"size": len(large_png)}
    pipeline.client.download_bytes.return_value = large_png
    budget = 750_000
    monkeypatch.setattr(VikingMultiReadTool, "_MAX_INLINE_MEDIA_BYTES", budget)

    source = await read_source(pipeline.client, URI)
    data = base64.b64decode(source.image["image_url"]["url"].split(",", 1)[1])
    assert len(data) <= budget
    with Image.open(BytesIO(data)) as preview:
        assert preview.size == (900, 500)


async def test_simple_compile_multi_read_preserves_images_and_per_uri_errors(runtime, tmp_path):
    from vikingbot.compile import simple

    pipeline, _ = runtime
    client = pipeline.client
    with Image.new("RGB", (4096, 128), "white") as original, BytesIO() as output:
        original.save(output, format="PNG")
        source = output.getvalue()
    client.stat.return_value = {"size": len(source)}
    client.download_bytes.return_value = source
    target = pipeline.target + "/findings.md"
    client.read_raw.side_effect = NotFoundError("missing")
    client.write = AsyncMock(return_value={"uri": target, "content_updated": True})

    async def run_agent(*, tool_registry, **kwargs):
        reader = tool_registry.get("openviking_multi_read")
        context = ToolContext()
        missing = "viking://resources/cedar/missing.md"
        uris = [missing, TEXT]
        client.read_content.side_effect = [FileNotFoundError("missing"), "visible text"]
        partial = await reader.execute(context, uris=uris)
        assert not partial.startswith("Error:")
        assert f"{missing} ---\nERROR: Error reading from Viking: missing" in partial
        assert f"{TEXT} ---\nvisible text" in partial

        client.read_content.side_effect = FileNotFoundError("missing")
        failed = await reader.execute(context, uris=uris)
        assert failed.startswith("Error:") and all(uri in failed for uri in uris)

        result = await reader.execute(context, uris=[URI])
        assert isinstance(result, MultimodalToolResult)
        assert URI in result.text and content_hash(source) in result.text
        attachment = next(part for part in result.content if part["type"] == "image_url")
        data = base64.b64decode(attachment["image_url"]["url"].split(",", 1)[1])
        with Image.open(BytesIO(data)) as preview:
            assert preview.size == (2048, 64)
        writer = tool_registry.get("openviking_write")
        await writer.execute(context, path="findings.md", content=f"[Source]({URI})")
        return "Done.", None, [], {}, 1

    loop = SimpleNamespace(
        config=Config(),
        model="test-model",
        memory_window=50,
        sessions=SessionManager(tmp_path),
        max_iterations=5,
        _run_agent_loop=run_agent,
    )
    result = await simple.run(
        loop,
        client,
        pipeline.request,
        pipeline.request.from_,
        pipeline.skill,
        SessionKey(type="compile", channel_id="test", chat_id="read"),
        {},
        AsyncMock(),
        {},
        conversation_key=SessionKey(type="compile", channel_id="test", chat_id="read"),
    )
    assert result.created == [target]


@pytest.mark.asyncio
async def test_image_download_budget_and_inline_passthrough(runtime, monkeypatch):
    from vikingbot.agent.tools import ov_file

    pipeline, png = runtime
    pipeline.client.stat.return_value = {"size": ov_file.MAX_IMAGE_INPUT_DOWNLOAD_BYTES + 1}
    result = await VikingMultiReadTool._read_image(pipeline.client, URI)
    assert not result["success"]
    pipeline.client.download_bytes.assert_not_awaited()

    pipeline.client.stat.return_value = {"size": len(png)}
    pipeline.client.download_bytes.return_value = png

    def unexpected_decode(*args):
        raise AssertionError("Inline-sized images must not be decoded")

    monkeypatch.setattr(ov_file, "compress_image_input", unexpected_decode)
    result = await VikingMultiReadTool._read_image(pipeline.client, URI)
    assert result["success"]
    assert base64.b64decode(result["image"]["image_url"]["url"].split(",", 1)[1]) == png


@pytest.mark.asyncio
async def test_image_pixel_budget_precedes_decode(runtime, large_png, monkeypatch):
    pipeline, _ = runtime
    pipeline.client.stat.return_value = {"size": len(large_png)}
    pipeline.client.download_bytes.return_value = large_png
    monkeypatch.setattr("openviking.utils.image_input.MAX_IMAGE_INPUT_DECODE_PIXELS", 1)

    def unexpected_load(*args, **kwargs):
        raise AssertionError("Pixel budget must be checked before decoding")

    monkeypatch.setattr("PIL.PngImagePlugin.PngImageFile.load", unexpected_load)
    with pytest.raises(ValueError, match="pixel decode budget"):
        await VikingMultiReadTool._read_image(pipeline.client, URI)
    with pytest.raises(CompileFailure, match="pixel decode budget") as caught:
        await read_source(pipeline.client, URI)
    assert caught.value.code == "SOURCE_READ_FAILED"


@pytest.mark.asyncio
async def test_concurrent_image_reads_share_conversion_budget(runtime, large_png, monkeypatch):
    from vikingbot.agent.tools import ov_file

    pipeline, _ = runtime
    pipeline.client.stat.return_value = {"size": len(large_png)}
    pipeline.client.download_bytes.return_value = large_png
    compress, transpose = ov_file.compress_image_input, ImageOps.exif_transpose
    ready, lock = threading.Barrier(2), threading.Lock()
    active = peak = 0

    def synchronized_compress(*args):
        ready.wait(timeout=5)
        return compress(*args)

    def observed_transpose(image):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return transpose(image)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(ov_file, "compress_image_input", synchronized_compress)
    monkeypatch.setattr(ImageOps, "exif_transpose", observed_transpose)
    results = await asyncio.gather(
        VikingMultiReadTool._read_image(pipeline.client, URI),
        VikingMultiReadTool._read_image(pipeline.client, URI),
    )
    assert all(result["success"] for result in results)
    assert peak == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["viking://user/default/memories/cedar"], indirect=True)
async def test_memory_file_worker_uses_image_evidence(runtime, tmp_path):
    pipeline, png = runtime
    image = await seed(runtime)
    assignment = image_assignment(image)
    config = Config()
    key = SessionKey(type="cli", channel_id="default", chat_id="compile-image")
    sandbox = DirectBackend(config.sandbox, key, tmp_path)
    manager = SimpleNamespace(
        get_sandbox=AsyncMock(return_value=sandbox), to_workspace_id=lambda key: "compile-image"
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=ChildProvider(pipeline.model),
        workspace=tmp_path,
        model="vision-test",
        config=config,
        sandbox_manager=manager,
        session_manager=SessionManager(tmp_path),
    )
    markdown = f"Retry limit: 7. [Source]({URI})"

    def respond(messages, **kwargs):
        turn = pipeline.model.provider.chat.await_count
        if turn == 1:
            tool = ToolCallRequest("assignment", "read_file", {"path": "assignment.json"}, 0)
        elif turn == 2:
            assert "base64," not in json.dumps(messages)
            assigned = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
            assert assigned["to"] == pipeline.target and assigned["changes"] == pipeline.changes
            assert assigned["inputs"] == assignment["inputs"]
            tool = ToolCallRequest("view", "read_evidence", {"source_range": image.source_id}, 0)
        elif turn == 3:
            assert _inline_media_bytes(messages) == len(png)
            native = next(m["content"] for m in messages if isinstance(m["content"], list))
            attached = next(p for p in native if p["type"] == "image_url")
            assert base64.b64decode(attached["image_url"]["url"].split(",", 1)[1]) == png
            assert URI in json.dumps(native) and content_hash(png) in json.dumps(native)
            tool = ToolCallRequest(
                "write", "write_file", {"path": "cedar.md", "content": markdown}, 0
            )
        else:
            return LLMResponse(content="Done")
        return LLMResponse(content="", tool_calls=[tool])

    pipeline.model.provider.chat.side_effect = respond
    pipeline.files.sandbox = sandbox
    pipeline.timestamp = "2026-11-14T00:00:00Z"
    pipeline.model.agent_runner = agent_runner(loop, key, None, pipeline.limits)
    await sandbox.start()
    try:
        [document] = await pipeline.generate(
            "map", "Cite original sources.", assignment, [image.source_id]
        )
        assert document.path == "cedar.md"
        assert await sandbox.read_file(document.workspace_path) == markdown
    finally:
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
