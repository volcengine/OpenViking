# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Routing needs direct confirmation without arbitrary group caps or unseen evidence."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.models import CompileLimits
from vikingbot.compile.ops.shuffle import Shuffle, group_related_records
from vikingbot.compile.pipeline_io import MAX_JOB_ATTEMPTS
from vikingbot.compile.plan import Node, Routing
from vikingbot.compile.results import Record, RouteBatchResponse


class MemoryFiles:
    def __init__(self):
        self.values = {}

    async def put(self, key, value):
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)


def routing_runtime(count, monkeypatch):
    records = [
        Record(f"record-{i}", f"payload-{i}", [f"source-{i}"], f"Observation {i}", {}, [])
        for i in range(count)
    ]
    runtime = SimpleNamespace(
        files=MemoryFiles(),
        contract=SimpleNamespace(
            routing=Routing(instructions="Jointly process related evidence"), distinguish={}
        ),
        limits=CompileLimits(),
        metrics=Counter(),
        evidence={f"source-{i}": {"uri": f"viking://resources/notes-{i}.md"} for i in range(count)},
        model=SimpleNamespace(ask=AsyncMock()),
        warnings=[],
        failures=[],
        status={},
    )
    shuffle = Shuffle(runtime)
    monkeypatch.setattr(
        shuffle, "vectors", AsyncMock(return_value=[[float(i + 1)] for i in range(count)])
    )
    return shuffle, runtime, records, Node("grouped", "shuffle", "extracted", "routing", False)


def test_compile_grouping_does_not_make_relevance_transitive():
    assert group_related_records(["a", "b", "c", "d"], [("b", "a"), ("c", "b")]) == [
        ["a", "b"],
        ["c"],
        ["d"],
    ]
    with pytest.raises(ValueError, match="Unknown related record"):
        group_related_records(["a"], [("a", "unseen")])


def test_compile_grouping_wrong_bridge_does_not_join_project_clusters():
    clusters = [[f"{project}-{i}" for i in range(3)] for project in ("a", "b")]
    links = [(a, b) for cluster in clusters for a in cluster for b in cluster if a != b]
    links.append(("a-2", "b-0"))
    assert group_related_records([*clusters[0], *clusters[1]], links) == clusters


@pytest.mark.asyncio
async def test_compile_shuffle_keeps_large_directly_confirmed_workset(monkeypatch):
    shuffle, runtime, records, node = routing_runtime(61, monkeypatch)
    monkeypatch.setattr(
        "vikingbot.compile.ops.shuffle.top_candidates",
        lambda vectors, *args: [
            [j for j in range(len(vectors)) if i != j] for i in range(len(vectors))
        ],
    )

    def respond(stage, system, request, schema):
        return RouteBatchResponse(
            decisions=[
                {"record": item["record"], "related": item["candidates"]}
                for item in request["records"]
            ]
        )

    runtime.model.ask.side_effect = respond
    groups = await shuffle.run(node, records)
    assert len(groups) == 1
    assert groups[0].records == records
    assert {ref for record in groups[0].records for ref in record.source_refs} == set(
        runtime.evidence
    )
    assert not runtime.failures


@pytest.mark.asyncio
async def test_compile_shuffle_accepts_described_peer_and_ignores_self_link(monkeypatch):
    shuffle, runtime, records, node = routing_runtime(3, monkeypatch)
    monkeypatch.setattr(
        "vikingbot.compile.ops.shuffle.top_candidates", lambda *args: [[1], [0], [0]]
    )
    runtime.model.ask.return_value = RouteBatchResponse(
        decisions=[
            {"record": "record-0", "related": ["record-0", "record-2"]},
            {"record": "record-1"},
            {"record": "record-2"},
        ]
    )
    groups = await shuffle.run(node, records)
    assert [group.records for group in groups] == [[records[0], records[2]], [records[1]]]
    assert runtime.files.values["routes/grouped-record-0"]["related"] == ["record-2"]
    assert runtime.files.values["route-responses/grouped-record-0-1"]["decisions"][0][
        "related"
    ] == ["record-0", "record-2"]
    assert not runtime.failures


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid", "diagnostic"),
    [
        ({"related": ["unseen-id"]}, "Unknown related IDs: ['unseen-id']"),
        ({"history": ["viking://resources/other/private.md"]}, "History must belong"),
    ],
)
async def test_compile_shuffle_rejects_unknown_links_with_bounded_repair(
    monkeypatch, invalid, diagnostic
):
    shuffle, runtime, records, node = routing_runtime(3, monkeypatch)
    monkeypatch.setattr(
        "vikingbot.compile.ops.shuffle.top_candidates", lambda *args: [[1], [0], [0]]
    )

    def respond(stage, system, request, schema):
        if runtime.model.ask.call_count > 1:
            assert diagnostic in request["records"][0]["previous_error"]
        return RouteBatchResponse(
            decisions=[
                {"record": item["record"], **(invalid if item["record"] == "record-0" else {})}
                for item in request["records"]
            ]
        )

    runtime.model.ask.side_effect = respond
    groups = await shuffle.run(node, records)
    assert runtime.model.ask.call_count == MAX_JOB_ATTEMPTS
    assert runtime.status == {"record-0": "failed"}
    assert [record for group in groups for record in group.records] == records[1:]
    assert "routes/grouped-record-0" not in runtime.files.values
    assert len(runtime.failures) == 1
