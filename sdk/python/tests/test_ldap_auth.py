# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import base64
import json

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient


@pytest.mark.asyncio
async def test_ldap_credentials_from_file_environment_and_explicit_arguments(
    tmp_path, monkeypatch, sdk_transport
):
    path = tmp_path / "ovcli.conf"
    path.write_text(
        json.dumps(
            {
                "url": "http://localhost:1933",
                "auth_mode": "ldap",
                "ldap_username": "file-user",
                "ldap_password": "file-pass",
                "extra_headers": {"X-Custom": "routing"},
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(path))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"})

    sdk_transport(handle)

    async def health(**kwargs):
        client = AsyncHTTPClient(**kwargs)
        await client.initialize()
        try:
            assert await client.health()
        finally:
            await client.close()
        return requests[-1].headers

    file_headers = await health()
    assert file_headers["Authorization"] == "Basic ZmlsZS11c2VyOmZpbGUtcGFzcw=="
    assert file_headers["X-Custom"] == "routing"
    assert "X-API-Key" not in file_headers

    monkeypatch.setenv("OPENVIKING_AUTH_MODE", "ldap")
    monkeypatch.setenv("OPENVIKING_USERNAME", "env-user")
    monkeypatch.setenv("OPENVIKING_PASSWORD", "env-pass")
    env_headers = await health()
    assert env_headers["Authorization"] == "Basic ZW52LXVzZXI6ZW52LXBhc3M="

    explicit_headers = await health(
        auth_mode="ldap",
        ldap_username="alice",
        ldap_password="päss:w0rd!",
        api_key="test-key",
    )
    scheme, encoded = explicit_headers["Authorization"].split()
    assert scheme == "Basic"
    assert base64.b64decode(encoded) == "alice:päss:w0rd!".encode("utf-8")
    assert explicit_headers["X-API-Key"] == "test-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("ldap_options", [{}, {"auth_mode": "ldap", "ldap_username": "alice"}])
async def test_incomplete_ldap_does_not_send_authorization(ldap_options, sdk_transport):
    def handle(request):
        assert "Authorization" not in request.headers
        assert request.headers["X-API-Key"] == "test-key"
        return httpx.Response(200, json={"status": "ok"})

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933", api_key="test-key", **ldap_options)
    await client.initialize()
    try:
        assert await client.health()
    finally:
        await client.close()
