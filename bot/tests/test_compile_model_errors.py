from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.ops import reduce as reduce_op
from vikingbot.compile.pipeline_io import JsonModel, ModelCallError
from vikingbot.compile.plan import Node, Transform
from vikingbot.compile.results import FileDraft, FileResponse, Group, Record
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.model_error import ModelError, ModelErrorKind, model_error_kind


@pytest.mark.asyncio
async def test_compile_preserves_vlm_input_too_large_error_kind():
    error = ModelError(
        ModelErrorKind.INPUT_TOO_LARGE,
        "Error code: 400 - context_length_exceeded",
        provider="openai",
        status_code=400,
        code="context_length_exceeded",
    )
    vlm = SimpleNamespace(
        provider="openai",
        thinking=None,
        get_completion_async=AsyncMock(side_effect=error),
    )
    provider = VLMProviderAdapter(
        vlm,
        default_model="test-model",
        langfuse_client=SimpleNamespace(enabled=False, _client=None),
    )
    files = SimpleNamespace(put=AsyncMock())
    model = JsonModel(
        provider,
        "test-model",
        0.0,
        files,
        SimpleNamespace(merge_input_chars=16_000, pipeline_call_seconds=5),
        {},
        Counter(),
    )

    with pytest.raises(ModelCallError) as raised:
        await model.call("reduce", [], [], max_tokens=128)

    assert model_error_kind(raised.value) is ModelErrorKind.INPUT_TOO_LARGE
    assert vlm.get_completion_async.await_count == 1


@pytest.mark.asyncio
async def test_reduce_compresses_after_preserved_input_too_large_error(monkeypatch):
    original = Record("original", "payload/original", ["e1"], "original", {}, [])
    compressed = Record("compressed", "payload/compressed", ["e1"], "compressed", {}, [])
    overflow = ModelCallError("reduce: context_length_exceeded")
    overflow.__cause__ = ModelError(
        ModelErrorKind.INPUT_TOO_LARGE,
        "Error code: 400 - context_length_exceeded",
    )
    response = FileResponse(
        files=[FileDraft(path="result.md", content="summary", inputs=["compressed"])]
    )
    ask = AsyncMock(side_effect=[overflow, response])
    runtime = SimpleNamespace(
        contract=SimpleNamespace(
            reduce=Transform(
                instructions="Summarize the inputs.",
                output="files",
                execution="direct",
            ),
            distinguish={},
        ),
        target="viking://resources/output",
        evidence={"e1": {"uri": "viking://resources/source.md"}},
        catalog={},
        records={"original": original},
        system="system",
        files=SimpleNamespace(),
        model=SimpleNamespace(ask=ask),
    )
    payload = AsyncMock(side_effect=lambda _runtime, record: {"id": record.record_id})
    pack = AsyncMock(return_value=[[original]])
    combine = AsyncMock(return_value=[compressed])
    save_files = AsyncMock(return_value=["viking://resources/output/result.md"])
    monkeypatch.setattr(reduce_op.common, "payload", payload)
    monkeypatch.setattr(reduce_op.common, "pack", pack)
    monkeypatch.setattr(reduce_op.common, "transform", combine)
    monkeypatch.setattr(reduce_op.file_ops, "save_files", save_files)

    result = await reduce_op.reduce_group(
        runtime,
        Node("summary", "reduce", "groups", task="reduce"),
        Group("group-1", [original]),
    )

    assert result == ["viking://resources/output/result.md"]
    assert ask.await_count == 2
    assert ask.await_args_list[0].args[2]["inputs"] == [{"id": "original"}]
    assert ask.await_args_list[1].args[2]["inputs"] == [{"id": "compressed"}]
    pack.assert_awaited_once()
    combine.assert_awaited_once()
