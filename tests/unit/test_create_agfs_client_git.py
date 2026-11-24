"""Verify that AGFS startup wires the configured snapshot storage into the binding."""

from pathlib import Path

import pytest

from openviking.pyagfs.exceptions import AGFSNotSupportedError
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config import GitConfig, GitLocalConfig
from openviking_cli.utils.config.agfs_config import AGFSConfig


@pytest.fixture
def agfs_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ov.conf"
    config_path.write_text("{}")
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    return RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path / "data"), backend="local"))


@pytest.mark.parametrize("git_config", [None, GitConfig(enabled=False)], ids=["unset", "disabled"])
def test_git_disabled_rejects_snapshot_reads(agfs_config, git_config):
    client = create_agfs_client(agfs_config, git_config=git_config)
    try:
        with pytest.raises(AGFSNotSupportedError, match="disabled"):
            client.git_show(account="account", target_ref="main")
    finally:
        client.close()


@pytest.mark.parametrize("explicit_dir", [False, True], ids=["default-dir", "configured-dir"])
def test_git_reopens_snapshots_from_storage_directory(agfs_config, tmp_path, explicit_dir):
    storage_dir = Path(agfs_config.agfs.path)
    default_dir = storage_dir / ".ovgit"
    base_dir = tmp_path / "snapshot store" if explicit_dir else default_dir
    git_config = GitConfig(
        enabled=True, local=GitLocalConfig(base_dir=str(base_dir) if explicit_dir else "")
    )
    client = create_agfs_client(agfs_config, git_config=git_config)
    try:
        path = "/local/account/resources/note.md"
        client.ensure_parent_dirs(path)
        client.write(path, b"snapshot contents")
        commit = client.git_commit(
            account="account",
            branch="main",
            message="initial",
            author_name="Alice",
            author_email="alice@example.com",
            paths=["resources/note.md"],
        )
        assert commit["result"] == "created"
        assert any(base_dir.iterdir())
    finally:
        client.close()

    reopened = create_agfs_client(agfs_config, git_config=git_config)
    try:
        blob = reopened.git_show(account="account", target_ref="main", path="resources/note.md")
        assert blob["bytes"] == b"snapshot contents"
    finally:
        reopened.close()
