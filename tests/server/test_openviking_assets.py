# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for server-side OpenViking Assets configuration resolution."""

import hashlib

import httpx
import pytest

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

CATALOG = """\
protocol: openviking-assets/1

defaults:
  git:
    watch_interval: 30

catalog:
  - name: alpha
    connector: git
    description: alpha repo
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


def test_resolver_normalizes_and_resolves_defaults():
    result = resolve_openviking_assets(
        manifest_yaml=MANIFEST,
        catalog_yaml=CATALOG,
        manifest_label="manifests/code-qa.yaml",
        catalog_label="catalog.yaml",
    )

    assert [asset.name for asset in result.assets] == ["alpha", "beta"]
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


def test_single_file_manifest_selects_subset():
    result = resolve_openviking_assets(
        manifest_yaml=SINGLE_FILE_MANIFEST + "\nassets:\n  - beta\n",
    )

    assert [asset.name for asset in result.assets] == ["beta"]


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


async def test_resolve_endpoint_returns_standard_envelope(client: httpx.AsyncClient):
    response = await client.post("/api/v1/openviking-assets/resolve", json=_request())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [asset["name"] for asset in body["result"]["assets"]] == ["alpha", "beta"]
    assert body["result"]["manifest"] == "manifests/code-qa.yaml"


async def test_resolve_endpoint_accepts_single_file_manifest(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/openviking-assets/resolve",
        json={"manifest_yaml": SINGLE_FILE_MANIFEST, "manifest_label": "single-file.yaml"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [asset["name"] for asset in body["result"]["assets"]] == ["alpha", "beta"]
    assert body["result"]["catalog"] == "single-file.yaml"


async def test_resolve_endpoint_maps_configuration_errors(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/openviking-assets/resolve",
        json=_request(manifest_yaml="include:\n  - base.yaml\nassets:\n  - alpha\n"),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "flat manifest" in body["error"]["message"]


async def test_preflight_endpoint_maps_source_permission_errors(
    client: httpx.AsyncClient, monkeypatch
):
    async def deny(**_kwargs):
        raise PermissionDeniedError("repository access denied")

    monkeypatch.setattr(
        "openviking.server.routers.openviking_assets.preflight_git_repository",
        deny,
    )
    response = await client.post(
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
