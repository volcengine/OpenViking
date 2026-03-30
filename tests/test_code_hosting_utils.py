# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for code_hosting_utils git SSH URL support (Issue #317)."""

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


def _mock_config():
    return SimpleNamespace(
        code=SimpleNamespace(
            github_domains=["github.com", "www.github.com"],
            gitlab_domains=["gitlab.com", "www.gitlab.com"],
            code_hosting_domains=["github.com", "gitlab.com"],
        )
    )


# Ensure openviking_cli.utils.config is importable (stub if needed)
_config_mod_name = "openviking_cli.utils.config"
try:
    importlib.import_module(_config_mod_name)
except Exception:
    for mod_name in ("openviking_cli", "openviking_cli.utils", _config_mod_name):
        if mod_name not in sys.modules:
            m = ModuleType(mod_name)
            sys.modules[mod_name] = m
    sys.modules[_config_mod_name].get_openviking_config = _mock_config  # type: ignore[attr-defined]

# Load code_hosting_utils directly from file to avoid the heavy openviking/__init__.py chain
_module_path = (
    Path(__file__).resolve().parents[1] / "openviking" / "utils" / "code_hosting_utils.py"
)
_spec = importlib.util.spec_from_file_location("openviking.utils.code_hosting_utils", _module_path)
_module = importlib.util.module_from_spec(_spec)
sys.modules["openviking.utils.code_hosting_utils"] = _module
_spec.loader.exec_module(_module)

parse_code_hosting_url = _module.parse_code_hosting_url
is_code_hosting_url = _module.is_code_hosting_url
is_git_repo_url = _module.is_git_repo_url
validate_git_ssh_uri = _module.validate_git_ssh_uri


@pytest.fixture(autouse=True)
def _patch_config():
    with patch.object(_module, "get_openviking_config", side_effect=_mock_config):
        yield


# --- parse_code_hosting_url ---


def test_parse_code_hosting_url_git_ssh():
    assert parse_code_hosting_url("git@github.com:org/repo.git") == "org/repo"


def test_parse_code_hosting_url_git_ssh_no_dotgit():
    assert parse_code_hosting_url("git@github.com:org/repo") == "org/repo"


def test_parse_code_hosting_url_git_ssh_unknown_host():
    assert parse_code_hosting_url("git@unknown.com:org/repo.git") is None


def test_parse_code_hosting_url_git_ssh_single_segment():
    assert parse_code_hosting_url("git@github.com:repo") is None


def test_parse_code_hosting_url_https():
    assert parse_code_hosting_url("https://github.com/org/repo") == "org/repo"


def test_parse_code_hosting_url_https_dotgit():
    assert parse_code_hosting_url("https://github.com/org/repo.git") == "org/repo"


# --- validate_git_ssh_uri ---


def test_validate_git_ssh_uri_valid():
    validate_git_ssh_uri("git@github.com:org/repo.git")  # should not raise


def test_validate_git_ssh_uri_not_git():
    with pytest.raises(ValueError, match="Not a git@ SSH URI"):
        validate_git_ssh_uri("https://github.com/org/repo")


def test_validate_git_ssh_uri_no_colon():
    with pytest.raises(ValueError, match="missing colon or empty path"):
        validate_git_ssh_uri("git@github.com")


def test_validate_git_ssh_uri_empty_path():
    with pytest.raises(ValueError, match="missing colon or empty path"):
        validate_git_ssh_uri("git@github.com:")


# --- is_code_hosting_url ---


def test_is_code_hosting_url_git_ssh():
    assert is_code_hosting_url("git@github.com:org/repo.git") is True


def test_is_code_hosting_url_git_ssh_no_colon():
    assert is_code_hosting_url("git@github.com") is False


def test_is_code_hosting_url_https():
    assert is_code_hosting_url("https://github.com/org/repo") is True


# --- is_git_repo_url ---


def test_is_git_repo_url_git_ssh():
    assert is_git_repo_url("git@github.com:org/repo.git") is True


def test_is_git_repo_url_https_repo():
    assert is_git_repo_url("https://github.com/org/repo") is True


def test_is_git_repo_url_https_issues():
    assert is_git_repo_url("https://github.com/org/repo/issues/123") is False


def test_is_git_repo_url_https_pull():
    assert is_git_repo_url("https://github.com/org/repo/pull/456") is False


def test_is_git_repo_url_https_blob():
    assert is_git_repo_url("https://github.com/org/repo/blob/main/file.py") is False


def test_is_git_repo_url_unknown_domain():
    assert is_git_repo_url("https://example.com/org/repo") is False


def test_is_git_repo_url_single_segment():
    assert is_git_repo_url("https://github.com/org") is False
