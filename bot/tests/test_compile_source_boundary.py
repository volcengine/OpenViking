"""Source extraction cannot execute or publish a whole-task transformation."""

from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import Tool
from vikingbot.bus.queue import MessageBus
from vikingbot.compile.ops import common
from vikingbot.compile.pipeline_agent import agent_runner
from vikingbot.compile.results import Record, RecordResponse
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.config.schema import Config, SessionKey
from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest

SOURCE_ID = "a" * 24


class UnusedProvider(LLMProvider):
    def get_default_model(self):
        return "fake"

    async def chat(self, *args, **kwargs):
        raise AssertionError("The child must use the task provider")


class SpyTool(Tool):
    description = "A test tool that must not run outside the child policy."

    def __init__(self, name):
        self._name = name
        self.calls = 0

    @property
    def name(self):
        return self._name

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "path": {"type": "string"},
                "working_dir": {"type": "string"},
            },
        }

    async def execute(self, tool_context, **kwargs):
        self.calls += 1
        return "Tool executed."


def source_data(kind):
    data = {
        "inputs": [
            {
                "id": SOURCE_ID,
                "payload": {"uri": "viking://resources/source.md", "text": "A supported fact."},
            }
        ],
        "record_fields": {"fact": "A fact available in this assigned source."},
        "scope_fields": {},
    }
    if kind:
        data["assignment_kind"] = kind
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "kind", "first_action", "exec_count"),
    [
        ("map_agent", "source_records", "exec", 0),
        ("map_agent", None, "exec", 1),
        ("reduce_agent", None, "exec", 1),
        ("map_agent", "source_records", "read_file", 0),
        ("map_agent", "source_records", "plain_text", 0),
    ],
)
async def test_child_policy_and_local_submission_recovery(
    tmp_path, monkeypatch, stage, kind, first_action, exec_count
):
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", lambda **kwargs: SimpleNamespace())
    config = Config(storage_workspace=str(tmp_path), sandbox={"backend": "opensandbox"})
    loop = AgentLoop(
        bus=MessageBus(), provider=UnusedProvider(), workspace=tmp_path / "work", config=config
    )
    shell, reader = SpyTool("exec"), SpyTool("read_file")
    loop.tools.register(shell)
    loop.tools.register(reader)
    seen = []

    async def call(stage, messages, tools, **kwargs):
        seen.append((deepcopy(messages), tools))
        if len(seen) == 1:
            if first_action == "plain_text":
                return LLMResponse(content="The source has been processed.")
            arguments = (
                {"command": "inspect other task files"}
                if first_action == "exec"
                else {"path": "__compile_staging__/pipeline/sources/foreign.json"}
            )
            return LLMResponse(
                content=None, tool_calls=[ToolCallRequest("attempt", first_action, arguments, 0)]
            )
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    "submit",
                    "emit",
                    {
                        "records": [
                            {
                                "inputs": [SOURCE_ID],
                                "scope": {},
                                "payload": {"fact": "A supported fact."},
                            }
                        ]
                    },
                    0,
                )
            ],
        )

    model = SimpleNamespace(
        model="fake",
        max_tokens=4096,
        metrics=Counter(),
        resources=None,
        fits=lambda *args: True,
        call=call,
        files=SimpleNamespace(sandbox=SimpleNamespace(write_file=AsyncMock())),
    )
    run = agent_runner(
        loop,
        SessionKey(type="cli", channel_id="source", chat_id="policy"),
        None,
        SimpleNamespace(subagent_iterations=4),
    )
    result = await run(
        "Extract assigned facts.", source_data(kind), RecordResponse, None, model, stage=stage
    )
    assert result.records[0].payload == {"fact": "A supported fact."}
    assert len(seen) == 2
    tools = {definition["function"]["name"]: definition["function"] for definition in seen[0][1]}
    assert ("exec" in tools) == (kind != "source_records")
    properties = tools["emit"]["parameters"]["$defs"]["RecordDraft"]["properties"]
    publication_fields = {
        name for name in properties if name.startswith("ready_") or name == "target_uri"
    }
    if kind == "source_records":
        assert not publication_fields
    else:
        assert {"ready_path", "target_uri"} <= publication_fields
    assert shell.calls == exec_count
    assert reader.calls == 0  # Staging paths cannot bypass the bound file reader.
    if first_action == "plain_text":
        assert any("has not been submitted" in m["content"] for m in seen[1][0])
        assert any(m["content"] == "The source has been processed." for m in seen[1][0])


@pytest.mark.asyncio
async def test_evidence_schema_and_reader_use_assigned_ids_only():
    files = SimpleNamespace(
        get=AsyncMock(side_effect=AssertionError("Foreign evidence must not be read"))
    )
    reader = EvidenceReader(
        files, {"inputs": [{"id": SOURCE_ID, "payload": {"source_ranges": [SOURCE_ID]}}]}
    )
    assert reader.parameters["properties"]["source_range"]["enum"] == [SOURCE_ID]
    with pytest.raises(ValueError, match="outside this assignment"):
        await reader.execute(source_range="viking://resources/other.png")
    files.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "publication",
    [
        {"ready_path": "entity/example.md", "ready_content": "# Example"},
        {"target_uri": "viking://resources/wiki/entity/example.md"},
    ],
)
async def test_runtime_rejects_source_publication_even_if_provider_ignores_schema(publication):
    record = Record(SOURCE_ID, "source", [SOURCE_ID], "", {}, [])
    response = RecordResponse.model_validate(
        {"records": [{"inputs": [SOURCE_ID], "payload": {"fact": "value"}, **publication}]}
    )

    async def ask(stage, system, data, schema, validate, **kwargs):
        assert data["assignment_kind"] == "source_records"
        validate(response)
        return response

    runtime = SimpleNamespace(
        evidence={},
        files=SimpleNamespace(
            get=AsyncMock(return_value={"uri": "viking://resources/source.md", "text": "A fact."})
        ),
        contract=SimpleNamespace(distinguish={}),
        model=SimpleNamespace(ask=ask),
        system="",
    )
    with pytest.raises(ValueError, match="Source extraction submits local facts"):
        await common.transform(
            runtime,
            "map",
            SimpleNamespace(execution="agent", instructions="Extract facts.", fields={}),
            [record],
            extra={"assignment_kind": "source_records"},
            prompt="",
        )
