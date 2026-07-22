# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from openviking.telemetry.replay import ReplayResult, encode_value


@pytest.fixture
def debug_trace_module():
    path = Path(__file__).with_name("debug_trace.py")
    spec = importlib.util.spec_from_file_location("openviking_debug_trace", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tag(key: str, value) -> dict:
    if key in {"replay.arguments", "replay.result"}:
        value = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return {"key": key, "type": "string", "value": value}


def _trace() -> dict:
    return {
        "data": [
            {
                "traceID": "trace-1",
                "spans": [
                    {
                        "spanID": "entry-1",
                        "operationName": "replay.entry:test.entry",
                        "startTime": 1,
                        "references": [],
                        "tags": [
                            _tag("replay.kind", "entry"),
                            _tag("replay.name", "test.entry"),
                            _tag("replay.module", "test.module"),
                            _tag("replay.arguments", encode_value({"value": "input"})),
                            _tag("replay.outcome", "returned"),
                            _tag("replay.result", encode_value("historical")),
                        ],
                    }
                ],
            }
        ]
    }


def test_replay_list_prints_json_without_normal_trace_tree(
    debug_trace_module, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(debug_trace_module, "fetch_trace", lambda _trace_id: _trace())

    debug_trace_module.main(["0" * 32, "--replay-list"])

    output = json.loads(capsys.readouterr().out)
    assert output == [
        {
            "invocation_id": "entry-1",
            "module": "test.module",
            "name": "test.entry",
            "outcome": "returned",
        }
    ]


def test_replay_command_prints_runner_result_without_normal_trace_tree(
    debug_trace_module, monkeypatch, capsys
) -> None:
    class FakeRunner:
        async def run(self, entry, mock_records):
            assert entry.invocation_id == "entry-1"
            assert mock_records == []
            return ReplayResult(outcome="returned", result={"fresh": True})

    monkeypatch.setattr(debug_trace_module, "fetch_trace", lambda _trace_id: _trace())
    monkeypatch.setattr(debug_trace_module, "ReplayRunner", FakeRunner)

    debug_trace_module.main(["0" * 32, "--replay", "test.entry", "--invocation", "entry-1"])

    output = json.loads(capsys.readouterr().out)
    assert output["outcome"] == "returned"
    assert output["result"] == encode_value({"fresh": True})
    assert output["unconsumed_mock_records"] == []


def test_replay_command_exits_nonzero_for_replay_failure(
    debug_trace_module, monkeypatch, capsys
) -> None:
    class FailingRunner:
        async def run(self, _entry, _mock_records):
            return ReplayResult(
                outcome="raised",
                exception=RuntimeError("missing historical evidence"),
            )

    monkeypatch.setattr(debug_trace_module, "fetch_trace", lambda _trace_id: _trace())
    monkeypatch.setattr(debug_trace_module, "ReplayRunner", FailingRunner)

    with pytest.raises(SystemExit) as error:
        debug_trace_module.main(["0" * 32, "--replay", "test.entry"])

    assert error.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["outcome"] == "raised"
    assert output["exception"]["message"] == "missing historical evidence"


def test_extract_tags_preserves_gate_decision_details(debug_trace_module) -> None:
    reason = "missing observable runtime source binding; " * 20
    span = {
        "tags": [
            _tag("gate.decision.0.reason", reason),
            _tag("ordinary.attribute", reason),
        ]
    }

    tags = debug_trace_module.extract_tags(span)

    assert tags["gate.decision.0.reason"] == reason
    assert tags["ordinary.attribute"].endswith("...")
