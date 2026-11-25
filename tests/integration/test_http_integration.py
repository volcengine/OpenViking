# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Error and observer contracts against a locally started HTTP server."""

import pytest

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.client.sync_http import SyncHTTPClient
from openviking_cli.exceptions import InvalidURIError, NotFoundError


@pytest.mark.asyncio
async def test_stat_not_found(server_url):
    client = AsyncHTTPClient(url=server_url)
    await client.initialize()
    try:
        with pytest.raises(NotFoundError) as missing:
            await client.stat("viking://resources/missing.md")
        with pytest.raises(InvalidURIError) as invalid:
            await client.stat("viking://nonexistent/path")
    finally:
        await client.close()

    assert missing.value.code == "NOT_FOUND"
    assert invalid.value.code == "INVALID_URI"


def test_observer_access(server_url):
    client = SyncHTTPClient(url=server_url)
    client.initialize()
    try:
        queue = client.queue_status(format="json")
        vikingdb = client.vikingdb_status(format="json")
        system = client.get_status(format="json")
    finally:
        client.close()

    assert queue["name"] == "queue" and "pending" in queue["status"]["summary"]
    assert vikingdb["name"] == "vikingdb" and vikingdb["status"]["backend"] == "local"
    assert system["components"]["queue"]["name"] == queue["name"]
    assert system["components"]["vikingdb"]["name"] == vikingdb["name"]
    assert system["is_healthy"] == all(
        component["is_healthy"] for component in system["components"].values()
    )
