# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Core routing and task-state tests for Connector imports."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from openviking.connector import delegate as connector_delegate_module
from openviking.parse.mode import ParseMode
from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg, AddResourcePhase
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier

# Deterministic stand-in for the code-hosting predicate: routing tests must
# not depend on the real hosting-domain configuration.
_GIT_REPO_PREFIX = "https://git.example/"


class _BackgroundTask:
    def add_done_callback(self, _callback):
        pass


@pytest.fixture
def connector_config(monkeypatch):
    import openviking_cli.utils.config.open_viking_config as config_module

    config = SimpleNamespace(
        enable=True,
        connector="https://connector.example/doc/add",
        tracker="https://connector.example/task/info",
        timeout_seconds=60,
        poll_interval_ms=10,
        allowed_add_types=["tos"],
    )
    monkeypatch.setattr(
        config_module,
        "get_openviking_config",
        lambda: SimpleNamespace(connector=config),
    )
    monkeypatch.setattr(
        "openviking.connector.routing.is_git_repo_url",
        lambda path: (
            isinstance(path, str) and path.startswith((_GIT_REPO_PREFIX, "https://github.com/"))
        ),
    )
    monkeypatch.setattr(
        "openviking.utils.code_hosting_utils.get_openviking_config",
        lambda: SimpleNamespace(
            code=SimpleNamespace(
                github_domains=["github.com", "git.example"],
                gitlab_domains=[],
                azure_devops_domains=[],
                code_hosting_domains=[],
            )
        ),
    )
    return config


@pytest.fixture
def ctx():
    return RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
        api_key="secret",
    )


@pytest.fixture
def service():
    # Parent of the import target exists by default; the create_parent
    # pre-check tests build their own service with a missing parent.
    return ResourceService(
        vikingdb=object(),
        viking_fs=SimpleNamespace(exists=AsyncMock(return_value=True)),
        resource_processor=object(),
        skill_processor=object(),
    )


def _task_tracker():
    return SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )


def _install_connector_dependencies(monkeypatch, tracker, connector_client):
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )
    monkeypatch.setattr(
        connector_delegate_module,
        "ConnectorClient",
        lambda **_kwargs: connector_client,
    )

    def discard_monitor(coro):
        coro.close()
        return _BackgroundTask()

    monkeypatch.setattr(connector_delegate_module.asyncio, "create_task", discard_monitor)


@pytest.mark.asyncio
async def test_add_resource_routes_tos_to_connector(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    result = await service.add_resource(
        path="tos://bucket/a/b/c",
        ctx=ctx,
        to="viking://resources/x/y",
    )

    assert result == {
        "status": "accepted",
        "task_id": "task-1",
        "connector_task_key": "connector-1",
        "resource_id": "viking://resources/x/y",
    }
    connector_client.submit_doc_add.assert_awaited_once_with(
        add_type="tos",
        api_key="secret",
        tos_path="bucket/a/b/c",
        to="viking://resources/x/y",
        include_child=True,
        param_config=None,
        auth_config=None,
        extra_params=None,
    )
    tracker.create.assert_awaited_once_with(
        "connector_import",
        resource_id="viking://resources/x/y",
        account_id="acct",
        user_id="alice",
    )


@pytest.mark.asyncio
async def test_add_resource_routes_git_repo_to_connector(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    connector_config.allowed_add_types = ["tos", "git"]
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    await service.add_resource(
        path="https://git.example/org/repo.git",
        ctx=ctx,
        to="viking://resources/imports",
        args={"branch": "release"},
    )

    connector_client.submit_doc_add.assert_awaited_once_with(
        add_type="git",
        api_key="secret",
        tos_path=None,
        to="viking://resources/imports",
        include_child=True,
        param_config={
            "repo_url": "https://git.example/org/repo.git",
            "branch": "release",
        },
        auth_config=None,
        extra_params=None,
    )


@pytest.mark.asyncio
async def test_git_connector_maps_ref_arg_to_branch(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    connector_config.allowed_add_types = ["git"]
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    await service.add_resource(
        path="https://git.example/org/repo",
        ctx=ctx,
        to="viking://resources/imports",
        args={"ref": "v1.2"},
    )

    submitted = connector_client.submit_doc_add.await_args.kwargs
    assert submitted["add_type"] == "git"
    assert submitted["param_config"]["branch"] == "v1.2"
    assert submitted["param_config"]["repo_url"] == "https://git.example/org/repo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "expected_branch"),
    [
        (None, "release"),
        ({"branch": "override"}, "override"),
    ],
    ids=["url-branch", "explicit-branch"],
)
async def test_git_connector_preserves_native_tree_url_semantics(
    monkeypatch,
    connector_config,
    ctx,
    service,
    args,
    expected_branch,
):
    connector_config.allowed_add_types = ["git"]
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    await service.add_resource(
        path="https://github.com/acme/repo/tree/release",
        ctx=ctx,
        to="viking://resources/imports",
        args=args,
    )

    submitted = connector_client.submit_doc_add.await_args.kwargs
    assert submitted["param_config"] == {
        "repo_url": "https://github.com/acme/repo",
        "branch": expected_branch,
    }


def test_git_commit_tree_url_falls_back_to_native(connector_config, service):
    connector_config.allowed_add_types = ["git"]

    assert (
        service._connector.should_delegate(
            "https://github.com/acme/repo/tree/deadbee",
            to="viking://resources/imports",
        )
        is False
    )


def test_git_commit_tree_url_with_credentials_fails_closed(connector_config, service):
    connector_config.allowed_add_types = ["git"]

    with pytest.raises(InvalidArgumentError, match="cannot fall back") as exc_info:
        service._connector.should_delegate(
            "https://github.com/acme/repo/tree/deadbee",
            to="viking://resources/imports",
            connector_args={"token": "ghp-secret"},
        )

    assert "ghp-secret" not in str(exc_info.value)


_FULL_COMMIT_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def test_git_full_commit_arg_routes_to_connector(connector_config, service):
    connector_config.allowed_add_types = ["git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            to="viking://resources/imports",
            connector_args={"commit": _FULL_COMMIT_SHA},
        )
        is True
    )


def test_git_full_commit_with_credentials_routes_to_connector(connector_config, service):
    connector_config.allowed_add_types = ["git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            to="viking://resources/imports",
            connector_args={"commit": _FULL_COMMIT_SHA, "token": "ghp-secret"},
        )
        is True
    )


