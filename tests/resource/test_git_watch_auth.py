# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.resource.git_watch_auth import (
    create_git_http_auth_state,
    git_http_auth_config_from_state,
    is_git_http_auth_state,
)
from openviking.resource.watch_manager import WatchTask
from openviking.utils.git_auth import GitHttpAuthConfig
from openviking_cli.exceptions import InvalidArgumentError


def test_git_watch_auth_state_round_trip_is_repository_bound():
    repo_url = "https://git.example/org/private.git"
    state = create_git_http_auth_state(
        GitHttpAuthConfig(username="git-user", token="git-secret"),
        repo_url,
    )

    assert is_git_http_auth_state(state)
    assert git_http_auth_config_from_state(state, repo_url) == {
        "username": "git-user",
        "token": "git-secret",
    }


def test_git_watch_auth_state_rejects_a_different_repository_without_leaking_token():
    state = create_git_http_auth_state(
        GitHttpAuthConfig(username="git-user", token="git-secret"),
        "https://git.example/org/private.git",
    )

    with pytest.raises(InvalidArgumentError) as exc_info:
        git_http_auth_config_from_state(
            state,
            "https://attacker.example/org/private.git",
        )

    assert "git-secret" not in str(exc_info.value)


def test_git_watch_auth_state_is_persisted_but_hidden_from_public_task_dict():
    repo_url = "https://git.example/org/private.git"
    state = create_git_http_auth_state(
        GitHttpAuthConfig(username="git-user", token="git-secret"),
        repo_url,
    )
    task = WatchTask(path=repo_url, auth_state=state)

    stored = task.to_storage_dict()
    restored = WatchTask.from_dict(stored)

    assert stored["auth_state"] == state
    assert restored.auth_state == state
    assert "auth_state" not in task.to_dict()
    assert "git-secret" not in str(task.to_dict())


@pytest.mark.parametrize("operation", ["preflight", "clone"])
@pytest.mark.parametrize(
    "stderr,auth_failure",
    [
        (b"fatal: Authentication failed for 'https://secret-token@example/repo'", True),
        (b"remote: HTTP Basic: Access denied", True),
        (b"git@example: Permission denied (publickey).", True),
        (b"fatal: could not read Username: terminal prompts disabled", True),
        (b"fatal: The requested URL returned error: 401", True),
        (b"fatal: Could not resolve host: example", False),
        (b"fatal: repository not found", False),
        (b"fatal: unable to access: Connection timed out", False),
        (b"fatal: cannot create directory: Permission denied", False),
    ],
)
async def test_git_commands_preserve_only_explicit_auth_failure(
    monkeypatch, operation, stderr, auth_failure
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openviking.parse.accessors.git_accessor import GitAccessor
    from openviking.service.resource_service import ResourceService
    from openviking.utils.git_auth import GIT_AUTH_FAILED

    process = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", stderr)))
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(Exception) as caught:
        if operation == "preflight":
            await ResourceService()._preflight_git_source("https://example/repo")
        else:
            await GitAccessor()._run_git(["git", "clone", "https://example/repo"])
    assert (getattr(caught.value, "code", None) == GIT_AUTH_FAILED) is auth_failure
    assert "secret-token" not in str(caught.value)


@pytest.mark.parametrize("mode", ["exception", "result", "async_task"])
@pytest.mark.parametrize("auth_failure", [True, False])
async def test_git_watch_pauses_only_on_auth_failure(monkeypatch, mode, auth_failure):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openviking.resource.watch_manager import WatchManager
    from openviking.resource.watch_scheduler import WatchScheduler
    from openviking.service.resource_service import ResourceService
    from openviking.utils.git_auth import GIT_AUTH_FAILED
    from openviking_cli.exceptions import OpenVikingError

    error = "Git authentication failed." if auth_failure else "Connection timed out"
    code = GIT_AUTH_FAILED if auth_failure else "UNKNOWN"
    service = ResourceService()
    service.refresh_resource = AsyncMock(
        return_value={"status": "failed", "error": error, "code": code}
    )
    if mode == "exception":
        service.refresh_resource.side_effect = OpenVikingError(error, code=code)
    elif mode == "async_task":
        service.refresh_resource.return_value = {"status": "queued", "task_id": "git-import"}
        tracker = AsyncMock()
        tracker.wait.return_value = SimpleNamespace(
            status=SimpleNamespace(value="failed"), error=error, result={"code": code}
        )
        monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    scheduler = WatchScheduler(resource_service=service)
    manager = WatchManager(viking_fs=None)
    await manager.initialize()
    scheduler._watch_manager = manager
    task = await manager.create_task(
        path="https://github.com/org/repo.git",
        to_uri="viking://resources/repo",
        watch_interval=30,
    )
    await scheduler._execute_task(task)
    updated = await manager.get_task(task.task_id)
    assert updated.last_status == "failed"
    assert updated.last_error == error
    assert updated.is_active is (not auth_failure)
    assert (updated.next_execution_time is None) is auth_failure
