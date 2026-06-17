"""Unit tests for GitConfig pydantic model."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config import (
    GitConfig,
    GitLocalConfig,
    GitS3Config,
    OpenVikingConfig,
)
from openviking.utils.agfs_utils import _render_git_toml


class TestGitConfigDefaults:
    def test_disabled_by_default(self):
        cfg = GitConfig()
        assert cfg.enabled is False
        assert cfg.backend == "local"
        assert cfg.default_branch == "main"
        assert cfg.author_name == "viking-bot"
        assert cfg.author_email == "bot@viking.local"

    def test_local_subconfig_defaults(self):
        cfg = GitConfig()
        assert isinstance(cfg.local, GitLocalConfig)
        assert cfg.local.base_dir == ""


class TestGitConfigValidation:
    def test_invalid_backend_rejected(self):
        with pytest.raises(ValidationError):
            GitConfig(backend="ftp")

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            GitConfig(unknown_thing=True)

    def test_enabled_with_local_backend_ok(self):
        cfg = GitConfig(enabled=True, backend="local", local=GitLocalConfig(base_dir="/tmp/git"))
        assert cfg.enabled is True
        assert cfg.local.base_dir == "/tmp/git"


class TestGitConfigOnOpenVikingConfig:
    def test_open_viking_config_has_git_field_with_default(self):
        cfg = OpenVikingConfig(storage={"workspace": "/tmp/x"})
        assert isinstance(cfg.git, GitConfig)
        assert cfg.git.enabled is False
        assert cfg.git.backend == "local"
        assert isinstance(cfg.git.local, GitLocalConfig)

    def test_open_viking_config_accepts_git_section(self):
        cfg = OpenVikingConfig(
            storage={"workspace": "/tmp/x"},
            git={
                "enabled": True,
                "backend": "local",
                "local": {"base_dir": "/tmp/g"},
            },
        )
        assert cfg.git.enabled is True
        assert cfg.git.local.base_dir == "/tmp/g"

    def test_git_config_round_trip_via_config_file(self, tmp_path):
        """Round-trip the new `git` section through the runtime JSON file loader."""
        from openviking_cli.utils.config.open_viking_config import (
            OpenVikingConfigSingleton,
        )

        cfg_dict = {
            "storage": {"workspace": str(tmp_path / "data")},
            "git": {
                "enabled": True,
                "backend": "local",
                "default_branch": "main",
                "author_name": "viking-bot",
                "author_email": "bot@viking.local",
                "local": {"base_dir": str(tmp_path / "git")},
            },
        }
        cfg_path = tmp_path / "ov.conf"
        cfg_path.write_text(json.dumps(cfg_dict))

        cfg = OpenVikingConfigSingleton._load_from_file(str(cfg_path))

        assert cfg.git.enabled is True
        assert cfg.git.local.base_dir == str(tmp_path / "git")


class TestGitS3ConfigParsing:
    """A5.1 — parsing of the s3 backend config."""

    def test_s3_config_defaults(self):
        s3 = GitS3Config()
        assert s3.bucket == ""
        assert s3.region == "us-east-1"
        assert s3.prefix == "git"
        assert s3.endpoint == ""
        assert s3.access_key is None
        assert s3.secret_key is None
        assert s3.cas_mode == "native"
        assert s3.redis_lock_url is None
        assert s3.use_path_style is True

    def test_backend_s3_with_s3_section_ok(self):
        cfg = GitConfig(
            enabled=True,
            backend="s3",
            s3=GitS3Config(bucket="b", region="cn-beijing"),
        )
        assert cfg.backend == "s3"
        assert cfg.s3.bucket == "b"
        assert cfg.s3.region == "cn-beijing"

    def test_enabled_backend_s3_without_s3_section_rejected(self):
        with pytest.raises(ValidationError):
            GitConfig(enabled=True, backend="s3", s3=None)

    def test_enabled_backend_s3_missing_bucket_rejected(self):
        with pytest.raises(ValidationError):
            GitConfig(enabled=True, backend="s3", s3=GitS3Config(region="cn-beijing"))

    def test_enabled_backend_s3_missing_region_rejected(self):
        # region has a non-empty default, so explicitly blank it to trigger the check.
        with pytest.raises(ValidationError):
            GitConfig(
                enabled=True,
                backend="s3",
                s3=GitS3Config(bucket="b", region=""),
            )

    def test_disabled_backend_s3_skips_validation(self):
        # When git is disabled the s3 section is not required.
        cfg = GitConfig(enabled=False, backend="s3", s3=None)
        assert cfg.s3 is None

    def test_s3_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            GitS3Config(bucket="b", region="r", unknown="x")

    def test_invalid_cas_mode_rejected(self):
        with pytest.raises(ValidationError):
            GitS3Config(cas_mode="invalid")


class TestRenderGitTomlS3:
    """A5.2 — _render_git_toml output for the s3 backend."""

    def _render_s3(self, **s3_kwargs):
        s3_defaults = {"bucket": "my-bucket", "region": "cn-beijing"}
        s3_defaults.update(s3_kwargs)
        cfg = GitConfig(enabled=True, backend="s3", s3=GitS3Config(**s3_defaults))
        return _render_git_toml(cfg, Path("/tmp/storage"))

    def test_renders_header_and_s3_section(self):
        out = self._render_s3()
        assert 'backend = "s3"' in out
        assert "[git.s3]" in out
        assert "[git.local]" not in out

    def test_renders_required_s3_keys(self):
        out = self._render_s3(prefix="gitobj", endpoint="https://tos.example.com")
        assert 'bucket = "my-bucket"' in out
        assert 'region = "cn-beijing"' in out
        assert 'prefix = "gitobj"' in out
        assert 'endpoint = "https://tos.example.com"' in out
        assert 'cas_mode = "native"' in out

    def test_use_path_style_rendered_as_lowercase_bool(self):
        out_true = self._render_s3(use_path_style=True)
        assert "use_path_style = true" in out_true
        out_false = self._render_s3(use_path_style=False)
        assert "use_path_style = false" in out_false

    def test_credentials_emitted_when_present(self):
        out = self._render_s3(access_key="AK", secret_key="SK")
        assert 'access_key = "AK"' in out
        assert 'secret_key = "SK"' in out

    def test_credentials_omitted_when_empty(self):
        out = self._render_s3()
        assert "access_key" not in out
        assert "secret_key" not in out

    def test_redis_lock_url_omitted_when_none(self):
        out = self._render_s3()
        assert "redis_lock_url" not in out

    def test_redis_lock_url_emitted_when_present(self):
        out = self._render_s3(cas_mode="redis_lock", redis_lock_url="redis://localhost:6379")
        assert 'redis_lock_url = "redis://localhost:6379"' in out

    def test_missing_s3_section_raises(self):
        # Build a disabled config (skips model validation) then force s3=None to
        # exercise the renderer's own guard.
        cfg = GitConfig(enabled=False, backend="s3", s3=None)
        with pytest.raises(ValueError):
            _render_git_toml(cfg, Path("/tmp/storage"))


class TestRenderGitTomlLocal:
    """A5.3 — regression: local backend still renders only [git.local]."""

    def test_local_renders_local_section_only(self):
        cfg = GitConfig(enabled=True, backend="local", local=GitLocalConfig(base_dir="/tmp/g"))
        out = _render_git_toml(cfg, Path("/tmp/storage"))
        assert 'backend = "local"' in out
        assert "[git.local]" in out
        assert "[git.s3]" not in out
        assert 'base_dir = "/tmp/g"' in out

    def test_local_base_dir_defaults_to_storage_git(self):
        cfg = GitConfig(enabled=True, backend="local")
        out = _render_git_toml(cfg, Path("/tmp/storage"))
        assert f'base_dir = "{Path("/tmp/storage") / ".ovgit"}"' in out
