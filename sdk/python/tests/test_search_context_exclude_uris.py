# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

import openviking_sdk.client as sdk_client


@pytest.mark.parametrize(
    ("excluded", "expected"),
    [
        (["/resources/archive"], ["viking://resources/archive"]),
        (["resources/archive"], ["viking://resources/archive"]),
        (
            ["/resources/archive", "viking://resources/manual"],
            ["viking://resources/archive", "viking://resources/manual"],
        ),
        (["viking://resources/archive"], ["viking://resources/archive"]),
        ([], []),
        (["/resources/{year}"], ["viking://resources/{year}"]),
    ],
)
def test_search_context_normalizes_exclusion_uris(monkeypatch, excluded, expected):
    config = SimpleNamespace(
        url="https://sdk-control.invalid",
        api_key=None,
        account=None,
        user=None,
        actor_peer_id=None,
        gateway_token=None,
        timeout=1.0,
        extra_headers={},
        profile_enabled=False,
        upload_mode=None,
        auth_mode=None,
        ldap_username=None,
        ldap_password=None,
        oidc_token=None,
    )
    monkeypatch.setattr(sdk_client, "resolve_client_config", lambda **kwargs: config)
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "result": {"entries": []}})

    options = {"exclude_uris": excluded}
    original = copy.deepcopy(options)

    async def run():
        client = sdk_client.AsyncHTTPClient(url=config.url)
        client._http = httpx.AsyncClient(
            base_url=config.url, transport=httpx.MockTransport(transport), trust_env=False
        )
        try:
            return await client.search_context(
                "setup", target_uri="/resources/guide", options=options
            )
        finally:
            await client.close()

    assert asyncio.run(run()) == {"entries": []}
    assert requests[0]["target_uri"] == "viking://resources/guide"
    assert requests[0]["exclude_uris"] == expected
    assert options == original
