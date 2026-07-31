"""Tests for git wiring in create_agfs_client.

Verifies that when GitConfig.enabled is True, create_agfs_client builds an
in-memory git config dict and injects it into the binding ``config`` under the
``git`` key (no file is written to disk). When git is disabled (or git_config is
None), no ``git`` section is added to the binding config.
"""
from types import SimpleNamespace

import pytest

from openviking_cli.utils.config import GitConfig, GitLocalConfig
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client


class _FakeAgfsConfig:
    """Minimal stand-in for StorageConfig.agfs — only what mount + binding need."""

    def __init__(self, path):
        self.path = str(path)
        self.backend = "local"
        self.s3 = None
        # cache section consumed by RagfsBindingConfig.to_binding_dict()
        self.cache = SimpleNamespace(model_dump=lambda **kwargs: {})
        # queuefs default
        self.queuefs = SimpleNamespace(
            backend="sqlite", recover_stale_sec=0, busy_timeout_ms=5000, db_path=None
        )


@pytest.fixture
def agfs_config(tmp_path):
    return _FakeAgfsConfig(tmp_path / "data")


@pytest.fixture
def fake_binding(monkeypatch):
    """Stub out RAGFSBindingClient to capture constructor kwargs."""
    instances = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            instances.append(self)

        def mount(self, *a, **k):
            pass

        def unmount(self, *a, **k):
            pass

    from openviking import pyagfs as pyagfs_mod
    monkeypatch.setattr(
        pyagfs_mod, "get_binding_client", lambda: (_FakeClient, None)
    )
    return instances


def test_git_disabled_omits_git_section(agfs_config, fake_binding):
    """git_config=None → binding config has no 'git' section."""
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config))
    assert len(fake_binding) == 1
    assert "git" not in fake_binding[0].kwargs.get("config", {})
    assert "git_config_path" not in fake_binding[0].kwargs


def test_git_disabled_explicit_omits_git_section(agfs_config, fake_binding):
    """An explicitly-disabled GitConfig is equivalent to None."""
    cfg = GitConfig(enabled=False)
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config), git_config=cfg)
    assert "git" not in fake_binding[0].kwargs.get("config", {})


def test_git_enabled_injects_git_dict_into_config(agfs_config, fake_binding, tmp_path):
    """enabled=True → injects a 'git' dict into the binding config, no file written."""
    cfg = GitConfig(
        enabled=True,
        backend="local",
        default_branch="main",
        author_name="viking-bot",
        author_email="bot@viking.local",
        local=GitLocalConfig(base_dir=str(tmp_path / "git")),
    )
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config), git_config=cfg)

    kwargs = fake_binding[0].kwargs
    assert "git_config_path" not in kwargs
    git = kwargs["config"]["git"]
    assert git["enabled"] is True
    assert git["backend"] == "local"
    assert git["author_name"] == "viking-bot"
    assert git["local"]["base_dir"] == str(tmp_path / "git")


def test_git_enabled_with_empty_base_dir_defaults_to_storage_git(agfs_config, fake_binding):
    """When local.base_dir is empty, the git dict should fill it with {storage_path}/.ovgit."""
    from pathlib import Path

    cfg = GitConfig(enabled=True, local=GitLocalConfig(base_dir=""))
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config), git_config=cfg)
    git = fake_binding[0].kwargs["config"]["git"]
    expected = str(Path(agfs_config.path).resolve() / ".ovgit")
    assert git["local"]["base_dir"] == expected


def test_git_enabled_preserves_special_chars_in_strings(agfs_config, fake_binding, tmp_path):
    """Strings with backslashes / quotes are preserved verbatim in the dict."""
    cfg = GitConfig(
        enabled=True,
        author_name='He said "hi"',
        author_email='a\\b@x.com',
        local=GitLocalConfig(base_dir=str(tmp_path / "git")),
    )
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config), git_config=cfg)
    git = fake_binding[0].kwargs["config"]["git"]
    assert git["author_name"] == 'He said "hi"'
    assert git["author_email"] == 'a\\b@x.com'
