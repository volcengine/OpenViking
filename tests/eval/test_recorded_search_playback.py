# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.eval.ragas.playback import IOPlayback
from openviking.eval.recorder import IORecord, IORecorder
from openviking.eval.recorder.wrapper import RecordingVikingDB


def test_replay_search_from_recorder(tmp_path):
    recorder = IORecorder(enabled=True, record_file=str(tmp_path / "search.jsonl"))
    source = AsyncMock()
    source.search.return_value = []
    vector = [0.25, 0.75]
    where = {"kind": "note"}
    asyncio.run(RecordingVikingDB(source, recorder).search("notes", vector, top_k=3, filter=where))
    source.search.assert_awaited_once_with(query_vector=vector, filter=where, limit=3)

    (record,) = recorder.get_records()
    playback = IOPlayback()
    playback._vector_store = AsyncMock()
    assert asyncio.run(playback.play_record(record)).playback_success
    playback._vector_store.search.assert_awaited_once_with(
        query_vector=vector, filter=where, limit=3
    )


@pytest.mark.parametrize(
    "request_data",
    [
        {"args": ["notes", [0.25, 0.75], 3, {"kind": "note"}]},
        {"kwargs": {"vector": [0.25, 0.75], "top_k": 3, "filter": {"kind": "note"}}},
        {"kwargs": {"query_vector": [0.25, 0.75], "limit": 3, "filter": {"kind": "note"}}},
    ],
)
def test_replay_search_from_call_arguments(request_data):
    record = IORecord(
        timestamp="2026-01-01T00:00:00",
        io_type="vikingdb",
        operation="search",
        request=request_data,
    )
    playback = IOPlayback()
    playback._vector_store = AsyncMock()
    assert asyncio.run(playback.play_record(record)).playback_success
    playback._vector_store.search.assert_awaited_once_with(
        query_vector=[0.25, 0.75], filter={"kind": "note"}, limit=3
    )
