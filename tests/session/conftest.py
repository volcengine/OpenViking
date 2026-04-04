# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Session test fixtures"""

import asyncio
from typing import AsyncGenerator

import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.message import TextPart, ToolPart
from openviking.service.task_tracker import TaskStatus, get_task_tracker, reset_task_tracker
from openviking.session import Session


@pytest_asyncio.fixture(autouse=True)
async def _drain_background_tasks(client: AsyncOpenViking):
    """Wait for background commit tasks to finish before client teardown."""
    reset_task_tracker()
    yield
    # Drain asyncio.create_task() background tasks BEFORE client.close()
    tracker = get_task_tracker()
    for _ in range(100):  # up to 10s
        pending = [
            t for t in tracker.list_tasks() if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ]
        if not pending:
            break
        await asyncio.sleep(0.1)
    reset_task_tracker()


@pytest_asyncio.fixture(scope="function")
async def session(client: AsyncOpenViking) -> AsyncGenerator[Session, None]:
    """Create new Session"""
    session = client.session()
    yield session


@pytest_asyncio.fixture(scope="function")
async def session_with_id(client: AsyncOpenViking) -> AsyncGenerator[Session, None]:
    """Create Session with specified ID"""
    session = client.session(session_id="test_session_001")
    yield session


@pytest_asyncio.fixture(scope="function")
async def session_with_messages(client: AsyncOpenViking) -> AsyncGenerator[Session, None]:
    """Create Session with existing messages"""
    session = client.session(session_id="test_session_with_messages")

    session.add_message("user", [TextPart("Hello, this is a test message.")])
    session.add_message("assistant", [TextPart("Hello! How can I help you today?")])
    session.add_message("user", [TextPart("I need help with testing.")])
    session.add_message("assistant", [TextPart("I can help you with testing.")])

    yield session


@pytest_asyncio.fixture(scope="function")
async def session_with_tool_call(
    client: AsyncOpenViking,
) -> AsyncGenerator[tuple[Session, str, str], None]:
    """Create Session with tool call"""
    session = client.session(session_id="test_session_with_tool")

    tool_id = "test_tool_001"
    tool_part = ToolPart(
        tool_id=tool_id,
        tool_name="test_tool",
        tool_uri=f"viking://session/{session.session_id}/tools/{tool_id}",
        skill_uri="viking://agent/skills/test_skill",
        tool_input={"param": "value"},
        tool_status="running",
    )

    msg = session.add_message("assistant", [TextPart("Executing tool..."), tool_part])

    yield session, msg.id, tool_id
