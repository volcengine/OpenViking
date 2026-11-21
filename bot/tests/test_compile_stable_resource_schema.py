"""Stable request schemas keep task-local attachment locations in tool results."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from vikingbot.compile.pipeline_agent import agent_runner
from vikingbot.compile.skill_resources import SkillResources
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from bot.tests.test_compile_markdown import agent_loop as agent_loop
from bot.tests.test_compile_markdown import pipeline as pipeline
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def test_resource_locations_are_returned_without_changing_tool_schema(tmp_path):
    raw = "# Rules\n\nSynthetic validity: 5 days.\n".encode()
    client = SimpleNamespace(
        stat=AsyncMock(return_value={"size": len(raw)}),
        download_bytes=AsyncMock(return_value=raw),
    )
    saved = []

    def files(cwd):
        return SimpleNamespace(
            sandbox=SimpleNamespace(
                sandbox_cwd=cwd,
                write_file=AsyncMock(side_effect=lambda path, body: saved.append((path, body))),
            ),
            put=AsyncMock(),
        )

    a = SkillResources(client, "viking://agent/skills/synthetic", files(str(tmp_path / "a")))
    b = SkillResources(client, "viking://agent/skills/synthetic", files(str(tmp_path / "b")))
    assert a.local_root != b.local_root
    assert a.to_schema() == b.to_schema()
    results = [json.loads(await tool.execute(path="rules.md")) for tool in (a, b)]
    assert results[0]["local_path"] != results[1]["local_path"]
    assert all(result["text"] == raw.decode() and result["complete"] for result in results)
    assert all(result["sha256"] == hashlib.sha256(raw).hexdigest() for result in results)
    assert [path for path, _ in saved] == [result["local_path"] for result in results]
    assert str(tmp_path) not in json.dumps(a.to_schema())
    for invalid in ("../secret", "viking://agent/skills/other/rules.md"):
        with pytest.raises(ValueError):
            await a.execute(path=invalid)
    assert client.stat.await_count == 2


@pytest.mark.parametrize("pair", range(5))
async def test_real_worker_sdk_requests_have_stable_resource_schema(
    pipeline, agent_loop, monkeypatch, record_property, pair
):
    loop, key, _ = agent_loop
    raw = f"# Synthetic rules\n\nValidity: {pair + 5} days.\n".encode()
    expected = raw.decode() + "\n[Source](viking://resources/source/synthetic.md)\n"
    pipeline.client.stat = AsyncMock(return_value={"size": len(raw)})
    pipeline.client.download_bytes = AsyncMock(return_value=raw)
    requests, phase = [], 0

    async def handle(request):
        nonlocal phase
        requests.append(json.loads(request.content))
        phase = (phase % 3) + 1
        if phase == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "read-rules",
                        "type": "function",
                        "function": {
                            "name": "read_skill_resource",
                            "arguments": '{"path":"rules.md"}',
                        },
                    }
                ],
            }
        elif phase == 2:
            assert any(
                f"Validity: {pair + 5} days." in str(message.get("content", ""))
                for message in requests[-1]["messages"]
            )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "write-page",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"path": "topic.md", "content": expected}),
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
                        "finish_reason": "stop" if phase == 3 else "tool_calls",
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
    locations, outputs = [], []
    try:
        for index in range(2):
            resources = SkillResources(pipeline.client, pipeline.request.skill, pipeline.files)
            pipeline.resources = pipeline.model.resources = resources
            locations.append(resources.local_root)
            documents = await pipeline.generate(
                f"map-{index}", "Write the assigned source facts.", {"inputs": []}, []
            )
            assert len(documents) == 1
            outputs.append(await pipeline.files.sandbox.read_file(documents[0].workspace_path))
    finally:
        await client.close()
    schemas = [request["tools"] for request in requests]
    record_property(
        "schema_fingerprints", json.dumps([fingerprint(schemas[0]), fingerprint(schemas[3])])
    )
    record_property("sdk_requests", len(requests))
    record_property("output_sha256", hashlib.sha256(expected.encode()).hexdigest())
    assert len(requests) == 6 and locations[0] != locations[1]
    assert outputs == [expected, expected]
    assert schemas[0] == schemas[3]
    assert all(schemas[index] == schemas[0] for index in range(6))
    assert all(location not in json.dumps(schemas) for location in locations)
