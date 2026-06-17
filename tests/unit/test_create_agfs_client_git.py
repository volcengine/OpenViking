"""Tests for git wiring in create_agfs_client.

Verifies that when GitConfig.enabled is True, create_agfs_client generates
a ragfs.toml at {storage_path}/.runtime/ragfs.toml with the right [git]
section and passes its path to RAGFSBindingClient via ``git_config_path=``.
When git is disabled (or git_config is None), no ``git_config_path`` is passed
(legacy construction path).
"""
from pathlib import Path
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


def test_git_disabled_passes_no_git_config_path(agfs_config, fake_binding):
    """git_config=None → RAGFSBindingClient gets git_config_path=None."""
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config))
    assert len(fake_binding) == 1
    assert fake_binding[0].kwargs.get("git_config_path") is None


def test_git_disabled_explicit_passes_no_git_config_path(agfs_config, fake_binding):
    """An explicitly-disabled GitConfig is equivalent to None."""
    cfg = GitConfig(enabled=False)
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config), git_config=cfg)
    assert fake_binding[0].kwargs.get("git_config_path") is None


def test_git_enabled_writes_toml_and_passes_git_config_path(agfs_config, fake_binding, tmp_path):
    """enabled=True → writes ragfs.toml under .runtime/, passes git_config_path kwarg."""
    cfg = GitConfig(
        enabled=True,
        backend="local",
        default_branch="main",
        author_name="viking-bot",
        author_email="bot@viking.local",
        local=GitLocalConfig(base_dir=str(tmp_path / "git")),
    )
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config), git_config=cfg)

    # git_config_path was passed to the binding
    kwargs = fake_binding[0].kwargs
    assert "git_config_path" in kwargs
    toml_path = Path(kwargs["git_config_path"])
    assert toml_path.exists()
    assert toml_path.parent.name == ".runtime"
    assert toml_path.name == "ragfs.toml"
    # Lives under storage path, not in tmp_path root
    assert str(toml_path).startswith(str(Path(agfs_config.path).resolve()))

    body = toml_path.read_text()
    assert "[git]" in body
    assert "enabled = true" in body
    assert 'backend = "local"' in body
    assert 'author_name = "viking-bot"' in body
    assert "[git.local]" in body
    assert str(tmp_path / "git") in body


def test_git_enabled_with_empty_base_dir_defaults_to_storage_git(agfs_config, fake_binding):
    """When local.base_dir is empty, the generated toml should fill it with {storage_path}/.ovgit."""
    cfg = GitConfig(enabled=True, local=GitLocalConfig(base_dir=""))
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config), git_config=cfg)
    body = Path(fake_binding[0].kwargs["git_config_path"]).read_text()
    expected = str(Path(agfs_config.path).resolve() / ".ovgit")
    assert expected in body


def test_git_enabled_escapes_special_chars_in_strings(agfs_config, fake_binding, tmp_path):
    """Strings with backslashes / quotes round-trip into valid TOML."""
    cfg = GitConfig(
        enabled=True,
        author_name='He said "hi"',
        author_email='a\\b@x.com',
        local=GitLocalConfig(base_dir=str(tmp_path / "git")),
    )
    create_agfs_client(RagfsBindingConfig(agfs=agfs_config), git_config=cfg)
    body = Path(fake_binding[0].kwargs["git_config_path"]).read_text(encoding="utf-8")
    # Parse it back with a real TOML parser to prove validity.
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # py<3.11
    parsed = tomllib.loads(body)
    assert parsed["git"]["author_name"] == 'He said "hi"'
    assert parsed["git"]["author_email"] == 'a\\b@x.com'