@pytest.mark.parametrize("branch_arg", ["branch", "ref"])
def test_git_commit_with_branch_falls_back_to_native_for_wait(
    connector_config,
    service,
    branch_arg,
):
    connector_config.allowed_add_types = ["git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            to="viking://resources/imports",
            wait=True,
            connector_args={"commit": _FULL_COMMIT_SHA, branch_arg: "main"},
        )
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "args"),
    [
        (f"https://github.com/acme/repo/tree/{_FULL_COMMIT_SHA}", None),
        (
            "https://github.com/acme/repo",
            {"commit": _FULL_COMMIT_SHA, "branch": "main"},
        ),
        (
            "https://github.com/acme/repo",
            {"commit": _FULL_COMMIT_SHA, "ref": "release"},
        ),
    ],
    ids=["tree-url", "commit-over-branch", "commit-over-ref"],
)
async def test_git_connector_pins_full_commit_in_param_config(
    monkeypatch,
    connector_config,
    ctx,
    service,
    path,
    args,
):
    connector_config.allowed_add_types = ["git"]
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    await service.add_resource(
        path=path,
        ctx=ctx,
        to="viking://resources/imports",
        args=args,
    )

    submitted = connector_client.submit_doc_add.await_args.kwargs
    assert submitted["param_config"] == {
        "repo_url": "https://github.com/acme/repo",
        "commit": _FULL_COMMIT_SHA,
    }


