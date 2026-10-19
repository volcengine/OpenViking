# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient


def _delete_group_transport(requests):
    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"status": "ok", "result": {"deleted": True}},
        )

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{}, {"force": False}, {"force": True}])
async def test_async_delete_group_force_is_opt_in(options):
    requests = []
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=_delete_group_transport(requests)
    )
    try:
        result = await client.admin_delete_group("acme", "engineering", **options)
    finally:
        await client.close()

    assert result == {"deleted": True}
    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url.path == "/api/v1/admin/accounts/acme/groups/engineering"
    assert dict(requests[0].url.params) == ({"force": "true"} if options.get("force") else {})


@pytest.mark.parametrize("options", [{}, {"force": False}, {"force": True}])
def test_sync_delete_group_force_is_opt_in(options):
    requests = []
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=_delete_group_transport(requests)
    )
    try:
        result = client.admin_delete_group("acme", "engineering", **options)
    finally:
        client.close()

    assert result == {"deleted": True}
    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url.path == "/api/v1/admin/accounts/acme/groups/engineering"
    assert dict(requests[0].url.params) == ({"force": "true"} if options.get("force") else {})
