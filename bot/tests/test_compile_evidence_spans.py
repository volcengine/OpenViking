"""Verify source-location validation and excerpt expansion through the compile model path."""

import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from vikingbot.compile.models import CompileLimits
from vikingbot.compile.ops import common
from vikingbot.compile.pipeline_io import JsonModel
from vikingbot.compile.plan import (
    EvidenceSpan,
    FileResponse,
    Record,
    RecordResponse,
    Transform,
    content_hash,
    result_schema,
)
from vikingbot.compile.skill_resources import EvidenceReader
from vikingbot.providers.base import LLMResponse, ToolCallRequest


@pytest.fixture
def files():
    text = "# Terms\r\nprice: 12\r\nexception: region X\r\nnotes\r\nunrelated section\r\n"
    source = {
        "uri": "viking://resources/input/source",
        "text": text,
        "hash": content_hash(text),
        "start_line": 101,
        "end_line": 105,
        "start_char": 1000,
        "end_char": 1000 + len(text),
        "context": "Product X, version 2026",
    }
    data = {"sources/S": source}

    async def put(key, value):
        data[key] = value

    return SimpleNamespace(data=data, get=AsyncMock(side_effect=data.get), put=put)


def span(start, end, source="S"):
    return {"source_range": source, "start_line": start, "end_line": end}


def inputs(spans):
    return {
        "inputs": [
            {
                "id": "record",
                "payload": {
                    "source_ranges": ["S"],
                    "evidence_spans": spans,
                },
            }
        ]
    }


async def test_excerpt_union_preserves_original_and_full_access(files):
    data = inputs([span(2, 3), span(3, 4), span(2, 2)])
    reader = EvidenceReader(files, data)
    preview = await reader.read("S", reader.spans["S"])
    assert preview["excerpts"] == [
        {"start_line": 2, "end_line": 4, "text": "price: 12\r\nexception: region X\r\nnotes\r\n"}
    ]
    assert preview["complete"] is False
    assert preview["line_count"] == 5
    assert preview["context"] == "Product X, version 2026"
    assert preview["hash"] == files.data["sources/S"]["hash"]
    assert "text" not in preview
    data["original_evidence"] = {"S": preview}
    reader = EvidenceReader(files, data)
    assert reader.allowed - reader.delivered == {"S"}
    full = json.loads(await reader.execute(source_range="S"))
    assert full["complete"] is True
    assert full["text"] == files.data["sources/S"]["text"]
    selected = json.loads(await reader.execute(source_range="S", start_line=1, end_line=2))
    assert selected["excerpts"][0]["text"] == "# Terms\r\nprice: 12\r\n"
    assert (await reader.read("S", [(1, 5)]))["complete"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_line": 0, "end_line": 2},
        {"start_line": 4, "end_line": 3},
        {"start_line": 1, "end_line": 6},
        {"start_line": 1},
        {"end_line": 2},
        {"start_line": True, "end_line": 2},
        {"source_range": "unauthorized"},
    ],
)
async def test_reader_rejects_invalid_bounds_and_sources(files, kwargs):
    with pytest.raises(ValueError):
        await EvidenceReader(files, inputs([])).execute(**{"source_range": "S", **kwargs})


async def test_unlocated_neighbour_retains_full_shard(files):
    data = inputs([span(2, 3)])
    data["inputs"].append({"id": "unlocated", "payload": {"source_ranges": ["S"]}})
    reader = EvidenceReader(files, data)
    assert "S" not in reader.spans
    assert (await reader.read("S", reader.spans.get("S", ())))["complete"] is True


@pytest.mark.parametrize(
    "location", [None, span(2, 3), span(4, 3), span(1, 6), span(1, 2, "other")]
)
@pytest.mark.parametrize("routing_text", [None, "", "price"])
async def test_map_numbers_view_and_validates_supporting_locations(files, location, routing_text):
    source = files.data["sources/S"]
    record = Record("S", "sources/S", ["S"], source["uri"], {}, [])
    registered = []

    async def ask(stage, system, data, schema, validate, **kwargs):
        assert data["inputs"][0]["payload"]["text"].startswith("1: # Terms\r\n2: price: 12")
        assert "routing_text" not in result_schema(schema, data)["$defs"]["RecordDraft"]["required"]
        result = RecordResponse.model_validate(
            {
                "records": [
                    {
                        "inputs": ["S"],
                        "payload": {"price": 12},
                        **({"routing_text": routing_text} if routing_text is not None else {}),
                        **({"evidence_spans": [location]} if location else {}),
                    }
                ]
            }
        )
        validate(result)
        return result

    runtime = SimpleNamespace(
        files=files,
        evidence={"S": source},
        system="",
        contract=SimpleNamespace(distinguish={}),
        model=SimpleNamespace(ask=ask, identity={}, fits=lambda *args: True),
        register=registered.append,
        status={},
    )
    if location and (
        location["source_range"] != "S"
        or not 1 <= location["start_line"] <= location["end_line"] <= 5
    ):
        with pytest.raises(ValueError):
            await common.transform(runtime, "map", Transform(instructions="Extract"), [record])
        assert not registered
    else:
        records = await common.transform(
            runtime, "map", Transform(instructions="Extract"), [record]
        )
        stored = await files.get(records[0].payload_ref)
        assert stored["evidence_spans"] == ([location] if location else [])
        assert records[0].source_refs == ["S"]
        assert stored["routing_text"] == records[0].routing_text
        assert records[0].routing_text == (routing_text or '{"price": 12}')
    assert content_hash(source["text"]) == source["hash"]
    assert source["text"].startswith("# Terms\r\n")


async def test_reduce_receives_excerpts_and_can_expand_full_source(files):
    calls = []

    async def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            data = json.loads(kwargs["messages"][1]["content"])
            assert data["original_evidence"]["S"]["complete"] is False
            assert data["original_evidence"]["S"]["excerpts"][0]["start_line"] == 2
            assert "read_evidence" in [t["function"]["name"] for t in kwargs["tools"]]
            return LLMResponse(
                None, [ToolCallRequest("read", "read_evidence", {"source_range": "S"}, 0)]
            )
        expanded = json.loads(kwargs["messages"][-1]["content"])
        assert expanded["complete"] is True
        assert expanded["text"] == files.data["sources/S"]["text"]
        return LLMResponse(None, [ToolCallRequest("emit", "emit", {"files": []}, 0)])

    model = JsonModel(SimpleNamespace(chat=chat), "test", 0, files, CompileLimits(), {}, Counter())
    await model.ask("reduce", "Generate files", inputs([span(2, 3)]), FileResponse)
    assert len(calls) == 2


def test_span_schema_uses_positive_integer_lines():
    for value in [0, -1, True, "2"]:
        with pytest.raises(ValidationError):
            EvidenceSpan(source_range="S", start_line=value, end_line=3)
