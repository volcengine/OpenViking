"""Reuse only byte-identical completed Markdown at the path-collision boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from vikingbot.compile.ops import finalize
from vikingbot.compile.ops.reduce import resolve_files
from vikingbot.compile.pipeline_agent import agent_runner
from vikingbot.compile.results import Document
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from bot.tests.test_compile_markdown import agent_loop as agent_loop
from bot.tests.test_compile_markdown import pipeline as pipeline
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


async def candidates(pipeline, bodies, *, path="topic.md"):
    documents = []
    for index, body in enumerate(bodies):
        workspace = f"__compile_staging__/drafts/completed-{index}/output/{path}"
        await pipeline.files.sandbox.write_file(workspace, body)
        documents.append(Document(path, workspace, [f"range-{index}"]))
    return documents


async def test_equal_completed_outputs_keep_exact_bytes_and_union_assignment_refs(pipeline):
    body = "# Schedule\n\nValidity: 5 days.\n\n[Source](viking://resources/source/a.md)\n"
    documents = await candidates(pipeline, [body, body, body])
    pipeline.generate = AsyncMock(side_effect=AssertionError("No synthesis is needed"))
    result = await resolve_files(pipeline, documents)
    assert len(result) == 1
    assert result[0].workspace_path == documents[0].workspace_path
    assert result[0].source_refs == ["range-0", "range-1", "range-2"]
    assert documents[0].source_refs == ["range-0"]
    bundle = await finalize.run(pipeline, result)
    assert bundle.operations[0]["content"] == body
    assert bundle.operations[0]["mode"] == "create"
    assert (await pipeline.files.get("finalize"))["outputs"]["topic.md"]["source_refs"] == result[
        0
    ].source_refs
    assert pipeline.metrics["identical_collision_reuses"] == 1
    pipeline.generate.assert_not_awaited()


@pytest.mark.parametrize(
    "bodies",
    [
        ("# Rule\n\n5 days\n", "# Rule\n\n6 days\n"),
        ("# Rule\n\nversion A\n", "# Rule\n\nversion B\n"),
        (
            "# Rule\n\n[Source](viking://resources/source/a.md)\n",
            "# Rule\n\n[Source](viking://resources/source/b.md)\n",
        ),
        ("# Rule\n", "# Rule\r\n"),
        ("", ""),
        ("  \n", "  \n"),
    ],
)
async def test_differences_or_empty_candidates_still_use_collision_worker(pipeline, bodies):
    documents = await candidates(pipeline, bodies)
    repaired = Document("topic.md", "resolved.md", ["range-0", "range-1"])
    pipeline.generate = AsyncMock(return_value=[repaired])
    assert await resolve_files(pipeline, documents) == [repaired]
    pipeline.generate.assert_awaited_once()
    assert pipeline.metrics["identical_collision_reuses"] == 0


async def test_unique_paths_do_not_add_body_reads(pipeline, monkeypatch):
    documents = [Document("a.md", "a", ["range-0"]), Document("b.md", "b", ["range-1"])]
    read = AsyncMock(side_effect=AssertionError("No duplicate candidates"))
    monkeypatch.setattr(pipeline.files.sandbox, "read_file_bytes", read)
    assert await resolve_files(pipeline, documents) is documents
    read.assert_not_awaited()


async def test_probe_failure_marks_sources_failed_and_cancellation_propagates(
    pipeline, monkeypatch
):
    documents = [
        Document("topic.md", "missing-a", ["range-0"]),
        Document("topic.md", "missing-b", ["range-1"]),
    ]
    monkeypatch.setattr(
        pipeline.files.sandbox,
        "read_file_bytes",
        AsyncMock(side_effect=OSError("synthetic read failure")),
    )
    assert await resolve_files(pipeline, documents) == []
    assert pipeline.status == {"range-0": "failed", "range-1": "failed"}
    assert pipeline.failures
    monkeypatch.setattr(
        pipeline.files.sandbox, "read_file_bytes", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await resolve_files(pipeline, documents)
    assert pipeline.metrics["identical_collision_reuses"] == 0


@pytest.mark.parametrize("pair", range(5))
async def test_real_collision_entry_reuses_exact_outputs_without_sdk_calls(
    pipeline, agent_loop, monkeypatch, record_property, pair
):
    loop, key, _ = agent_loop
    body = (
        f"# Schedule\n\nValidity: {pair + 5} days.\n\n"
        "[Source A](viking://resources/source/a.md)\n"
        "[Source B](viking://resources/source/b.md)\n"
    )
    documents = await candidates(pipeline, [body, body])
    requests = []

    async def handle(request):
        value = json.loads(request.content)
        requests.append(value)
        if len(requests) == 1:
            calls = [
                {
                    "id": f"read-{i}",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": f"inputs/{i}.md"}),
                    },
                }
                for i in range(2)
            ]
            message = {"role": "assistant", "content": None, "tool_calls": calls}
        elif len(requests) == 2:
            returned = [item["content"] for item in value["messages"] if item["role"] == "tool"]
            assert len(returned) == 2 and all(body in content for content in returned)
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "topic.md", "content": body}),
                        },
                    }
                ],
            }
        else:
            message = {"role": "assistant", "content": "Complete."}
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "synthetic",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "stop" if len(requests) == 3 else "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = openai.AsyncOpenAI(
        api_key="synthetic",
        base_url="https://model.example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    backend = OpenAIVLM({"model": "synthetic", "api_key": "synthetic", "provider": "openai"})
    monkeypatch.setattr(backend, "get_async_client", lambda: client)
    pipeline.model.provider = VLMProviderAdapter(
        backend, "synthetic", SimpleNamespace(enabled=False)
    )
    pipeline.model.agent_runner = agent_runner(loop, key, None, pipeline.limits)
    try:
        result = await resolve_files(pipeline, documents)
        bundle = await finalize.run(pipeline, result)
    finally:
        await client.close()
    assert len(bundle.operations) == 1
    assert bundle.operations[0]["content"] == body
    assert bundle.operations[0]["mode"] == "create"
    expected_refs = ["range-0", "range-1"]
    assert result[0].source_refs == expected_refs
    assert (await pipeline.files.get("finalize"))["outputs"]["topic.md"][
        "source_refs"
    ] == expected_refs
    record_property("sdk_requests", len(requests))
    record_property("output_sha256", hashlib.sha256(body.encode()).hexdigest())
    record_property("source_refs", json.dumps(expected_refs))
    assert requests == []


async def test_identical_invalid_utf8_is_not_a_reuse_proof(pipeline, monkeypatch):
    documents = [Document("topic.md", "a", ["range-0"]), Document("topic.md", "b", ["range-1"])]
    monkeypatch.setattr(
        pipeline.files.sandbox, "read_file_bytes", AsyncMock(return_value=b"\xff\xfe")
    )
    repaired = Document("topic.md", "repaired", ["range-0", "range-1"])
    pipeline.generate = AsyncMock(return_value=[repaired])
    assert await resolve_files(pipeline, documents) == [repaired]
    pipeline.generate.assert_awaited_once()
    assert pipeline.metrics["identical_collision_reuses"] == 0


async def test_existing_target_still_requires_normal_publication_preflight(pipeline):
    body = "# Schedule\n\nValidity: 5 days.\n"
    documents = await candidates(pipeline, [body, body])
    pipeline.generate = AsyncMock(side_effect=AssertionError("Exact completed candidates"))
    pipeline.client.stat = AsyncMock(return_value={"size": 30})
    pipeline.client.download_bytes = AsyncMock(
        return_value=b"# Existing target\n\nProtected old content.\n"
    )
    result = await resolve_files(pipeline, documents)
    bundle = await finalize.run(pipeline, result)
    assert bundle.operations == [
        {"uri": "viking://resources/wiki/topic.md", "content": body, "mode": "create"}
    ]
    assert pipeline.old["topic.md"] == "# Existing target\n\nProtected old content.\n"
    # The server receives create semantics, not an unread overwrite authorization.
    assert bundle.updated == []


async def test_reused_path_stays_reserved_against_other_collision_workers(pipeline):
    a = await candidates(pipeline, ["# A\n\nSame facts.\n"] * 2, path="a.md")
    b = await candidates(pipeline, ["# B\n\n5 days.\n", "# B\n\n6 days.\n"], path="b.md")
    for index, document in enumerate(b):
        document.source_refs = [f"b-{index}"]
    blocked = Document("a.md", "untrusted.md", ["b-0", "b-1"])
    accepted = Document("b.md", "resolved.md", ["b-0", "b-1"])
    pipeline.generate = AsyncMock(side_effect=[[blocked], [accepted]])
    result = await resolve_files(pipeline, [*a, *b])
    assert {document.path for document in result} == {"a.md", "b.md"}
    assert pipeline.generate.await_count == 2
    assert all(
        "a.md" in call.args[2]["reserved_paths"] for call in pipeline.generate.await_args_list
    )
    assert pipeline.metrics["identical_collision_reuses"] == 1
