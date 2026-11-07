# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""ROOT task cancellation.

Regression coverage for a ROOT identity being unable to stop a long-running
task such as Compile: the cancel endpoint rejected it outright.
"""

import httpx
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.server.routers import tasks as tasks_router
from openviking.service.task_domain import TaskStatus
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier


class _MemoryAgfs:
    """Minimal AGFS client backed by a dict, enough for PersistentTaskStore."""

    def __init__(self):
        self.files = {}

    def mkdir(self, path, **kwargs):
        pass

    def ls(self, path, **kwargs):
        return []

    def read(self, path, **kwargs):
        if path not in self.files:
            raise AGFSNotFoundError(path)
        return self.files[path]

    def write(self, path, data, **kwargs):
        self.files[path] = data

    def rm(self, path, **kwargs):
        self.files.pop(path, None)


class _Harness:
    def __init__(self, app, tracker):
        self.app = app
        self.tracker = tracker
        self._ctx = None

    def use(self, ctx: RequestContext) -> None:
        self._ctx = ctx

    async def current(self) -> RequestContext:
        return self._ctx


def _ctx(account_id: str = "acme", user_id: str = "alice", role: Role = Role.USER):
    return RequestContext(user=UserIdentifier(account_id, user_id), role=role)


@pytest_asyncio.fixture
async def harness():
    tracker = TaskTracker(PersistentTaskStore(_MemoryAgfs()))
    set_task_tracker(tracker)
    harness = _Harness(FastAPI(), tracker)

    @harness.app.exception_handler(OpenVikingError)
    async def _error_handler(request: Request, exc: OpenVikingError):
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500),
            content={"status": "error", "error": {"code": exc.code, "message": exc.message}},
        )

    harness.app.include_router(tasks_router.router)
    harness.app.dependency_overrides[get_request_context] = harness.current
    yield harness
    set_task_tracker(None)


@pytest_asyncio.fixture
async def client(harness):
    transport = httpx.ASGITransport(app=harness.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_root_can_cancel_another_users_task(client, harness):
    task = await harness.tracker.create("add_resource", account_id="acme", user_id="alice")
    harness.use(_ctx(user_id="root", role=Role.ROOT))

    response = await client.post(f"/api/v1/tasks/{task.task_id}/cancel")

    assert response.status_code == 200, response.text
    assert response.json()["result"]["status"] in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}

    status = await client.get(f"/api/v1/tasks/{task.task_id}")
    assert status.status_code == 200, status.text
    assert status.json()["result"]["status"] in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}


async def test_user_cannot_cancel_another_users_task(client, harness):
    task = await harness.tracker.create("add_resource", account_id="acme", user_id="alice")
    harness.use(_ctx(user_id="bob"))

    response = await client.post(f"/api/v1/tasks/{task.task_id}/cancel")

    assert response.status_code == 404, response.text
    stored = await harness.tracker.get(task.task_id, account_id="acme", user_id="alice")
    assert stored is not None
    assert stored.status == TaskStatus.PENDING


async def test_root_cancel_unknown_task_returns_not_found(client, harness):
    harness.use(_ctx(user_id="root", role=Role.ROOT))

    response = await client.post("/api/v1/tasks/task-missing/cancel")

    assert response.status_code == 404, response.text
