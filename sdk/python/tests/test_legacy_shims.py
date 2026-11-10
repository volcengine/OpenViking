# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import sys
from pathlib import Path

import httpx
import pytest
from openviking_sdk.client import ERROR_CODE_TO_EXCEPTION

SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _purge_legacy_modules() -> None:
    for name in list(sys.modules):
        if (
            name == "openviking_sdk"
            or name.startswith("openviking_sdk.")
            or name == "openviking_cli"
            or name.startswith("openviking_cli.")
        ):
            sys.modules.pop(name, None)


def test_legacy_async_http_client_shim_points_to_sdk():
    _purge_legacy_modules()
    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient

    client = LegacyAsyncHTTPClient(url="http://localhost:1933")
    assert client._url == "http://localhost:1933"
    try:
        client._raise_exception({"code": "CONFLICT", "message": "boom"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "CONFLICT"


def test_legacy_async_http_client_shim_imports_from_repo_checkout(monkeypatch):
    _purge_legacy_modules()
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if path != str(SDK_ROOT)],
    )

    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient

    client = LegacyAsyncHTTPClient(url="http://localhost:1933")
    assert client._url == "http://localhost:1933"


def test_legacy_async_http_client_shim_raises_legacy_exceptions():
    _purge_legacy_modules()
    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient
    from openviking_cli.exceptions import ConflictError

    client = LegacyAsyncHTTPClient(url="http://localhost:1933")

    with pytest.raises(ConflictError) as exc_info:
        client._raise_exception({"code": "CONFLICT", "message": "boom"})

    assert exc_info.value.code == "CONFLICT"


def test_legacy_sync_http_client_shim_points_to_sdk():
    _purge_legacy_modules()
    from openviking_cli.client.sync_http import SyncHTTPClient as LegacySyncHTTPClient

    client = LegacySyncHTTPClient(url="http://localhost:1933")
    assert client._async_client._url == "http://localhost:1933"


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("code", ERROR_CODE_TO_EXCEPTION)
async def test_legacy_clients_preserve_server_error_details(sync, code):
    from openviking_cli.client.http import AsyncHTTPClient
    from openviking_cli.client.sync_http import SyncHTTPClient
    from openviking_cli.exceptions import OpenVikingError

    details = {
        "resource": "viking://resources/demo",
        "uri": "viking://resources/demo",
        "retryable": True,
        "upstream_status_code": 503,
        "provider": {"retry_after": 2},
    }
    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    async_client = client._async_client if sync else client
    async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={
                    "status": "error",
                    "error": {"code": code, "message": "busy", "details": details},
                },
            )
        ),
    )
    try:
        with pytest.raises(OpenVikingError) as raised:
            if sync:
                client.stat("viking://resources/demo")
            else:
                await client.stat("viking://resources/demo")
        assert raised.value.code == code
        assert {key: raised.value.details[key] for key in details} == details
    finally:
        await async_client.close()
