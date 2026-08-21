# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for server-side OpenViking Assets configuration resolution."""

import asyncio
import hashlib
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio

from openviking.server.identity import RequestContext, Role
from openviking.server.openviking_assets import (
    normalize_repo_url,
    preflight_git_repository,
    resolve_openviking_assets,
)
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier

FULL_COMMIT_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

CATALOG = """\
protocol: openviking-assets/1

defaults:
  git:
    watch_interval: 30

catalog:
  - name: alpha
    connector: git
    description: alpha repo
    to: viking://resources/repos/alpha
    params:
      repo_url: https://github.com/org/alpha
      branch: main

  - name: beta
    connector: git
    params:
      repo_url: git@github.com:org/beta.git
    watch_interval: 0
"""

MANIFEST = """\
assets:
  - alpha
  - beta
"""

SINGLE_FILE_MANIFEST = """\
protocol: openviking-assets/1

defaults:
  git:
    watch_interval: 30

catalog:
  - name: alpha
    connector: git
    description: alpha repo
    to: viking://resources/repos/alpha
    params:
      repo_url: https://github.com/org/alpha
      branch: main

  - name: beta
    connector: git
    params:
      repo_url: git@github.com:org/beta.git
    watch_interval: 0
"""


def _request(**overrides):
    body = {
        "manifest_yaml": MANIFEST,
        "catalog_yaml": CATALOG,
        "manifest_label": "manifests/code-qa.yaml",
        "catalog_label": "catalog.yaml",
    }
    body.update(overrides)
    return body


@pytest_asyncio.fixture
async def assets_client():
    """Client for resolver endpoints, which do not require a storage service."""
    from types import SimpleNamespace

    from openviking.server.app import create_app
    from openviking.server.auth import get_request_context
    from openviking.server.config import ServerConfig

    app = create_app(config=ServerConfig(), service=SimpleNamespace(sessions=None))
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"),
        role=Role.USER,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def test_resolver_normalizes_and_resolves_defaults():
    result = resolve_openviking_assets(
        manifest_yaml=MANIFEST,
        catalog_yaml=CATALOG,
        manifest_label="manifests/code-qa.yaml",
        catalog_label="catalog.yaml",
    )

    assert [asset.name for asset in result.assets] == ["alpha", "beta"]
    assert result.assets[0].to == "viking://resources/repos/alpha"
    assert result.assets[1].to is None
    assert result.assets[0].watch_interval == 30
    assert result.assets[1].watch_interval == 0
    assert result.assets[1].locator == "github.com/org/beta"
    identity = b"git\ngithub.com/org/alpha\nmain"
    assert result.assets[0].asset_id == hashlib.sha1(identity).hexdigest()[:12]


def test_resolver_accepts_single_file_manifest():
    result = resolve_openviking_assets(
        manifest_yaml=SINGLE_FILE_MANIFEST,
        manifest_label="single-file.yaml",
    )

    assert [asset.name for asset in result.assets] == ["alpha", "beta"]
    assert result.manifest == "single-file.yaml"
    assert result.catalog == "single-file.yaml"
    assert result.assets[0].watch_interval == 30
    assert result.assets[1].watch_interval == 0


def test_resolver_accepts_and_normalizes_full_commit_sha():
    manifest = f"""\
protocol: openviking-assets/1
catalog:
  - name: pinned
    connector: git
    params:
      repo_url: https://github.com/org/pinned
      commit: {FULL_COMMIT_SHA.upper()}
"""

    result = resolve_openviking_assets(manifest_yaml=manifest)

    asset = result.assets[0]
    assert asset.branch is None
    assert asset.commit == FULL_COMMIT_SHA
    assert asset.git_ref == FULL_COMMIT_SHA
    identity = f"git\ngithub.com/org/pinned\n{FULL_COMMIT_SHA}".encode()
    assert asset.asset_id == hashlib.sha1(identity).hexdigest()[:12]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            f"branch: main\n      commit: {FULL_COMMIT_SHA}",
            "branch and params.commit are mutually exclusive",
        ),
        ("commit: deadbee", "full 40-character hexadecimal SHA"),
        ("commit: '   '", "commit must be a non-empty string"),
    ],
)
def test_resolver_rejects_invalid_commit_selection(params: str, message: str):
    manifest = f"""\
protocol: openviking-assets/1
catalog:
  - name: pinned
    connector: git
    params:
      repo_url: https://github.com/org/pinned
      {params}
"""

    with pytest.raises(InvalidArgumentError, match=message):
        resolve_openviking_assets(manifest_yaml=manifest)


def test_single_file_manifest_selects_subset():
    result = resolve_openviking_assets(
        manifest_yaml=SINGLE_FILE_MANIFEST + "\nassets:\n  - beta\n",
    )

    assert [asset.name for asset in result.assets] == ["beta"]


