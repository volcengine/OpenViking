"""Compile Reduce evidence preloading obeys the configured per-assignment budget."""

import json
from collections import Counter
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from vikingbot.agent.loop import AgentLoop
from vikingbot.compile.models import CompileLimits
from vikingbot.compile.pipeline_io import JsonModel
from vikingbot.compile.results import FileResponse
from vikingbot.compile.service import BotCompileService
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.config.schema import CompileConfig


class FakeFiles:
    def __init__(self):
        self.sources = {
            "a-big": {"text": "large " * 1000, "uri": "viking://resources/big.md"},
            "z-small": {"text": "small evidence", "uri": "viking://resources/small.md"},
        }
        self.reads = []
        self.put = AsyncMock()

    async def get(self, path):
        self.reads.append(path)
        if path.startswith("sources/"):
            return self.sources[path.removeprefix("sources/")]
        return None


def input_data():
    return {"inputs": [{"id": "record", "payload": {"source_ranges": ["a-big", "z-small"]}}]}


async def ask_with_budget(stage, budget):
    files = FakeFiles()
    model = JsonModel(
        SimpleNamespace(),
        "test-model",
        0.0,
        files,
        CompileLimits(merge_input_chars=100_000, reduce_evidence_chars=budget),
        {},
        Counter(),
    )
    direct = AsyncMock(return_value=FileResponse())
    with patch.object(model, "direct", direct):
        await model._ask(
            stage, "system", input_data(), FileResponse, None, agent=False, readers=None
        )
    assert direct.await_args is not None
    sent = direct.await_args.args[2]
    return files, model, sent


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["reduce", "reduce_merge"])
async def test_zero_budget_keeps_reduce_evidence_on_demand(stage):
    files, _, sent = await ask_with_budget(stage, 0)

    assert "original_evidence" not in sent
    assert not any(path.startswith("sources/") for path in files.reads)
    reader = EvidenceReader(files, sent)
    assert reader.allowed - reader.delivered == {"a-big", "z-small"}
    assert json.loads(await reader.execute(source_range="z-small"))["text"] == "small evidence"


@pytest.mark.asyncio
async def test_positive_budget_skips_large_range_but_loads_later_small_range():
    _, _, sent = await ask_with_budget("reduce", 500)

    assert set(sent["original_evidence"]) == {"z-small"}


@pytest.mark.asyncio
async def test_unset_budget_preserves_existing_identity_and_prompt_scoped_cache():
    default_files, default_model, sent = await ask_with_budget("reduce", None)
    capped_files, capped_model, _ = await ask_with_budget("reduce", 0)

    assert set(sent["original_evidence"]) == {"a-big", "z-small"}
    assert default_model.identity == capped_model.identity
    assert "reduce_evidence_chars" not in default_model.identity
    assert default_files.put.await_args is not None
    assert capped_files.put.await_args is not None
    assert default_files.put.await_args.args[0] != capped_files.put.await_args.args[0]


@pytest.mark.asyncio
async def test_reduce_budget_does_not_change_other_file_response_stages():
    _, _, sent = await ask_with_budget("map", 0)

    assert set(sent["original_evidence"]) == {"a-big", "z-small"}


@pytest.mark.asyncio
async def test_zero_budget_exposes_read_evidence_to_direct_model():
    files = FakeFiles()
    model = JsonModel(
        SimpleNamespace(),
        "test-model",
        0.0,
        files,
        CompileLimits(merge_input_chars=100_000, reduce_evidence_chars=0),
        {},
        Counter(),
    )
    call = AsyncMock(
        return_value=SimpleNamespace(tool_calls=[], content='{"files":[]}', finish_reason="stop")
    )

    with patch.object(model, "call", call):
        await model._ask(
            "reduce", "system", input_data(), FileResponse, None, agent=False, readers=None
        )

    assert call.await_args is not None
    tools = call.await_args.args[2]
    assert [tool["function"]["name"] for tool in tools] == ["emit", "read_evidence"]
    assert "original_evidence" not in json.loads(call.await_args.args[1][1]["content"])


@pytest.mark.parametrize("invalid", [-1, True, 1.5])
def test_compile_config_accepts_zero_and_rejects_invalid_budget(tmp_path, invalid):
    assert CompileConfig(reduce_evidence_chars=0).reduce_evidence_chars == 0
    assert CompileConfig().reduce_evidence_chars is None
    with pytest.raises(ValidationError):
        CompileConfig(reduce_evidence_chars=invalid)

    config = SimpleNamespace(
        bot_data_path=tmp_path,
        compile=CompileConfig(reduce_evidence_chars=0),
    )
    service = BotCompileService(agent_loop=cast(AgentLoop, SimpleNamespace(config=config)))
    assert service.limits.reduce_evidence_chars == 0
