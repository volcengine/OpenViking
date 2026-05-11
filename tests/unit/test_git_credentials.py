# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for openviking_cli.utils.git_credentials."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from openviking_cli.utils.git_credentials import (
    _extract_url_host,
    _load_ovcli_git_credentials,
    get_token_for_url,
    inject_token,
    is_git_url,
    mask_token_in_url,
    save_git_credentials,
)


class TestExtractUrlHost:
    def test_https_simple(self):
        assert _extract_url_host("https://github.com/org/repo") == "github.com"

    def test_https_with_port(self):
        assert _extract_url_host("https://gitlab.example.com:8443/org/repo") == "gitlab.example.com"

    def test_http(self):
        assert _extract_url_host("http://self-hosted.example.com/repo") == "self-hosted.example.com"

    def test_git_ssh_colon(self):
        assert _extract_url_host("git@github.com:org/repo.git") == "github.com"

    def test_git_ssh_gitlab(self):
        assert _extract_url_host("git@gitlab.com:group/project.git") == "gitlab.com"

    def test_git_ssh_no_colon(self):
        assert _extract_url_host("git@nodomain") == ""

    def test_https_with_token_in_userinfo(self):
        assert _extract_url_host("https://mytoken@github.com/org/repo") == "github.com"

    def test_empty_string(self):
        assert _extract_url_host("") == ""

    def test_non_url(self):
        assert _extract_url_host("not-a-url") == ""


class TestIsGitUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/org/repo.git",
            "http://localhost:3000/user/repo",
            "git@github.com:org/repo.git",
            "git://github.com/org/repo.git",
            "ssh://git@bitbucket.org/user/repo.git",
        ],
    )
    def test_valid_git_urls(self, url):
        assert is_git_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "/local/path/to/repo",
            "C:\\Users\\user\\repo",
            "ftp://example.com/repo",
            "s3://bucket/path",
            "",
            "plain string",
        ],
    )
    def test_non_git_urls(self, url):
        assert is_git_url(url) is False


class TestInjectToken:
    def test_https_no_existing_credentials(self):
        result = inject_token("https://github.com/org/repo", "mytoken")
        assert result == "https://mytoken@github.com/org/repo"

    def test_https_replaces_existing_credentials(self):
        result = inject_token("https://oldtoken@github.com/org/repo", "newtoken")
        assert result == "https://newtoken@github.com/org/repo"

    def test_http(self):
        result = inject_token("http://self-hosted.example.com/repo", "secret")
        assert result == "http://secret@self-hosted.example.com/repo"

    def test_https_with_port(self):
        result = inject_token("https://gitlab.example.com:8443/group/repo", "tok")
        assert result == "https://tok@gitlab.example.com:8443/group/repo"

    def test_ssh_url_unchanged(self):
        url = "git@github.com:org/repo.git"
        assert inject_token(url, "mytoken") == url

    def test_git_scheme_unchanged(self):
        url = "git://github.com/org/repo.git"
        assert inject_token(url, "mytoken") == url

    def test_ssh_scheme_unchanged(self):
        url = "ssh://git@github.com/org/repo.git"
        assert inject_token(url, "mytoken") == url

    def test_empty_token_produces_empty_userinfo(self):
        result = inject_token("https://github.com/org/repo", "")
        # Empty token: userinfo is empty, resulting in https://@github.com/...
        assert "github.com" in result


class TestMaskTokenInUrl:
    def test_masks_token(self):
        result = mask_token_in_url("https://mytoken@github.com/org/repo")
        assert result == "https://***@github.com/org/repo"
        assert "mytoken" not in result

    def test_no_token_unchanged(self):
        url = "https://github.com/org/repo"
        assert mask_token_in_url(url) == url

    def test_ssh_unchanged(self):
        url = "git@github.com:org/repo.git"
        assert mask_token_in_url(url) == url

    def test_with_port(self):
        result = mask_token_in_url("https://tok@gitlab.example.com:8443/group/repo")
        assert result == "https://***@gitlab.example.com:8443/group/repo"
        assert "tok" not in result

    def test_empty_string_unchanged(self):
        assert mask_token_in_url("") == ""

    def test_roundtrip_inject_then_mask(self):
        original = "https://github.com/org/repo"
        with_token = inject_token(original, "supersecret")
        masked = mask_token_in_url(with_token)
        assert "supersecret" not in masked
        assert "***@github.com" in masked


class TestGetTokenForUrl:
    def test_explicit_credentials_dict_takes_priority(self):
        creds = {"github.com": "explicit_token"}
        with patch.dict(os.environ, {"GITHUB_TOKEN": "env_token"}, clear=False):
            result = get_token_for_url("https://github.com/org/repo", credentials=creds)
        assert result == "explicit_token"

    def test_github_env_var_fallback(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "env_github_token"}, clear=False):
            with patch(
                "openviking_cli.utils.git_credentials._load_ovcli_git_credentials",
                return_value=None,
            ):
                result = get_token_for_url("https://github.com/org/repo")
        assert result == "env_github_token"

    def test_gitlab_env_var_fallback(self):
        with patch.dict(os.environ, {"GITLAB_TOKEN": "env_gitlab_token"}, clear=False):
            with patch(
                "openviking_cli.utils.git_credentials._load_ovcli_git_credentials",
                return_value=None,
            ):
                result = get_token_for_url("https://gitlab.com/group/repo")
        assert result == "env_gitlab_token"

    def test_ovcli_conf_fallback(self):
        with patch.dict(os.environ, {}, clear=False):
            # Remove GITHUB_TOKEN so env fallback is skipped
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
            with patch.dict(os.environ, env, clear=True):
                with patch(
                    "openviking_cli.utils.git_credentials._load_ovcli_git_credentials",
                    return_value={"github.com": "conf_token"},
                ):
                    result = get_token_for_url("https://github.com/org/repo")
        assert result == "conf_token"

    def test_no_token_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "openviking_cli.utils.git_credentials._load_ovcli_git_credentials",
                return_value=None,
            ):
                result = get_token_for_url("https://github.com/org/repo")
        assert result is None

    def test_ssh_url_returns_none_without_creds(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "openviking_cli.utils.git_credentials._load_ovcli_git_credentials",
                return_value=None,
            ):
                result = get_token_for_url("git@github.com:org/repo.git")
        assert result is None

    def test_explicit_creds_bare_host_matches(self):
        # Credentials keyed without port should match a URL with port
        creds = {"github.com": "tok"}
        result = get_token_for_url("https://github.com:443/org/repo", credentials=creds)
        assert result == "tok"