@pytest.mark.parametrize(
    ("to", "message"),
    [
        ("'   '", "must be a non-empty string"),
        ("viking://user/skills/repo", "must target resource content"),
        ("https://example.com/repo", "unsupported URI scheme"),
    ],
)
def test_resolver_rejects_invalid_asset_target(to: str, message: str):
    manifest = f"""\
protocol: openviking-assets/1
catalog:
  - name: targeted
    connector: git
    to: {to}
    params:
      repo_url: https://github.com/org/targeted
"""

    with pytest.raises(InvalidArgumentError, match=message):
        resolve_openviking_assets(manifest_yaml=manifest)


def test_resolver_rejects_duplicate_selected_targets():
    manifest = """\
protocol: openviking-assets/1
catalog:
  - name: alpha
    connector: git
    to: viking://resources/repos/shared
    params:
      repo_url: https://github.com/org/alpha
  - name: beta
    connector: git
    to: viking://resources/repos/shared/
    params:
      repo_url: https://github.com/org/beta
"""

    with pytest.raises(InvalidArgumentError, match="same target URI"):
        resolve_openviking_assets(manifest_yaml=manifest)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:volcengine/OpenViking.git", "github.com/volcengine/OpenViking"),
        ("https://User@GitHub.com/Org/Repo.git", "github.com/Org/Repo"),
        ("ssh://git@host.com:29418/t/repo", "host.com/t/repo"),
    ],
)
def test_normalize_repo_url_forms(url: str, expected: str):
    assert normalize_repo_url(url) == expected


@pytest.mark.parametrize(
    ("manifest", "catalog", "message"),
    [
        ("include:\n  - base.yaml\nassets:\n  - alpha\n", CATALOG, "flat manifest"),
        ("asets:\n  - alpha\n", CATALOG, "Extra inputs are not permitted"),
        ("assets:\n  - missing\n", CATALOG, "not in catalog"),
        (
            "assets:\n  - alpha\n",
            CATALOG.replace("connector: git", "connector: rss", 1),
            "not supported",
        ),
        (
            "assets:\n  - alpha\n",
            CATALOG.replace("https://github.com/org/alpha", "ext::sh -c whoami"),
            "remote-helper",
        ),
        (
            "assets:\n  - alpha\n  - beta\n",
            CATALOG.replace("git@github.com:org/beta.git", "https://github.com/org/alpha").replace(
                "    watch_interval: 0", "      branch: main\n    watch_interval: 0"
            ),
            "same source",
        ),
        ("catalog: ../catalog.yaml\nassets:\n  - alpha\n", CATALOG, "not a file path"),
        (SINGLE_FILE_MANIFEST, CATALOG, "do not pass a separate catalog"),
        (
            SINGLE_FILE_MANIFEST.replace("protocol: openviking-assets/1\n\n", ""),
            None,
            "'protocol' is required",
        ),
        (SINGLE_FILE_MANIFEST + "\nassets:\n  - missing\n", None, "not defined in its"),
        ("defaults:\n  git:\n    watch_interval: 5\nassets:\n  - alpha\n", CATALOG, "only allowed"),
        (MANIFEST, None, "no catalog was provided"),
    ],
)
def test_resolver_rejects_invalid_configuration(manifest: str, catalog: str | None, message: str):
    with pytest.raises(InvalidArgumentError, match=message):
        resolve_openviking_assets(manifest_yaml=manifest, catalog_yaml=catalog)


class _GitProcess:
    def __init__(self, returncode: int, stderr: bytes = b""):
        self.returncode = returncode
        self.stderr = stderr
        self.killed = False

    async def communicate(self):
        return b"", self.stderr

    def kill(self):
        self.killed = True


async def test_git_preflight_uses_process_local_auth_without_secret_in_argv(monkeypatch):
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return _GitProcess(0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    result = await preflight_git_repository(
        asset_name="private",
        repo_url="https://github.com/org/private",
        branch="main",
        username="oauth2",
        token="secret-token",
    )

    assert result["accessible"] is True
    assert result["git_ref"] == "main"
    assert "secret-token" not in " ".join(captured["args"])
    assert any(
        "b2F1dGgyOnNlY3JldC10b2tlbg==" in value
        for key, value in captured["env"].items()
        if key.startswith("GIT_CONFIG_VALUE_")
    )
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.parametrize("repo_url", ["http://github.com/org/private.git"])
async def test_git_preflight_rejects_unsafe_token_urls_before_spawn(monkeypatch, repo_url):
    exec_mock = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", exec_mock)

    with pytest.raises(InvalidArgumentError):
        await preflight_git_repository(
            asset_name="private",
            repo_url=repo_url,
            token="secret-token",
        )

    exec_mock.assert_not_awaited()


async def test_git_preflight_cancellation_kills_and_reaps_authenticated_process(monkeypatch):
    process = Mock()
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError)
    process.kill = Mock()
    process.wait = AsyncMock()

    async def fake_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(asyncio.CancelledError):
        await preflight_git_repository(
            asset_name="private",
            repo_url="https://github.com/org/private.git",
            token="secret-token",
        )

    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()


