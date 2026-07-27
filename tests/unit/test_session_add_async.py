# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Focused tests for the synchronous and asynchronous session add paths."""

import asyncio
import json

from openviking.message import TextPart
from openviking.session import Session


class _RecordingVikingFS:
    def __init__(self) -> None:
        self.operations: list[tuple[str, str, str]] = []

    async def append_file(self, uri: str, content: str, ctx=None) -> None:
        self.operations.append(("append", uri, content))

    async def write_file(self, uri: str, content: str, ctx=None) -> None:
        self.operations.append(("write", uri, content))


class _ConcurrentVikingFS(_RecordingVikingFS):
    def __init__(self) -> None:
        super().__init__()
        self.append_count = 0
        self.both_appends_entered = asyncio.Event()
        self.release_appends = asyncio.Event()

    async def append_file(self, uri: str, content: str, ctx=None) -> None:
        self.append_count += 1
        if self.append_count == 2:
            self.both_appends_entered.set()
        await self.release_appends.wait()
        await super().append_file(uri, content, ctx=ctx)


def test_sync_add_message_returns_after_message_and_meta_are_persisted() -> None:
    fs = _RecordingVikingFS()
    session = Session(viking_fs=fs, session_id="sync-add")

    message = session.add_message("user", [TextPart("hello")])

    assert message is session.messages[0]
    assert [operation[0] for operation in fs.operations] == ["append", "write"]
    assert json.loads(fs.operations[0][2])["id"] == message.id
    persisted_meta = json.loads(fs.operations[1][2])
    assert persisted_meta["message_count"] == 1
    assert persisted_meta["total_message_count"] == 1


async def test_async_add_message_allows_different_sessions_to_wait_concurrently() -> None:
    fs = _ConcurrentVikingFS()
    first_session = Session(viking_fs=fs, session_id="async-add-first")
    second_session = Session(viking_fs=fs, session_id="async-add-second")

    first_task = asyncio.create_task(
        first_session._add_messages_async(
            [{"role": "user", "parts": [TextPart("first")]}]
        )
    )
    second_task = asyncio.create_task(
        second_session._add_messages_async(
            [{"role": "user", "parts": [TextPart("second")]}]
        )
    )

    await asyncio.wait_for(fs.both_appends_entered.wait(), timeout=1)
    assert not first_task.done()
    assert not second_task.done()

    fs.release_appends.set()
    first_messages, second_messages = await asyncio.wait_for(
        asyncio.gather(first_task, second_task),
        timeout=1,
    )

    assert first_messages[0] is first_session.messages[0]
    assert second_messages[0] is second_session.messages[0]
    for session_id in ("async-add-first", "async-add-second"):
        session_operations = [
            operation[0] for operation in fs.operations if f"/{session_id}/" in operation[1]
        ]
        assert session_operations == ["append", "write"]
