# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _stub_config():
    return SimpleNamespace(
        code=SimpleNamespace(
            github_domains=["github.com"],
            gitlab_domains=["gitlab.com"],
            code_hosting_domains=[],
        )
    )


def test_parse_code_hosting_url_http_gitlab_deep_path_takes_first_two_parts(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "utils" / "code_hosting_utils.py"
    )
    spec = importlib.util.spec_from_file_location("code_hosting_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "get_openviking_config", _stub_config)
    assert (
        module.parse_code_hosting_url("https://gitlab.com/group/subgroup/project")
        == "group/subgroup"
    )


def test_parse_code_hosting_url_ssh_gitlab_deep_path_takes_first_two_parts(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "utils" / "code_hosting_utils.py"
    )
    spec = importlib.util.spec_from_file_location("code_hosting_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "get_openviking_config", _stub_config)
    assert (
        module.parse_code_hosting_url("git@gitlab.com:group/subgroup/project.git")
        == "group/subgroup"
    )


def test_parse_code_hosting_url_ssh_github_standard_org_repo(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "utils" / "code_hosting_utils.py"
    )
    spec = importlib.util.spec_from_file_location("code_hosting_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "get_openviking_config", _stub_config)
    assert (
        module.parse_code_hosting_url("git@github.com:volcengine/OpenViking.git")
        == "volcengine/OpenViking"
    )


def test_parse_code_hosting_url_rejects_unlisted_host(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "utils" / "code_hosting_utils.py"
    )
    spec = importlib.util.spec_from_file_location("code_hosting_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "get_openviking_config", _stub_config)
    assert module.parse_code_hosting_url("git@evil.com:org/repo.git") is None


def test_parse_code_hosting_url_sanitizes_org_repo(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "utils" / "code_hosting_utils.py"
    )
    spec = importlib.util.spec_from_file_location("code_hosting_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "get_openviking_config", _stub_config)
    assert module.parse_code_hosting_url("git@github.com:o r/g!t.git") == "o_r/g_t"


def test_is_code_repository_root_url_accepts_github_repo_root(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "utils" / "code_hosting_utils.py"
    )
    spec = importlib.util.spec_from_file_location("code_hosting_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "get_openviking_config", _stub_config)
    assert module.is_code_repository_root_url("https://github.com/org/repo")
    assert module.is_code_repository_root_url("https://github.com/org/repo/")
    assert module.is_code_repository_root_url("https://github.com/org/repo.git")


def test_is_code_repository_root_url_rejects_github_non_repo_pages(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "utils" / "code_hosting_utils.py"
    )
    spec = importlib.util.spec_from_file_location("code_hosting_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "get_openviking_config", _stub_config)
    assert not module.is_code_repository_root_url("https://github.com/org/repo/issues/123")
    assert not module.is_code_repository_root_url("https://github.com/org/repo/pull/456")
    assert not module.is_code_repository_root_url("https://github.com/org/repo/blob/main/README.md")
    assert not module.is_code_repository_root_url("https://github.com/org/repo/tree/main")


def test_is_code_repository_root_url_rejects_gitlab_subpages(monkeypatch):
    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "utils" / "code_hosting_utils.py"
    )
    spec = importlib.util.spec_from_file_location("code_hosting_utils", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "get_openviking_config", _stub_config)
    assert module.is_code_repository_root_url("https://gitlab.com/group/project")
    assert not module.is_code_repository_root_url("https://gitlab.com/group/project/-/issues/1")