async def test_git_preflight_preserves_ssh_username(monkeypatch):
    captured = {}

    async def fake_exec(*args, **_kwargs):
        captured["args"] = args
        return _GitProcess(0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    repo_url = "ssh://git@github.com/org/private.git"

    result = await preflight_git_repository(asset_name="private", repo_url=repo_url)

    assert result["accessible"] is True
    assert repo_url in captured["args"]


async def test_git_preflight_accepts_commit_and_checks_repository_head(monkeypatch):
    captured = {}

    async def fake_exec(*args, **_kwargs):
        captured["args"] = args
        return _GitProcess(0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    result = await preflight_git_repository(
        asset_name="pinned",
        repo_url="https://github.com/org/pinned",
        commit=FULL_COMMIT_SHA.upper(),
    )

    assert captured["args"] == (
        "git",
        "ls-remote",
        "--exit-code",
        "https://github.com/org/pinned",
        "HEAD",
    )
    assert result["git_ref"] == FULL_COMMIT_SHA


async def test_git_preflight_rejects_invalid_commit_selection():
    with pytest.raises(InvalidArgumentError, match="mutually exclusive"):
        await preflight_git_repository(
            asset_name="pinned",
            repo_url="https://github.com/org/pinned",
            branch="main",
            commit=FULL_COMMIT_SHA,
        )

    with pytest.raises(InvalidArgumentError, match="full 40-character hexadecimal SHA"):
        await preflight_git_repository(
            asset_name="pinned",
            repo_url="https://github.com/org/pinned",
            commit="deadbee",
        )


async def test_git_preflight_maps_private_repository_failure_to_permission_denied(monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        return _GitProcess(128, b"remote: Repository not found.")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(PermissionDeniedError, match="verify auth_ref"):
        await preflight_git_repository(
            asset_name="private",
            repo_url="https://github.com/beleev/paper",
        )


async def test_git_preflight_maps_missing_branch_to_not_found(monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        return _GitProcess(2)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(NotFoundError, match="Git_ref not found"):
        await preflight_git_repository(
            asset_name="private",
            repo_url="https://github.com/beleev/paper",
            branch="missing",
        )


async def test_git_preflight_rejects_server_local_paths():
    with pytest.raises(PermissionDeniedError, match="only accepts remote"):
        await preflight_git_repository(
            asset_name="local",
            repo_url="file:///tmp/private.git",
        )


async def test_resolve_endpoint_returns_standard_envelope(assets_client: httpx.AsyncClient):
    response = await assets_client.post("/api/v1/openviking-assets/resolve", json=_request())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [asset["name"] for asset in body["result"]["assets"]] == ["alpha", "beta"]
    assert body["result"]["manifest"] == "manifests/code-qa.yaml"


async def test_resolve_endpoint_accepts_single_file_manifest(assets_client: httpx.AsyncClient):
    response = await assets_client.post(
        "/api/v1/openviking-assets/resolve",
        json={"manifest_yaml": SINGLE_FILE_MANIFEST, "manifest_label": "single-file.yaml"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [asset["name"] for asset in body["result"]["assets"]] == ["alpha", "beta"]
    assert body["result"]["catalog"] == "single-file.yaml"


async def test_resolve_endpoint_canonicalizes_current_user_target(
    assets_client: httpx.AsyncClient,
):
    manifest = """\
protocol: openviking-assets/1
catalog:
  - name: private
    connector: git
    to: viking://user/resources/repos/private
    params:
      repo_url: https://github.com/org/private
"""

    response = await assets_client.post(
        "/api/v1/openviking-assets/resolve",
        json={"manifest_yaml": manifest},
    )

    assert response.status_code == 200
    assert response.json()["result"]["assets"][0]["to"] == (
        "viking://user/alice/resources/repos/private"
    )


async def test_resolve_endpoint_maps_configuration_errors(assets_client: httpx.AsyncClient):
    response = await assets_client.post(
        "/api/v1/openviking-assets/resolve",
        json=_request(manifest_yaml="include:\n  - base.yaml\nassets:\n  - alpha\n"),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "flat manifest" in body["error"]["message"]


async def test_preflight_endpoint_maps_source_permission_errors(
    assets_client: httpx.AsyncClient, monkeypatch
):
    async def deny(**_kwargs):
        raise PermissionDeniedError("repository access denied")

    monkeypatch.setattr(
        "openviking.server.routers.openviking_assets.preflight_git_repository",
        deny,
    )
    response = await assets_client.post(
        "/api/v1/openviking-assets/preflight",
        json={
            "name": "private",
            "connector": "git",
            "repo_url": "https://github.com/beleev/paper",
        },
    )

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "PERMISSION_DENIED"


async def test_preflight_endpoint_forwards_commit(assets_client: httpx.AsyncClient, monkeypatch):
    captured = {}

    async def allow(**kwargs):
        captured.update(kwargs)
        return {"accessible": True, "git_ref": kwargs["commit"]}

    monkeypatch.setattr(
        "openviking.server.routers.openviking_assets.preflight_git_repository",
        allow,
    )
    response = await assets_client.post(
        "/api/v1/openviking-assets/preflight",
        json={
            "name": "pinned",
            "connector": "git",
            "repo_url": "https://github.com/org/pinned",
            "commit": FULL_COMMIT_SHA,
        },
    )

    assert response.status_code == 200
    assert captured["branch"] is None
    assert captured["commit"] == FULL_COMMIT_SHA
