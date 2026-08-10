# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Session test fixtures"""

import asyncio
from functools import partial
from typing import AsyncGenerator

import pytest_asyncio

from openviking.message import TextPart, ToolPart
from openviking.server.identity import RequestContext
from openviking.service.core import OpenVikingService
from openviking.session import Session
from openviking.storage.queuefs import QueueManager, SessionCommitMsg, get_queue_manager
from openviking.utils.time_utils import get_current_timestamp


@pytest_asyncio.fixture(scope="function")
async def client(
    service: OpenVikingService,
    request_context: RequestContext,
    monkeypatch,
) -> partial:
    """Bind the shared service's session factory to the test request context."""

    queue_manager = get_queue_manager()
    original_enqueue = queue_manager.enqueue
    commit_tasks = []

    async def enqueue_with_session_commit_fallback(queue_name, data):
        if queue_name != QueueManager.SESSION_COMMIT:
            return await original_enqueue(queue_name, data)

        async def process_commit():
            message = SessionCommitMsg(**data)
            queued_session = service.sessions.session(
                request_context,
                session_id=message.session_id,
                session_uri=message.session_uri,
            )
            while True:
                phase1 = await queued_session._read_phase1_meta(message.archive_uri)
                if phase1.get("status") == "ready" or await queued_session._archive_file_exists(
                    message.archive_uri,
                    ".failed.json",
                ):
                    break
                await asyncio.sleep(0)
            await queued_session.load()
            await queued_session.resume_queued_commit(message)

        commit_tasks.append(asyncio.create_task(process_commit()))
        return data["task_id"]

    monkeypatch.setattr(queue_manager, "enqueue", enqueue_with_session_commit_fallback)
    yield partial(service.sessions.session, request_context)
    if commit_tasks:
        await asyncio.gather(*commit_tasks, return_exceptions=True)


@pytest_asyncio.fixture(scope="function")
async def client_with_resource_sync(
    client,
    service: OpenVikingService,
    request_context: RequestContext,
):
    uri = "viking://resources/session-active-count.md"
    timestamp = get_current_timestamp()
    vector = service.vikingdb_manager.get_embedder().embed("active count test").dense_vector
    await service.vikingdb_manager.upsert(
        {
            "uri": uri,
            "parent_uri": "viking://resources",
            "is_leaf": True,
            "abstract": "Session active count test resource",
            "context_type": "resource",
            "category": "",
            "created_at": timestamp,
            "updated_at": timestamp,
            "active_count": 0,
            "vector": vector,
            "meta": {},
            "related_uri": [],
            "account_id": request_context.account_id,
            "owner_space": "",
            "level": 2,
        },
        ctx=request_context,
    )
    return service, request_context, uri


@pytest_asyncio.fixture(scope="function")
async def session(
    client,
    service: OpenVikingService,
    request_context: RequestContext,
) -> AsyncGenerator[Session, None]:
    """Create new Session"""
    session = await service.sessions.create(request_context)
    yield session


@pytest_asyncio.fixture(scope="function")
async def session_with_messages(
    client,
    service: OpenVikingService,
    request_context: RequestContext,
) -> AsyncGenerator[Session, None]:
    """Create Session with existing messages"""
    session = await service.sessions.create(
        request_context,
        session_id="test_session_with_messages",
    )

    session.add_message("user", [TextPart("Hello, this is a test message.")])
    session.add_message("assistant", [TextPart("Hello! How can I help you today?")])
    session.add_message("user", [TextPart("I need help with testing.")])
    session.add_message("assistant", [TextPart("I can help you with testing.")])

    yield session


@pytest_asyncio.fixture(scope="function")
async def session_with_tool_call(
    client,
    service: OpenVikingService,
    request_context: RequestContext,
) -> AsyncGenerator[tuple[Session, str, str], None]:
    """Create Session with tool call"""
    session = await service.sessions.create(
        request_context,
        session_id="test_session_with_tool",
    )

    tool_id = "test_tool_001"
    tool_part = ToolPart(
        tool_id=tool_id,
        tool_name="test_tool",
        tool_uri=f"{session.uri}/tools/{tool_id}",
        skill_uri="viking://user/skills/test_skill",
        tool_input={"param": "value"},
        tool_status="running",
    )

    msg = session.add_message("assistant", [TextPart("Executing tool..."), tool_part])

    yield session, msg.id, tool_id
