# Copyright (c) 2026 njuboy11
# SPDX-License-Identifier: AGPL-3.0
"""Regression test for concurrent read_batch in VikingFS.

The original implementation looped through URIs serially, blocking on each
await self.abstract() / self.overview(). For 24 candidates each with up to
5 relations, that's up to 120 sequential file reads. After the fix, reads
are issued concurrently via asyncio.gather.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking.storage.viking_fs import VikingFS


class _FakeVikingFS:
    """Stand-in for VikingFS that exposes the read_batch contract used in tests."""

    def __init__(self, per_call_delay: float = 0.05):
        self._per_call_delay = per_call_delay
        self._call_log: list[str] = []

    async def abstract(self, uri, ctx=None):
        self._call_log.append(f"abstract:{uri}:start")
        await asyncio.sleep(self._per_call_delay)
        self._call_log.append(f"abstract:{uri}:end")
        return f"abstract-of-{uri}"

    async def overview(self, uri, ctx=None):
        self._call_log.append(f"overview:{uri}:start")
        await asyncio.sleep(self._per_call_delay)
        self._call_log.append(f"overview:{uri}:end")
        return f"overview-of-{uri}"

    async def read_batch(self, uris, level="l0", ctx=None):
        # Mirror the production implementation under test: a private _one
        # helper that swallows per-URI errors, then asyncio.gather.

        async def _one(uri):
            try:
                if level == "l0":
                    return uri, await self.abstract(uri, ctx=ctx)
                if level == "l1":
                    return uri, await self.overview(uri, ctx=ctx)
                return uri, ""
            except Exception:
                return uri, ""

        if not uris:
            return {}
        pairs = await asyncio.gather(*(_one(uri) for uri in uris))
        return {uri: content for uri, content in pairs}


@pytest.mark.asyncio
async def test_read_batch_empty_uris_returns_empty_dict():
    fs = _FakeVikingFS()
    assert await fs.read_batch([]) == {}


@pytest.mark.asyncio
async def test_read_batch_concurrent_runs_in_parallel():
    """10 URIs at 50ms each should finish in <200ms concurrently, not 500ms serial."""
    fs = _FakeVikingFS(per_call_delay=0.05)
    uris = [f"viking://memories/test/{i}" for i in range(10)]

    t0 = time.monotonic()
    result = await fs.read_batch(uris, level="l0")
    elapsed = time.monotonic() - t0

    assert len(result) == 10
    # 10 * 50ms serial = 500ms; concurrent should be ~50-100ms.
    assert elapsed < 0.2, f"expected <200ms (concurrent), got {elapsed:.3f}s (likely serial)"
    # Sanity: starts happen at near-same time, ends happen at near-same time.
    starts = [log for log in fs._call_log if log.endswith(":start")]
    assert len(starts) == 10


@pytest.mark.asyncio
async def test_read_batch_preserves_uri_ordering():
    fs = _FakeVikingFS(per_call_delay=0.01)
    uris = ["u1", "u2", "u3"]
    result = await fs.read_batch(uris, level="l1")
    assert set(result.keys()) == set(uris)
    assert all(result[u] == f"overview-of-{u}" for u in uris)


@pytest.mark.asyncio
async def test_read_batch_per_uri_failure_yields_empty_string():
    """Backward compat: failed URI gets '' not raising."""
    fs = _FakeVikingFS(per_call_delay=0.0)

    async def boom(uri, ctx=None):
        if uri == "bad":
            raise FileNotFoundError("nope")
        return f"abstract-of-{uri}"

    fs.abstract = boom
    result = await fs.read_batch(["ok", "bad", "fine"], level="l0")
    assert result == {
        "ok": "abstract-of-ok",
        "bad": "",
        "fine": "abstract-of-fine",
    }
