# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SDK-level tests for the time-window parameters of the Python SDK.

The SDK is loaded from the repository checkout rather than from an installed
distribution, so the test exercises the parameters this change adds.
"""

import importlib.util
import sys
from pathlib import Path

import pytest_asyncio

SDK_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "sdk" / "python" / "openviking_sdk"
SESSION_URI = "viking://user/sdk_test_user/sessions"


def _load_repo_sdk():
    """Import the SDK from the checkout, whatever is installed in site-packages."""
    name = "openviking_sdk_repo"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name,
        SDK_PACKAGE_DIR / "__init__.py",
        submodule_search_locations=[str(SDK_PACKAGE_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AsyncHTTPClient = _load_repo_sdk().AsyncHTTPClient


@pytest_asyncio.fixture()
async def sdk_client(running_server):
    """An SDK client bound to the running server."""
    port, svc, sdk_user_key = running_server
    client = AsyncHTTPClient(
        url=f"http://127.0.0.1:{port}",
        api_key=sdk_user_key,
        account="",
        user="",
        timeout=33.0,
        extra_headers={},
        profile_enabled=False,
    )
    await client.initialize()
    yield client, svc
    await client.close()


async def test_sdk_list_sessions_forwards_the_time_window(sdk_client):
    client, _ = sdk_client
    created = await client.create_session(session_id="sdk-time-filter-session")
    session_id = created["session_id"]

    unfiltered = await client.list_sessions()
    assert session_id in {item["session_id"] for item in unfiltered}

    inside = await client.list_sessions(since="2000-01-01")
    assert session_id in {item["session_id"] for item in inside}

    for time_field in ("updated_at", "created_at", "mod_time"):
        matched = await client.list_sessions(since="2000-01-01", time_field=time_field)
        assert session_id in {item["session_id"] for item in matched}

    assert await client.list_sessions(since="2099-01-01") == []
    assert await client.list_sessions(until="2000-01-01") == []
    assert len(await client.list_sessions(limit=1)) == 1


async def test_sdk_ls_page_forwards_the_time_window(sdk_client):
    client, _ = sdk_client
    await client.create_session(session_id="sdk-time-filter-ls-session")

    page = await client.ls_page(SESSION_URI, since="2000-01-01")
    assert page["result"]

    future = await client.ls_page(SESSION_URI, since="2099-01-01")
    assert future["result"] == []

    past = await client.ls_page(SESSION_URI, until="2000-01-01")
    assert past["result"] == []
