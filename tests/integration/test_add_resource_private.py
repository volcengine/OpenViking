# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integration tests for private repository support in add_resource.

These tests verify the end-to-end token injection pipeline without requiring
a running OpenViking server.  HTTP calls are intercepted with httpx.MockTransport.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils.git_credentials import inject_token, mask_token_in_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_transport(captured_requests: list) -> httpx.MockTransport:
    """Return an httpx.MockTransport that records requests and returns a canned success response."""

    def _handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        body = json.dumps(
            {"status": "ok", "result": {"root_uri": "viking://user/resources/test"}}
        )
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    return httpx.MockTransport(_handler)


async def _make_client(
    captured_requests: list,
    git_credentials: dict | None = None,
) -> AsyncHTTPClient:
    """Build an AsyncHTTPClient whose HTTP transport is mocked."""
    mock_cli_config = MagicMock()
    mock_cli_config.git_credentials = git_credentials
    mock_cli_config.root_api_key = None
    mock_cli_config.tenant_id = None

    with patch(
        "openviking_cli.client.http.load_ovcli_config", return_value=mock_cli_config
    ):
        client = AsyncHTTPClient(base_url="http://localhost:7779")

    # Replace the internal httpx.AsyncClient transport with our mock.
    transport = _make_mock_transport(captured_requests)
    client._http = httpx.AsyncClient(
        transport=transport, base_url="http://localhost:7779"
    )
    return client


# ---------------------------------------------------------------------------
# Token injection via git_credentials dict
# ---------------------------------------------------------------------------


class TestAddResourceTokenInjection:
    async def test_github_url_gets_token_injected_from_credentials_dict(self, tmp_path):
        captured: list[httpx.Request] = []
        client = await _make_client(captured, git_credentials={"github.com": "myghtoken"})

        await client.add_resource("https://github.com/org/private-repo")

        assert len(captured) == 1
        body = json.loads(captured[0].content)
        path_sent = body.get("path", "")
        assert "myghtoken" in path_sent
        assert path_sent.startswith("https://myghtoken@github.com")

    async def test_gitlab_url_gets_token_injected_from_credentials_dict(self, tmp_path):
        captured: list[httpx.Request] = []
        client = await _make_client(captured, git_credentials={"gitlab.com": "gltoken"})

        await client.add_resource("https://gitlab.com/group/private-project")

        assert len(captured) == 1
        body = json.loads(captured[0].content)
        path_sent = body.get("path", "")
        assert "gltoken" in path_sent
        assert path_sent.startswith("https://gltoken@gitlab.com")

    async def test_public_url_with_no_credentials_passes_unchanged(self):
        captured: list[httpx.Request] = []
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "openviking_cli.utils.git_credentials._load_ovcli_git_credentials",
                return_value=None,
            ):
                client = await _make_client(captured, git_credentials=None)
                await client.add_resource("https://github.com/org/public-repo")

        body = json.loads(captured[0].content)
        path_sent = body.get("path", "")
        assert path_sent == "https://github.com/org/public-repo"

    async def test_token_from_github_env_var_injected(self):
        captured: list[httpx.Request] = []
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GITLAB_TOKEN")}
        env["GITHUB_TOKEN"] = "env_gh_token"
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "openviking_cli.utils.git_credentials._load_ovcli_git_credentials",
                return_value=None,
            ):
                client = await _make_client(captured, git_credentials=None)
                await client.add_resource("https://github.com/org/private-repo")

        body = json.loads(captured[0].content)
        path_sent = body.get("path", "")
        assert "env_gh_token" in path_sent

    async def test_token_from_gitlab_env_var_injected(self):
        captured: list[httpx.Request] = []
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GITLAB_TOKEN")}
        env["GITLAB_TOKEN"] = "env_gl_token"
        with patch.dict(os.environ, env, clear=True):
            with patch(
                "openviking_cli.utils.git_credentials._load_ovcli_git_credentials",
                return_value=None,
            ):
                client = await _make_client(captured, git_credentials=None)
                await client.add_resource("https://gitlab.com/group/private-project")

        body = json.loads(captured[0].content)
        path_sent = body.get("path", "")
        assert "env_gl_token" in path_sent


# ---------------------------------------------------------------------------
# Token resolution priority
# ---------------------------------------------------------------------------


class TestTokenResolutionPriority:
    async def test_explicit_dict_beats_env_var(self):
        captured: list[httpx.Request] = []
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        env["GITHUB_TOKEN"] = "should_not_be_used"
        with patch.dict(os.environ, env, clear=True):
            client = await _make_client(
                captured, git_credentials={"github.com": "dict_wins"}
            )
            await client.add_resource("https://github.com/org/repo")

        body = json.loads(captured[0].content)
        path_sent = body.get("path", "")
        assert "dict_wins" in path_sent
        assert "should_not_be_used" not in path_sent


# ---------------------------------------------------------------------------
# Token masking (ensure secrets don't leak in logs / markers)
# ---------------------------------------------------------------------------


class TestTokenMasking:
    def test_injected_url_is_maskable(self):
        url = "https://github.com/org/private-repo"
        with_token = inject_token(url, "top_secret_token")
        masked = mask_token_in_url(with_token)
        assert "top_secret_token" not in masked
        assert "***@github.com" in masked

    def test_mask_is_idempotent(self):
        url = "https://token@github.com/org/repo"
        first = mask_token_in_url(url)
        second = mask_token_in_url(first)
        assert first == second

    def test_url_without_token_is_unchanged_by_mask(self):
        url = "https://github.com/org/public-repo"
        assert mask_token_in_url(url) == url


# ---------------------------------------------------------------------------
# rust_cli _preprocess_add_resource_token helper
# ---------------------------------------------------------------------------


class TestPreprocessAddResourceToken:
    def _preprocess(self, argv: list[str]) -> list[str]:
        from openviking_cli.rust_cli import _preprocess_add_resource_token

        return _preprocess_add_resource_token(argv)

    def test_injects_token_into_github_url(self):
        argv = ["ov", "add-resource", "https://github.com/org/repo", "--token", "mytoken"]
        result = self._preprocess(argv)
        assert "--token" not in result
        assert "mytoken" not in result  # stripped from explicit arg
        injected_url = next(a for a in result if "github.com" in a)
        assert "mytoken@github.com" in injected_url

    def test_strips_token_flag_and_value(self):
        argv = ["ov", "add-resource", "https://github.com/org/repo", "--token", "mytoken"]
        result = self._preprocess(argv)
        assert "--token" not in result
        assert "mytoken" not in " ".join(
            a for a in result if "github.com" not in a
        )

    def test_no_token_flag_returns_argv_unchanged(self):
        argv = ["ov", "add-resource", "https://github.com/org/repo"]
        result = self._preprocess(argv)
        assert result == argv

    def test_non_git_url_not_injected(self):
        argv = ["ov", "add-resource", "/local/path", "--token", "mytoken"]
        result = self._preprocess(argv)
        # Token removed from flags, no git URL to inject into
        assert "--token" not in result
        assert "/local/path" in result
