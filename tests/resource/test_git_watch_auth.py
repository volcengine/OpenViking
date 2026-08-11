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
