# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for server-side OpenViking Assets configuration resolution."""

import hashlib

import httpx
import pytest

from openviking.server.openviking_assets import normalize_repo_url, resolve_openviking_assets
from openviking_cli.exceptions import InvalidArgumentError

CATALOG = """\
protocol: openviking-assets/1

defaults:
  git:
    watch_interval: 30

assets:
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
catalog: ../assets.yaml
assets:
  - alpha
  - beta
"""


def _request(**overrides):
    body = {
        "manifest_yaml": MANIFEST,
        "catalog_yaml": CATALOG,
        "manifest_label": "manifests/code-qa.yaml",
        "catalog_label": "assets.yaml",
    }
    body.update(overrides)
    return body


def test_resolver_normalizes_and_resolves_defaults():
    result = resolve_openviking_assets(
        manifest_yaml=MANIFEST,
        catalog_yaml=CATALOG,
        manifest_label="manifests/code-qa.yaml",
        catalog_label="assets.yaml",
    )

    assert [asset.name for asset in result.assets] == ["alpha", "beta"]
    assert result.assets[0].watch_interval == 30
    assert result.assets[1].watch_interval == 0
    assert result.assets[1].locator == "github.com/org/beta"
    identity = b"git\ngithub.com/org/alpha\nmain"
    assert result.assets[0].asset_id == hashlib.sha1(identity).hexdigest()[:12]


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
    ],
)
def test_resolver_rejects_invalid_configuration(manifest: str, catalog: str, message: str):
    with pytest.raises(InvalidArgumentError, match=message):
        resolve_openviking_assets(manifest_yaml=manifest, catalog_yaml=catalog)


async def test_resolve_endpoint_returns_standard_envelope(client: httpx.AsyncClient):
    response = await client.post("/api/v1/openviking-assets/resolve", json=_request())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [asset["name"] for asset in body["result"]["assets"]] == ["alpha", "beta"]
    assert body["result"]["manifest"] == "manifests/code-qa.yaml"


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