@pytest.mark.asyncio
async def test_connector_import_persists_task_before_remote_submission(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    tracker = _task_tracker()

    async def fail_submission(**_kwargs):
        tracker.create.assert_awaited_once()
        raise RuntimeError("submission failed")

    connector_client = SimpleNamespace(submit_doc_add=AsyncMock(side_effect=fail_submission))
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    with pytest.raises(RuntimeError, match="submission failed"):
        await service.add_resource(
            path="tos://bucket/prefix",
            ctx=ctx,
            to="viking://resources/imports",
        )

    tracker.fail.assert_awaited_once_with(
        "task-1",
        "submission failed",
        account_id="acct",
        user_id="alice",
    )


@pytest.mark.asyncio
async def test_connector_import_rejects_parent_target(
    connector_config,
    ctx,
    service,
):
    with pytest.raises(InvalidArgumentError, match="parent targets"):
        await service.add_resource(
            path="tos://bucket/a/b/c",
            ctx=ctx,
            parent="viking://resources/x/y",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_kwargs",
    [{}, {"create_parent": False}],
    ids=["omitted", "explicit_false"],
)
async def test_connector_requires_existing_parent_unless_create_parent(
    connector_config,
    ctx,
    request_kwargs,
):
    viking_fs = SimpleNamespace(exists=AsyncMock(return_value=False))
    service = ResourceService(
        vikingdb=object(),
        viking_fs=viking_fs,
        resource_processor=object(),
        skill_processor=object(),
    )

    with pytest.raises(InvalidArgumentError, match="does not exist"):
        await service.add_resource(
            path="tos://bucket/a/b/c",
            ctx=ctx,
            to="viking://resources/x/y",
            **request_kwargs,
        )

    viking_fs.exists.assert_awaited_once_with("viking://resources/x", ctx)


@pytest.mark.asyncio
async def test_connector_create_parent_false_accepts_existing_parent(
    monkeypatch,
    connector_config,
    ctx,
):
    viking_fs = SimpleNamespace(exists=AsyncMock(return_value=True))
    service = ResourceService(
        vikingdb=object(),
        viking_fs=viking_fs,
        resource_processor=object(),
        skill_processor=object(),
    )
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    result = await service.add_resource(
        path="tos://bucket/a/b/c",
        ctx=ctx,
        to="viking://resources/x/y",
        create_parent=False,
    )

    assert result["status"] == "accepted"
    connector_client.submit_doc_add.assert_awaited_once()


def test_connector_only_route_rejects_disabled_or_unsupported_requests(
    connector_config,
    service,
):
    assert service._connector.should_delegate("https://example.com/doc") is False

    with pytest.raises(InvalidArgumentError, match="args keys"):
        service._connector.should_delegate("tos://bucket/prefix", connector_args={"parser": "pdf"})

    with pytest.raises(InvalidArgumentError, match="parse_mode=no_split"):
        service._connector.should_delegate(
            "tos://bucket/prefix",
            parse_mode=ParseMode.NO_SPLIT,
        )

    connector_config.enable = False
    with pytest.raises(InvalidArgumentError, match="Connector integration"):
        service._connector.should_delegate("tos://bucket/prefix")


def test_git_route_degrades_when_disabled_or_type_not_allowed(connector_config, service):
    # "git" not in allowed_add_types: standard pipeline handles the repo.
    assert service._connector.should_delegate("https://git.example/org/repo.git") is False

    connector_config.allowed_add_types = ["tos", "git"]
    connector_config.enable = False
    assert service._connector.should_delegate("https://git.example/org/repo.git") is False


@pytest.mark.parametrize(
    "path",
    [
        "git@git.example.com:repo.git",
        "git@git.example.com:repo",
        "ssh://git@git.example.com/repo.git",
        "ssh://git@git.example.com/repo",
        "git://git.example.com/repo.git",
        "git://git.example.com/repo",
        "git@git.example.com:org/subgroup/repo",
        "ssh://git@git.example.com/org/subgroup/repo",
        "git://git.example.com/org/subgroup/repo",
    ],
)
def test_git_connector_detection_accepts_explicit_protocol_repo(monkeypatch, path):
    from openviking.connector.routing import detect_connector_add_type
    from openviking.utils import code_hosting_utils

    config = SimpleNamespace(
        code=SimpleNamespace(
            github_domains=[],
            gitlab_domains=[],
            azure_devops_domains=[],
            code_hosting_domains=["git.example.com"],
        )
    )
    monkeypatch.setattr(code_hosting_utils, "get_openviking_config", lambda: config)

    assert detect_connector_add_type(path) == ("git", False)


def test_shared_connector_source_falls_back_for_no_split(connector_config, service):
    connector_config.allowed_add_types = ["https"]

    assert (
        service._connector.should_delegate(
            "https://example.com/manual.pdf",
            parse_mode=ParseMode.NO_SPLIT,
        )
        is False
    )


@pytest.mark.parametrize(
    "path",
    [
        "https://gitcode.com/GitHub_Trending/no/nocobase/blob/bcfdc2/examples/base/config.git",
        "https://gitcode.com/ploc-org/CNPL/tree/master/projects/sample/config.git",
    ],
)
def test_git_connector_detection_rejects_gitcode_browse_file(monkeypatch, path):
    from openviking.connector.routing import detect_connector_add_type
    from openviking.utils import code_hosting_utils

    config = SimpleNamespace(
        code=SimpleNamespace(
            github_domains=[],
            gitlab_domains=[],
            azure_devops_domains=[],
            code_hosting_domains=["gitcode.com"],
        )
    )
    monkeypatch.setattr(code_hosting_utils, "get_openviking_config", lambda: config)
    assert detect_connector_add_type(path) is None


def test_git_source_falls_back_for_parent_target(
    connector_config,
    service,
):
    connector_config.allowed_add_types = ["tos", "git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            parent="viking://resources/manuals",
        )
        is False
    )


def test_git_route_accepts_explicit_create_parent_false(connector_config, service):
    connector_config.allowed_add_types = ["tos", "git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            to="viking://resources/repo",
            kwargs={"create_parent": False},
        )
        is True
    )


def test_git_source_falls_back_for_unsupported_args(connector_config, service):
    connector_config.allowed_add_types = ["tos", "git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            connector_args={"depth": "1"},
        )
        is False
    )


def test_git_route_accepts_credential_args(connector_config, service):
    connector_config.allowed_add_types = ["tos", "git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            to="viking://resources/repo",
            connector_args={"token": "ghp-secret", "username": "oauth2"},
        )
        is True
    )


@pytest.mark.parametrize(
    "routing_kwargs",
    [
        {"to": "viking://resources/repo", "kwargs": {"include": "docs/**"}},
        {"to": "viking://resources/repo", "wait": True},
        {},
        {"parent": "viking://resources/imports"},
    ],
    ids=["include", "wait", "missing_to", "parent"],
)
def test_git_credentials_fail_closed_when_connector_request_would_fallback(
    connector_config,
    service,
    routing_kwargs,
):
    connector_config.allowed_add_types = ["tos", "git"]

    with pytest.raises(InvalidArgumentError, match="cannot fall back") as exc_info:
        service._connector.should_delegate(
            "https://git.example/org/private.git",
            connector_args={"token": "ghp-secret", "username": "oauth2"},
            **routing_kwargs,
        )

    assert "ghp-secret" not in str(exc_info.value)


