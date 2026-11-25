# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Context propagation for the synchronous-to-asynchronous bridge."""

import asyncio
from contextvars import ContextVar

import pytest

from openviking_cli.utils import async_utils


@pytest.fixture(autouse=True)
def shutdown_bridge():
    yield
    async_utils._shutdown_loop()


def test_ordinary_sync_call_preserves_context():
    value = ContextVar("bridge_value", default="missing")
    token = value.set("request")

    async def read():
        return value.get()

    try:
        assert async_utils.run_async(read()) == "request"
    finally:
        value.reset(token)


def test_sync_call_from_foreign_running_loop_preserves_context():
    value = ContextVar("bridge_value", default="missing")

    async def read():
        return value.get()

    async def foreign():
        value.set("foreign")
        return async_utils.run_async(read())

    assert asyncio.run(foreign()) == "foreign"


def test_reentrant_call_preserves_context_from_shared_loop():
    value = ContextVar("bridge_value", default="missing")

    async def read():
        return value.get()

    async def outer():
        value.set("shared-loop")
        return async_utils.run_async(read())

    assert async_utils.run_async(outer()) == "shared-loop"


def test_reentrant_context_modifications_do_not_leak_to_parent():
    value = ContextVar("bridge_value", default="missing")

    async def change():
        before = value.get()
        value.set("child")
        return before, value.get()

    async def outer():
        value.set("parent")
        child = async_utils.run_async(change())
        return child, value.get()

    assert async_utils.run_async(outer()) == (("parent", "child"), "parent")


def test_each_reentrant_call_gets_the_current_context():
    value = ContextVar("bridge_value", default="missing")

    async def read():
        return value.get()

    async def outer():
        value.set("first")
        first = async_utils.run_async(read())
        value.set("second")
        second = async_utils.run_async(read())
        return first, second

    assert async_utils.run_async(outer()) == ("first", "second")
