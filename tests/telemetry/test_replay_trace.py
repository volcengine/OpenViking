# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import json

import pytest

from openviking.telemetry import replay
from openviking.telemetry.replay import EntryRecord, encode_value
from openviking.telemetry.replay.runner import ReplayRunner
from openviking.telemetry.replay.trace import (
    entries_from_jaeger_trace,
    select_replay_invocation,
)


def _tag(key: str, value) -> dict:
    if key.startswith("replay.") and key not in {
        "replay.kind",
        "replay.name",
        "replay.module",
        "replay.outcome",
    }:
        value = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return {"key": key, "type": "string", "value": value}


def _span(
    span_id: str,
    *,
    kind: str,
    name: str,
    parent_id: str | None = None,
    arguments=None,
    match_key=None,
    result=None,
) -> dict:
    tags = [
        _tag("replay.kind", kind),
        _tag("replay.name", name),
        _tag("replay.module", "tests.telemetry.test_replay_trace"),
        _tag("replay.outcome", "returned"),
    ]
    if arguments is not None:
        tags.append(_tag("replay.arguments", arguments))
    if match_key is not None:
        tags.append(_tag("replay.match", match_key))
    if result is not None:
        tags.append(_tag("replay.result", result))
    return {
        "spanID": span_id,
        "operationName": f"replay.{kind}:{name}",
        "startTime": int(span_id, 16),
        "references": (
            [{"refType": "CHILD_OF", "spanID": parent_id}] if parent_id is not None else []
        ),
        "tags": tags,
    }


def _trace() -> dict:
    arguments = encode_value({"value": "historical"})
    return {
        "data": [
            {
                "traceID": "trace-1",
                "spans": [
                    _span(
                        "01",
                        kind="entry",
                        name="test.current",
                        arguments=arguments,
                        result=encode_value("old-result"),
                    ),
                    _span(
                        "02",
                        kind="mock",
                        name="test.lookup",
                        parent_id="01",
                        match_key=encode_value({"key": "a"}),
                        result=encode_value("entry-one"),
                    ),
                    _span(
                        "03",
                        kind="entry",
                        name="test.current",
                        arguments=arguments,
                        result=encode_value("old-result-2"),
                    ),
                    _span(
                        "04",
                        kind="mock",
                        name="test.lookup",
                        parent_id="03",
                        match_key=encode_value({"key": "a"}),
                        result=encode_value("entry-two"),
                    ),
                ],
            }
        ]
    }


def test_jaeger_parser_lists_entry_span_ids_as_invocations() -> None:
    entries = entries_from_jaeger_trace(_trace())

    assert [entry.invocation_id for entry in entries] == ["01", "03"]
    assert {entry.name for entry in entries} == {"test.current"}


def test_selecting_ambiguous_entry_requires_invocation_id() -> None:
    with pytest.raises(ValueError, match="--invocation"):
        select_replay_invocation(_trace(), "test.current")


def test_selection_loads_only_mock_descendants_of_selected_entry() -> None:
    invocation = select_replay_invocation(_trace(), "test.current", invocation_id="01")

    assert invocation.entry.invocation_id == "01"
    assert [record.result for record in invocation.mock_records] == [encode_value("entry-one")]


class CurrentComponent:
    @replay.entry("test.runner.current")
    async def execute(self, value: str) -> str:
        return f"current:{value}"


@replay.component(CurrentComponent)
def _current_component() -> CurrentComponent:
    return CurrentComponent()


@pytest.mark.asyncio
async def test_runner_invokes_current_instance_method_and_ignores_historical_result() -> None:
    entry_record = EntryRecord(
        name="test.runner.current",
        module=__name__,
        arguments=encode_value({"value": "input"}),
        outcome="returned",
        result=encode_value("historical"),
        invocation_id="entry-1",
    )

    result = await ReplayRunner().run(entry_record, [])

    assert result.outcome == "returned"
    assert result.result == "current:input"
    assert result.unconsumed_records == []
