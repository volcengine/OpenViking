# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from openviking.telemetry import replay
from openviking.telemetry.replay import (
    MockRecord,
    ReplayCodecError,
    ReplayDataMissingError,
    ReplaySession,
    bind_replay_session,
    decode_value,
    encode_value,
)


def test_registered_codec_round_trips_nested_metadata() -> None:
    @dataclass
    class Payload:
        metadata: dict[str, object]

    @replay.codec(Payload, name="test.payload")
    class PayloadCodec:
        @staticmethod
        def encode(value: Payload, encode):
            return {"metadata": encode(value.metadata)}

        @staticmethod
        def decode(payload, decode):
            return Payload(metadata=decode(payload["metadata"]))

    value = Payload(metadata={"nested": [1, {"ok": True, "nothing": None}]})

    assert decode_value(encode_value(value)) == value


def test_unknown_runtime_object_is_not_stringified() -> None:
    with pytest.raises(ReplayCodecError, match="No replay codec"):
        encode_value(object())


def test_dict_keys_must_be_strings() -> None:
    with pytest.raises(ReplayCodecError, match="string keys"):
        encode_value({1: "value"})


@pytest.mark.asyncio
async def test_replay_mock_returns_recorded_value_without_calling_body() -> None:
    calls = 0

    @replay.mock("test.lookup", match=["key"])
    async def lookup(key: str) -> str:
        nonlocal calls
        calls += 1
        return "live"

    session = ReplaySession.from_records(
        [
            MockRecord(
                name="test.lookup",
                match_key=encode_value({"key": "a"}),
                outcome="returned",
                result=encode_value("recorded"),
            )
        ]
    )

    with bind_replay_session(session):
        assert await lookup("a") == "recorded"
    assert calls == 0


@pytest.mark.asyncio
async def test_missing_replay_mock_fails_without_live_fallback() -> None:
    @replay.mock("test.lookup", match=["key"])
    async def lookup(key: str) -> str:
        return "live"

    with bind_replay_session(ReplaySession.from_records([])):
        with pytest.raises(ReplayDataMissingError, match="test.lookup"):
            await lookup("missing")


@pytest.mark.asyncio
async def test_replay_mock_consumes_matching_records_in_order() -> None:
    @replay.mock("test.lookup", match=["key"])
    async def lookup(key: str) -> str:
        return "live"

    match_key = encode_value({"key": "a"})
    session = ReplaySession.from_records(
        [
            MockRecord(
                name="test.lookup",
                match_key=match_key,
                outcome="returned",
                result=encode_value("first"),
            ),
            MockRecord(
                name="test.lookup",
                match_key=match_key,
                outcome="returned",
                result=encode_value("second"),
            ),
        ]
    )

    with bind_replay_session(session):
        assert await lookup("a") == "first"
        assert await lookup("a") == "second"

    assert session.unconsumed_records == []


@pytest.mark.asyncio
async def test_entry_and_mock_record_structured_span_attributes(monkeypatch) -> None:
    spans = []

    class FakeSpan:
        def __init__(self, name):
            self.name = name
            self.attributes = {}

        def set_attribute(self, key, value):
            self.attributes[key] = value

        def record_exception(self, _error):
            pass

    class FakeSpanContext:
        def __init__(self, name):
            self.span = FakeSpan(name)

        def __enter__(self):
            spans.append(self.span)
            return self.span

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(
        "openviking.telemetry.replay.api.tracer.start_as_current_span",
        lambda name: FakeSpanContext(name),
    )

    @replay.mock("test.record.lookup", match=["key"])
    async def lookup(key: str) -> str:
        return f"value:{key}"

    @replay.entry("test.record.entry")
    async def execute(key: str) -> str:
        return await lookup(key)

    assert await execute("a") == "value:a"

    entry_span, mock_span = spans
    assert entry_span.name == "replay.entry:test.record.entry"
    assert mock_span.name == "replay.mock:test.record.lookup"
    assert entry_span.attributes["replay.kind"] == "entry"
    assert mock_span.attributes["replay.kind"] == "mock"
    assert json.loads(entry_span.attributes["replay.arguments"]) == encode_value({"key": "a"})
    assert json.loads(mock_span.attributes["replay.match"]) == encode_value({"key": "a"})
    assert json.loads(mock_span.attributes["replay.result"]) == encode_value("value:a")