class TestSaveGitCredentials:
    def test_creates_new_conf(self, tmp_path):
        conf_path = tmp_path / "ovcli.conf"
        result = save_git_credentials("github.com", "mytoken", config_path=str(conf_path))
        assert result == conf_path
        data = json.loads(conf_path.read_text())
        assert data["git_credentials"]["github.com"] == "mytoken"

    def test_merges_with_existing_credentials(self, tmp_path):
        conf_path = tmp_path / "ovcli.conf"
        conf_path.write_text(
            json.dumps({"git_credentials": {"gitlab.com": "oldtok"}}), encoding="utf-8"
        )
        save_git_credentials("github.com", "newtok", config_path=str(conf_path))
        data = json.loads(conf_path.read_text())
        assert data["git_credentials"]["gitlab.com"] == "oldtok"
        assert data["git_credentials"]["github.com"] == "newtok"

    def test_overwrites_existing_host(self, tmp_path):
        conf_path = tmp_path / "ovcli.conf"
        conf_path.write_text(
            json.dumps({"git_credentials": {"github.com": "oldtok"}}), encoding="utf-8"
        )
        save_git_credentials("github.com", "newtok", config_path=str(conf_path))
        data = json.loads(conf_path.read_text())
        assert data["git_credentials"]["github.com"] == "newtok"

    def test_preserves_other_conf_keys(self, tmp_path):
        conf_path = tmp_path / "ovcli.conf"
        conf_path.write_text(
            json.dumps({"server": {"host": "127.0.0.1"}}), encoding="utf-8"
        )
        save_git_credentials("github.com", "tok", config_path=str(conf_path))
        data = json.loads(conf_path.read_text())
        assert data["server"]["host"] == "127.0.0.1"
        assert data["git_credentials"]["github.com"] == "tok"

    def test_creates_parent_dirs(self, tmp_path):
        conf_path = tmp_path / "nested" / "dir" / "ovcli.conf"
        save_git_credentials("github.com", "tok", config_path=str(conf_path))
        assert conf_path.exists()

    def test_corrupted_conf_is_overwritten(self, tmp_path):
        conf_path = tmp_path / "ovcli.conf"
        conf_path.write_text("not valid json", encoding="utf-8")
        # Should not raise; starts from empty dict
        save_git_credentials("github.com", "tok", config_path=str(conf_path))
        data = json.loads(conf_path.read_text())
        assert data["git_credentials"]["github.com"] == "tok"


class TestLoadOvcliGitCredentials:
    def test_returns_none_when_no_file(self, tmp_path):
        with patch(
            "openviking_cli.utils.git_credentials.DEFAULT_CONFIG_DIR", tmp_path
        ):
            with patch.dict(
                os.environ,
                {k: v for k, v in os.environ.items() if "OPENVIKING_CLI" not in k},
                clear=True,
            ):
                result = _load_ovcli_git_credentials()
        assert result is None

    def test_reads_from_default_conf(self, tmp_path):
        conf_path = tmp_path / "ovcli.conf"
        conf_path.write_text(
            json.dumps({"git_credentials": {"github.com": "tok"}}), encoding="utf-8"
        )
        with patch("openviking_cli.utils.git_credentials.DEFAULT_CONFIG_DIR", tmp_path):
            with patch.dict(
                os.environ,
                {k: v for k, v in os.environ.items() if "OPENVIKING_CLI" not in k},
                clear=True,
            ):
                result = _load_ovcli_git_credentials()
        assert result == {"github.com": "tok"}

    def test_reads_from_env_override(self, tmp_path):
        conf_path = tmp_path / "custom.conf"
        conf_path.write_text(
            json.dumps({"git_credentials": {"gitlab.com": "envtok"}}), encoding="utf-8"
        )
        env = {k: v for k, v in os.environ.items() if "OPENVIKING_CLI" not in k}
        env["OPENVIKING_CLI_CONFIG_FILE"] = str(conf_path)
        with patch.dict(os.environ, env, clear=True):
            result = _load_ovcli_git_credentials()
        assert result == {"gitlab.com": "envtok"}

    def test_returns_none_when_no_git_credentials_key(self, tmp_path):
        conf_path = tmp_path / "ovcli.conf"
        conf_path.write_text(json.dumps({"server": {"host": "127.0.0.1"}}), encoding="utf-8")
        with patch("openviking_cli.utils.git_credentials.DEFAULT_CONFIG_DIR", tmp_path):
            with patch.dict(
                os.environ,
                {k: v for k, v in os.environ.items() if "OPENVIKING_CLI" not in k},
                clear=True,
            ):
                result = _load_ovcli_git_credentials()
        assert result is None
