# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Public Python client coverage for add_resource parse_mode."""

import inspect
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock

import pytest

import openviking
from openviking.async_client import AsyncOpenViking
from openviking.client.local import LocalClient
from openviking.parse.mode import ParseMode
from openviking.server.identity import RequestContext, Role
from openviking.sync_client import SyncOpenViking
from openviking_cli.client.sync_http import SyncHTTPClient
from openviking_cli.session.user_id import UserIdentifier


def test_public_python_clients_expose_parse_mode_parameter():
    for method in (
        AsyncOpenViking.add_resource,
        SyncOpenViking.add_resource,
        LocalClient.add_resource,
        SyncHTTPClient.add_resource,
    ):
        assert "parse_mode" in inspect.signature(method).parameters


def test_openviking_exports_parse_mode():
    assert openviking.ParseMode is ParseMode


@pytest.mark.asyncio
async def test_async_embedded_client_forwards_no_split():
    downstream = AsyncMock(return_value={"root_uri": "viking://resources/test"})
    client = object.__new__(AsyncOpenViking)
    client._client = SimpleNamespace(add_resource=downstream)
    client._ensure_initialized = AsyncMock()

    await AsyncOpenViking.add_resource(
        client,
        path="/tmp/manual.pdf",
        parse_mode=ParseMode.NO_SPLIT,
    )

    assert downstream.await_args.kwargs["parse_mode"] is ParseMode.NO_SPLIT


def test_sync_embedded_client_forwards_no_split():
    downstream = AsyncMock(return_value={"root_uri": "viking://resources/test"})
    client = object.__new__(SyncOpenViking)
    client._async_client = SimpleNamespace(add_resource=downstream)

    SyncOpenViking.add_resource(
        client,
        path="/tmp/manual.pdf",
        parse_mode=ParseMode.NO_SPLIT,
    )

    assert downstream.await_args.kwargs["parse_mode"] is ParseMode.NO_SPLIT


@pytest.mark.asyncio
async def test_local_client_forwards_no_split():
    downstream = AsyncMock(return_value={"root_uri": "viking://resources/test"})
    client = object.__new__(LocalClient)
    client._ctx = RequestContext(
        user=UserIdentifier("test_account", "test_user"),
        role=Role.USER,
    )
    client._service = SimpleNamespace(resources=SimpleNamespace(add_resource=downstream))

    await LocalClient.add_resource(
        client,
        path="/tmp/manual.pdf",
        parse_mode=ParseMode.NO_SPLIT,
    )

    assert downstream.await_args.kwargs["parse_mode"] is ParseMode.NO_SPLIT


def test_standalone_sdk_exports_parse_mode_type():
    import openviking_sdk

    assert get_args(openviking_sdk.ParseMode) == ("default", "no_split")