def test_git_credentials_require_enabled_allowed_connector(connector_config, service):
    with pytest.raises(InvalidArgumentError, match="require Connector import") as exc_info:
        service._connector.should_delegate(
            "https://git.example/org/private.git",
            to="viking://resources/private",
            connector_args={"token": "ghp-secret"},
        )

    assert "ghp-secret" not in str(exc_info.value)


def test_tos_route_rejects_credential_args(connector_config, service):
    with pytest.raises(InvalidArgumentError, match="args keys"):
        service._connector.should_delegate(
            "tos://bucket/prefix",
            connector_args={"token": "ghp-secret"},
        )


@pytest.mark.asyncio
async def test_git_credential_args_travel_in_auth_config_only(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    connector_config.allowed_add_types = ["tos", "git"]
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    await service.add_resource(
        path="https://git.example/org/private.git",
        ctx=ctx,
        to="viking://resources/private",
        args={"branch": "main", "token": "ghp-secret", "username": "oauth2"},
    )

    submitted = connector_client.submit_doc_add.await_args.kwargs
    assert submitted["auth_config"] == {"token": "ghp-secret", "username": "oauth2"}
    assert submitted["param_config"] == {
        "repo_url": "https://git.example/org/private.git",
        "branch": "main",
    }


@pytest.mark.asyncio
async def test_git_credentials_with_include_never_enqueue_native_job(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    connector_config.allowed_add_types = ["tos", "git"]
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.submit = AsyncMock()
    service._enqueue_add_resource_job = AsyncMock()

    with pytest.raises(InvalidArgumentError, match="cannot fall back") as exc_info:
        await service.add_resource(
            path="https://git.example/org/private.git",
            ctx=ctx,
            to="viking://resources/private",
            wait=False,
            include="docs/**",
            args={"token": "ghp-secret", "username": "oauth2"},
        )

    assert "ghp-secret" not in str(exc_info.value)
    service._connector.submit.assert_not_awaited()
    service._enqueue_add_resource_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credential_args",
    [
        {"token": "ghp-secret"},
        {"username": "oauth2"},
    ],
    ids=["token", "username"],
)
async def test_native_git_enqueue_rejects_credentials_before_durable_job(
    monkeypatch,
    ctx,
    service,
    credential_args,
):
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.should_delegate = Mock(return_value=False)
    service._enqueue_add_resource_job = AsyncMock()

    with pytest.raises(InvalidArgumentError, match="must use args.auth_config") as exc_info:
        await service.add_resource(
            path="https://git.example/org/private.git",
            ctx=ctx,
            to="viking://resources/private",
            args=credential_args,
        )

    assert all(value not in str(exc_info.value) for value in credential_args.values())
    service._enqueue_add_resource_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_git_nested_auth_is_owned_by_durable_task(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.should_delegate = Mock(return_value=False)
    service._preflight_git_source = AsyncMock(
        return_value=SimpleNamespace(
            source_name="private",
            source_path="https://git.example/org/private.git",
            source_format="repository",
        )
    )
    service._plan_source_job_target = AsyncMock(
        return_value=("viking://resources/private", None, False)
    )
    service._execute_resource_ingestion = AsyncMock(
        side_effect=AssertionError("Git source work must run in the queue worker")
    )
    service._enqueue_add_resource_job = AsyncMock(
        return_value=SimpleNamespace(task_id="task-private")
    )

    result = await service.add_resource(
        path="https://git.example/org/private.git",
        ctx=ctx,
        to="viking://resources/private",
        args={
            "branch": "main",
            "auth_config": {"username": "oauth2", "token": "secret-token"},
        },
    )

    assert result["task_id"] == "task-private"
    service._execute_resource_ingestion.assert_not_awaited()
    message = service._enqueue_add_resource_job.await_args.args[0]
    assert message.args == {"branch": "main"}
    assert "secret-token" not in json.dumps(message.to_dict())
    assert service._enqueue_add_resource_job.await_args.kwargs["task_auth"] == {
        "provider": "git_http_basic",
        "username": "oauth2",
        "token": "secret-token",
        "repo_url": "https://git.example/org/private.git",
    }


@pytest.mark.asyncio
async def test_native_git_nested_auth_watch_is_copied_from_task_state_by_worker(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.should_delegate = Mock(return_value=False)
    service._preflight_git_source = AsyncMock(
        return_value=SimpleNamespace(
            source_name="private",
            source_path="https://git.example/org/private.git",
            source_format="repository",
        )
    )
    service._plan_source_job_target = AsyncMock(
        return_value=("viking://resources/private", None, False)
    )
    service._enqueue_add_resource_job = AsyncMock(
        return_value=SimpleNamespace(task_id="task-private")
    )

    result = await service.add_resource(
        path="https://git.example/org/private.git",
        ctx=ctx,
        to="viking://resources/private",
        watch_interval=5,
        args={"auth_config": {"token": "secret-token"}},
    )

    assert result["task_id"] == "task-private"
    message = service._enqueue_add_resource_job.await_args.args[0]
    assert message.watch_interval == 5
    assert "secret-token" not in json.dumps(message.to_dict())
    assert service._enqueue_add_resource_job.await_args.kwargs["task_auth"] == {
        "provider": "git_http_basic",
        "username": "oauth2",
        "token": "secret-token",
        "repo_url": "https://git.example/org/private.git",
    }


@pytest.mark.asyncio
async def test_native_git_watch_refresh_queues_with_restored_task_auth(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.should_delegate = Mock(return_value=False)
    service._preflight_git_source = AsyncMock(
        return_value=SimpleNamespace(
            source_name="private",
            source_path="https://git.example/org/private.git",
            source_format="repository",
        )
    )
    service._plan_source_job_target = AsyncMock(
        return_value=("viking://resources/private", None, False)
    )
    service._enqueue_add_resource_job = AsyncMock(
        return_value=SimpleNamespace(task_id="task-refresh")
    )

    result = await service.refresh_resource(
        path="https://git.example/org/private.git",
        ctx=ctx,
        to="viking://resources/private",
        watch_interval=5,
        auth_config={"username": "oauth2", "token": "secret-token"},
    )

    assert result["task_id"] == "task-refresh"
    message = service._enqueue_add_resource_job.await_args.args[0]
    assert message.skip_watch_management is True
    assert "secret-token" not in json.dumps(message.to_dict())
    assert service._enqueue_add_resource_job.await_args.kwargs["task_auth"] == {
        "provider": "git_http_basic",
        "username": "oauth2",
        "token": "secret-token",
        "repo_url": "https://git.example/org/private.git",
    }


@pytest.mark.asyncio
async def test_native_git_rejects_malformed_nested_auth_without_secret_in_error(
    monkeypatch, ctx, service
):
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.should_delegate = Mock(return_value=False)
    service._enqueue_add_resource_job = AsyncMock()

    with pytest.raises(InvalidArgumentError, match="unsupported fields") as exc_info:
        await service.add_resource(
            path="https://git.example/org/private.git",
            ctx=ctx,
            to="viking://resources/private",
            args={
                "auth_config": {
                    "username": "oauth2",
                    "token": "secret-token",
                    "password": "another-secret",
                }
            },
        )

    assert "secret-token" not in str(exc_info.value)
    service._enqueue_add_resource_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_git_url_embedded_credentials_are_rejected_before_enqueue(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._enqueue_add_resource_job = AsyncMock()
    source = "https://user:embedded-token@git.example/org/private.git"
    with pytest.raises(InvalidArgumentError, match="cannot contain userinfo") as exc_info:
        await service.add_resource(
            path=source,
            ctx=ctx,
            to="viking://resources/private",
        )

    assert "embedded-token" not in str(exc_info.value)
    service._enqueue_add_resource_job.assert_not_awaited()


def test_prepared_add_resource_payload_drops_nested_git_auth():
    msg = AddResourceMsg(
        task_id="task-private",
        path="https://git.example/org/private.git",
        root_uri="viking://resources/private",
        account_id="acct",
        user_id="alice",
        role="user",
        prepared={"root_uri": "viking://resources/private"},
        args={"auth_config": {"username": "oauth2", "token": "secret-token"}},
    )

    payload = msg.to_dict()

    assert payload["args"] == {}
    assert payload["job_phase"] == AddResourcePhase.POST_PROCESS
    assert "secret-token" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_postprocess_queue_message_drops_git_auth(ctx, service):
    service._resource_processor = SimpleNamespace(
        process_resource=AsyncMock(
            return_value={
                "status": "success",
                "root_uri": "viking://resources/private",
                "source_path": "https://git.example/org/private.git",
                "_post_process": {"root_is_file": False},
                "_resource_lock": None,
            }
        )
    )
    service._enqueue_add_resource_job = AsyncMock(
        return_value=SimpleNamespace(task_id="task-private")
    )

    result = await service._execute_resource_ingestion(
        path="https://git.example/org/private.git",
        ctx=ctx,
        to="viking://resources/private",
        defer_post_processing=True,
        auth_config={"username": "oauth2", "token": "secret-token"},
    )

    assert result["task_id"] == "task-private"
    queued_msg = service._enqueue_add_resource_job.await_args.args[0]
    payload = queued_msg.to_dict()
    assert payload["args"] == {}
    assert "secret-token" not in json.dumps(payload)


def test_connector_route_accepts_public_exact_to(connector_config, ctx, service):
    connector_config.allowed_add_types = ["tos", "git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            ctx=ctx,
            to="viking://resources/manuals",
        )
        is True
    )


@pytest.mark.asyncio
async def test_add_resource_falls_back_for_shared_source_with_parent(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    connector_config.allowed_add_types = ["tos", "git"]
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.submit = AsyncMock()
    service._preflight_git_source = AsyncMock(
        return_value=SimpleNamespace(
            source_name="repo",
            source_path="https://git.example/org/repo.git",
            source_format="repository",
        )
    )
    service._plan_source_job_target = AsyncMock(
        return_value=("viking://resources/repo/repo", None, False)
    )
    service._enqueue_add_resource_job = AsyncMock(
        return_value=SimpleNamespace(task_id="task-standard")
    )

    result = await service.add_resource(
        path="https://git.example/org/repo.git",
        ctx=ctx,
        parent="viking://resources/repo",
    )

    assert result == {
        "status": "success",
        "task_id": "task-standard",
        "root_uri": "viking://resources/repo/repo",
    }
    service._connector.submit.assert_not_awaited()
    service._enqueue_add_resource_job.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manage_watch", False),
        ("parser_args", {}),
        ("resource_lock", object()),
        ("route_source", False),
        ("skip_watch_management", True),
        ("stage_callback", object()),
        ("watch_auth_state", {}),
        ("understanding_response_id", "response-1"),
        ("parser_backend", "understanding"),
        ("resolved_extension", ".pdf"),
    ],
)
async def test_add_resource_rejects_internal_execution_fields(ctx, service, field, value):
    with pytest.raises(InvalidArgumentError, match=field):
        await service.add_resource(
            path="https://example.com/manual.pdf",
            ctx=ctx,
            **{field: value},
        )


@pytest.mark.asyncio
async def test_add_resource_job_executes_frozen_route(ctx, service):
    service._execute_resource_ingestion = AsyncMock(
        return_value={"status": "success", "root_uri": "root"}
    )
    resource_lock = SimpleNamespace(close=AsyncMock())
    stage_callback = AsyncMock()
    msg = AddResourceMsg(
        task_id="task-1",
        path="git://example.com/repo.git",
        root_uri="viking://resources/repo",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        role=str(ctx.role),
        args={"parser_backend": "understanding"},
    )

    result = await service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=resource_lock,
        stage_callback=stage_callback,
    )

    assert result == {"status": "success", "root_uri": "root"}
    call = service._execute_resource_ingestion.await_args.kwargs
    assert call["path"] == msg.path
    assert call["to"] == msg.root_uri
    assert call["defer_post_processing"] is False
    assert call["resource_lock"] is resource_lock
    assert call["parser_backend"] == "understanding"


@pytest.mark.asyncio
async def test_shared_source_create_parent_false_routes_to_connector(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    connector_config.allowed_add_types = ["tos", "git"]
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.submit = AsyncMock(return_value={"status": "accepted"})
    service._enqueue_add_resource_job = AsyncMock()

    result = await service.add_resource(
        path="https://git.example/org/repo.git",
        ctx=ctx,
        to="viking://resources/repo",
        create_parent=False,
    )

    assert result == {"status": "accepted"}
    service._enqueue_add_resource_job.assert_not_awaited()
    service._connector.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_connector_import_without_target_is_rejected(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"TaskKey": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    with pytest.raises(InvalidArgumentError, match="exact 'to' target"):
        await service._connector.submit(
            path="tos://bucket/prefix",
            ctx=ctx,
            to=None,
        )

    tracker.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_connector_import_rejects_target_outside_public_resources_root(
    connector_config,
    ctx,
    service,
):
    with pytest.raises(InvalidArgumentError, match="public resources root"):
        await service.add_resource(
            path="tos://bucket/prefix",
            ctx=ctx,
            to="viking://user/alice/resources/spec",
        )


@pytest.mark.asyncio
async def test_connector_import_rejects_unsupported_args(
    connector_config,
    ctx,
    service,
):
    with pytest.raises(InvalidArgumentError, match="args keys"):
        await service.add_resource(
            path="tos://bucket/prefix",
            ctx=ctx,
            to="viking://resources/spec",
            args={"parser": "pdf"},
        )


def test_git_source_falls_back_for_wait(connector_config, service):
    connector_config.allowed_add_types = ["tos", "git"]

    assert (
        service._connector.should_delegate(
            "https://git.example/org/repo.git",
            to="viking://resources/repo",
            wait=True,
        )
        is False
    )


@pytest.mark.asyncio
async def test_tos_connector_rejects_wait(connector_config, ctx, service):
    with pytest.raises(InvalidArgumentError, match="wait=true"):
        await service.add_resource(
            path="tos://bucket/prefix",
            ctx=ctx,
            to="viking://resources/imports",
            wait=True,
        )


@pytest.mark.asyncio
async def test_tos_connector_forwards_tags_and_mode(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    await service.add_resource(
        path="tos://bucket/prefix",
        ctx=ctx,
        to="viking://resources/imports",
        tags=[" Team=Search ", "env=test", "team=search"],
        tag_mode="append",
    )

    submitted = connector_client.submit_doc_add.await_args.kwargs
    assert submitted["extra_params"] == {
        "tags": ["team=search", "env=test"],
        "tag_mode": "append",
    }


@pytest.mark.asyncio
async def test_monitor_links_reason_memory_on_success(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    tracker = _task_tracker()
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )
    client = SimpleNamespace(get_task_info=AsyncMock(return_value={"Status": "succeeded"}))
    service._link_resource_reason_memory = AsyncMock()

    outcome = await service._connector._monitor(
        client=client,
        connector_task_key="connector-1",
        ov_task_id="task-1",
        poll_interval_ms=10,
        timeout_seconds=5,
        ctx=ctx,
        reason="track quarterly reports",
        link_root_uri="viking://resources/imports",
    )

    assert outcome["status"] == "completed"
    tracker.complete.assert_awaited_once()
    link_kwargs = service._link_resource_reason_memory.await_args.kwargs
    assert link_kwargs["reason"] == "track quarterly reports"
    assert link_kwargs["result"] == {"root_uri": "viking://resources/imports"}


@pytest.mark.asyncio
async def test_git_reason_routes_to_connector(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    connector_config.allowed_add_types = ["tos", "git"]
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    service._connector.submit = AsyncMock(return_value={"status": "accepted"})
    service._enqueue_add_resource_job = AsyncMock()

    result = await service.add_resource(
        path="https://git.example/org/repo.git",
        ctx=ctx,
        to="viking://resources/imports",
        reason="track quarterly reports",
    )

    assert result == {"status": "accepted"}
    service._enqueue_add_resource_job.assert_not_awaited()
    connector_kwargs = service._connector.submit.await_args.kwargs
    assert connector_kwargs["reason"] == "track quarterly reports"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_info", "expected_stage", "expected_error"),
    [
        ({"Status": "succeeded"}, "connector:succeeded", None),
        (
            {"status": "failed", "error_message": "source unavailable"},
            "connector:failed",
            "connector task failed: source unavailable",
        ),
    ],
)
async def test_monitor_connector_task_maps_terminal_status(
    monkeypatch,
    connector_config,
    ctx,
    task_info,
    expected_stage,
    expected_error,
):
    tracker = _task_tracker()
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(connector_delegate_module.asyncio, "sleep", no_sleep)
    client = SimpleNamespace(get_task_info=AsyncMock(return_value=task_info))

    outcome = await ResourceService()._connector._monitor(
        client=client,
        connector_task_key="connector-1",
        ov_task_id="task-1",
        poll_interval_ms=1,
        timeout_seconds=1,
        ctx=ctx,
    )

    assert tracker.update_stage.await_args.args[1] == expected_stage
    if expected_error is None:
        assert outcome["status"] == "completed"
        tracker.complete.assert_awaited_once()
        tracker.fail.assert_not_awaited()
    else:
        assert outcome == {"status": "failed", "error": expected_error}
        assert tracker.fail.await_args.args[1] == expected_error
        tracker.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_connector_task_retries_transient_polling_error(
    monkeypatch,
    connector_config,
    ctx,
):
    tracker = _task_tracker()
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(connector_delegate_module.asyncio, "sleep", no_sleep)
    client = SimpleNamespace(
        get_task_info=AsyncMock(
            side_effect=[
                httpx.ReadTimeout("temporary timeout"),
                {"Status": "succeeded"},
            ]
        )
    )

    await ResourceService()._connector._monitor(
        client=client,
        connector_task_key="connector-1",
        ov_task_id="task-1",
        poll_interval_ms=1,
        timeout_seconds=1,
        ctx=ctx,
    )

    assert client.get_task_info.await_count == 2
    tracker.complete.assert_awaited_once()
    tracker.fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_connector_task_marks_cancelled_monitor_as_failed(
    monkeypatch,
    connector_config,
    ctx,
):
    tracker = _task_tracker()
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )

    async def cancelled_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(connector_delegate_module.asyncio, "sleep", cancelled_sleep)
    client = SimpleNamespace(get_task_info=AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await ResourceService()._connector._monitor(
            client=client,
            connector_task_key="connector-1",
            ov_task_id="task-1",
            poll_interval_ms=1,
            timeout_seconds=1,
            ctx=ctx,
        )

    tracker.fail.assert_awaited_once_with(
        "task-1",
        "background connector task monitoring cancelled",
        account_id="acct",
        user_id="alice",
    )
    tracker.complete.assert_not_awaited()


# --- Declared add_type routing -------------------------------------------------


@pytest.mark.asyncio
async def test_declared_add_type_routes_generic_type_to_connector(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    connector_config.allowed_add_types = ["feishu"]
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    result = await service.add_resource(
        path="https://example.feishu.cn/wiki/space-home",
        ctx=ctx,
        add_type="feishu",
        to="viking://resources/kb/feishu",
        args={"space_id": "spc1", "auth_config": {"app_secret": "s3cret"}},
    )

    assert result["status"] == "accepted"
    connector_client.submit_doc_add.assert_awaited_once_with(
        add_type="feishu",
        api_key="secret",
        tos_path=None,
        to="viking://resources/kb/feishu",
        include_child=True,
        param_config={
            "space_id": "spc1",
            "path": "https://example.feishu.cn/wiki/space-home",
        },
        auth_config={"app_secret": "s3cret"},
        extra_params=None,
    )


@pytest.mark.asyncio
async def test_declared_registry_type_routes_like_probed(
    monkeypatch,
    connector_config,
    ctx,
    service,
):
    tracker = _task_tracker()
    connector_client = SimpleNamespace(
        submit_doc_add=AsyncMock(return_value={"task_key": "connector-1"})
    )
    _install_connector_dependencies(monkeypatch, tracker, connector_client)

    await service.add_resource(
        path="tos://bucket/a/b",
        ctx=ctx,
        add_type="tos",
        to="viking://resources/x",
    )

    connector_client.submit_doc_add.assert_awaited_once_with(
        add_type="tos",
        api_key="secret",
        tos_path="bucket/a/b",
        to="viking://resources/x",
        include_child=True,
        param_config=None,
        auth_config=None,
        extra_params=None,
    )


def test_declared_add_type_requires_enabled_and_allowed(connector_config, ctx, service):
    # Not in allowed_add_types (fixture allows only "tos").
    with pytest.raises(InvalidArgumentError, match="disabled or does not allow"):
        service._connector.should_delegate(
            "https://example.feishu.cn/wiki/x",
            ctx=ctx,
            declared_add_type="feishu",
            to="viking://resources/x",
        )
    # Connector disabled entirely.
    connector_config.enable = False
    connector_config.allowed_add_types = ["feishu"]
    with pytest.raises(InvalidArgumentError, match="disabled or does not allow"):
        service._connector.should_delegate(
            "https://example.feishu.cn/wiki/x",
            ctx=ctx,
            declared_add_type="feishu",
            to="viking://resources/x",
        )


def test_declared_add_type_rejects_probe_mismatch(connector_config, ctx, service):
    connector_config.allowed_add_types = ["tos", "git", "feishu"]
    # Generic declared type claiming a path that probes as a registry type.
    with pytest.raises(InvalidArgumentError, match="does not match the source path"):
        service._connector.should_delegate(
            "tos://bucket/x",
            ctx=ctx,
            declared_add_type="feishu",
            to="viking://resources/x",
        )
    # Registry declared type on a path that probes differently (or not at all).
    with pytest.raises(InvalidArgumentError, match="does not match the source path"):
        service._connector.should_delegate(
            "https://not-a-repo.example/page",
            ctx=ctx,
            declared_add_type="git",
            to="viking://resources/x",
        )
    with pytest.raises(InvalidArgumentError, match="does not match the source path"):
        service._connector.should_delegate(
            _GIT_REPO_PREFIX + "org/repo",
            ctx=ctx,
            declared_add_type="tos",
            to="viking://resources/x",
        )


def test_declared_add_type_never_degrades(connector_config, ctx, service):
    connector_config.allowed_add_types = ["tos", "git"]
    # Probed git with wait=true degrades; declared git must raise instead.
    with pytest.raises(InvalidArgumentError, match="wait=true"):
        service._connector.should_delegate(
            _GIT_REPO_PREFIX + "org/repo",
            ctx=ctx,
            declared_add_type="git",
            to="viking://resources/x",
            wait=True,
        )
    # Same for an unsupported top-level param on a generic declared type.
    connector_config.allowed_add_types = ["feishu"]
    with pytest.raises(InvalidArgumentError, match="instruction"):
        service._connector.should_delegate(
            "https://example.feishu.cn/wiki/x",
            ctx=ctx,
            declared_add_type="feishu",
            to="viking://resources/x",
            instruction="summarize",
        )


def test_declared_git_rejects_abbreviated_commit(connector_config, ctx, service):
    connector_config.allowed_add_types = ["git"]
    with pytest.raises(InvalidArgumentError, match="abbreviated commit SHA"):
        service._connector.should_delegate(
            _GIT_REPO_PREFIX + "org/repo",
            ctx=ctx,
            declared_add_type="git",
            to="viking://resources/x",
            connector_args={"commit": "abc1234"},
        )


def test_declared_add_type_rejects_non_mapping_auth_config(connector_config, ctx, service):
    connector_config.allowed_add_types = ["feishu"]
    with pytest.raises(InvalidArgumentError, match="auth_config"):
        service._connector.should_delegate(
            "https://example.feishu.cn/wiki/x",
            ctx=ctx,
            declared_add_type="feishu",
            to="viking://resources/x",
            connector_args={"auth_config": "not-a-dict"},
        )


def test_declared_git_keeps_args_whitelist(connector_config, ctx, service):
    # Registry types keep their per-type args whitelist even when declared;
    # unknown keys raise instead of flowing into param_config.
    connector_config.allowed_add_types = ["git"]
    with pytest.raises(InvalidArgumentError, match="args keys"):
        service._connector.should_delegate(
            _GIT_REPO_PREFIX + "org/repo",
            ctx=ctx,
            declared_add_type="git",
            to="viking://resources/x",
            connector_args={"depth": 1},
        )
