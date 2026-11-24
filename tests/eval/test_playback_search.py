# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Playback must replay a recorded VikingDB search with its query vector."""

from openviking.eval.ragas.playback import IOPlayback
from openviking.eval.recorder import IORecord


class _StubVectorStore:
    def __init__(self) -> None:
        self.searches = []

    async def search(self, query_vector=None, filter=None, limit=None):
        self.searches.append(
            {"query_vector": query_vector, "filter": filter, "limit": limit}
        )
        return []


async def test_play_vikingdb_search_reuses_recorded_query_vector() -> None:
    # RecordingVikingDB records a flat request: no positional args and no
    # nested kwargs, so the vector lives on the request itself.
    record = IORecord(
        timestamp="2026-11-24T00:00:00Z",
        io_type="vikingdb",
        operation="search",
        request={
            "collection": "notes",
            "vector": [0.25, 0.75],
            "top_k": 3,
            "filter": {"kind": "note"},
        },
    )

    playback = IOPlayback()
    playback._vector_store = _StubVectorStore()

    result = await playback._play_vikingdb_operation(record)

    assert result.playback_success is True
    assert playback._vector_store.searches == [
        {"query_vector": [0.25, 0.75], "filter": {"kind": "note"}, "limit": 3}
    ]
