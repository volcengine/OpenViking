# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking_sdk.client import Session, SyncSession


@pytest.mark.parametrize("options", [None, {}, {"telemetry": True}])
def test_async_session_forwards_batch_options(options):
    messages = [{"role": "user", "content": "hello"}]
    result = {"session_id": "example", "added": 1}
    batch = AsyncMock(return_value=result)
    session = Session(SimpleNamespace(batch_add_messages=batch), "example")

    if options is None:
        assert asyncio.run(session.batch_add_messages(messages)) == result
        batch.assert_awaited_once_with("example", messages)
    else:
        assert asyncio.run(session.batch_add_messages(messages, options=options)) == result
        batch.assert_awaited_once_with("example", messages, options=options)


@pytest.mark.parametrize("options", [None, {}, {"telemetry": True}])
def test_sync_session_forwards_batch_options(options):
    messages = [{"role": "user", "content": "hello"}]
    result = {"session_id": "example", "added": 1}
    batch = Mock(return_value=result)
    session = SyncSession(SimpleNamespace(batch_add_messages=batch), "example")

    if options is None:
        assert session.batch_add_messages(messages) == result
        batch.assert_called_once_with("example", messages)
    else:
        assert session.batch_add_messages(messages, options=options) == result
        batch.assert_called_once_with("example", messages, options=options)
