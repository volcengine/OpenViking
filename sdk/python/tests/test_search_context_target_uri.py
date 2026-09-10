# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""``search_context`` must refuse ``target_uri`` rather than send it.

``POST /search`` rejects ``target_uri`` in context mode — the context path picks its own
roots per bucket, so a caller-supplied one has nowhere to apply. The SDK carried the
parameter anyway, so it read as supported and every non-empty value came back as a server
error one round trip later.

``SyncHTTPClient.search_context`` delegates to the async client, so both are covered by
the one guard; the sync case is exercised here to pin that.
"""

import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient


def _client() -> AsyncHTTPClient:
    return AsyncHTTPClient(url="http://localhost:1933", api_key="k")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_uri",
    ["viking://user/u/memories", ["viking://user/u/memories"]],
    ids=["str", "list"],
)
async def test_async_search_context_refuses_a_target_uri(target_uri):
    with pytest.raises(ValueError, match="target_uri is not supported by search_context"):
        await _client().search_context(query="q", target_uri=target_uri)


def test_sync_search_context_refuses_a_target_uri():
    client = SyncHTTPClient(url="http://localhost:1933", api_key="k")

    with pytest.raises(ValueError, match="target_uri is not supported by search_context"):
        client.search_context(query="q", target_uri="viking://user/u/memories")


@pytest.mark.asyncio
@pytest.mark.parametrize("target_uri", ["", []], ids=["empty-str", "empty-list"])
async def test_the_empty_default_is_still_accepted(target_uri, monkeypatch):
    # The guard must not reject the default, which is what every caller that does not use
    # target_uri passes. Stop at the request so no server is needed.
    class _Sent(Exception):
        pass

    async def _request(self, method, path, **kwargs):
        raise _Sent(path)

    monkeypatch.setattr(AsyncHTTPClient, "_request", _request)

    with pytest.raises(_Sent, match="/api/v1/search/search"):
        await _client().search_context(query="q", target_uri=target_uri)
