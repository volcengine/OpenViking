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
from openviking.utils.agfs_utils import _build_git_config_dict


class TestGitConfigDefaults:
    def test_enabled_by_default(self):
        cfg = GitConfig()
        assert cfg.enabled is True
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
        assert cfg.git.enabled is True
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


class TestGitInheritsFromAgfs:
    """git section inherits unset defaults from storage.agfs."""

    FULL_S3 = {
        "bucket": "B",
        "region": "R",
        "endpoint": "E",
        "access_key": "AK",
        "secret_key": "SK",
    }

    def test_no_git_section_local_agfs(self):
        cfg = OpenVikingConfig(storage={"workspace": "/tmp/x"})
        assert cfg.git.enabled is True
        assert cfg.git.backend == "local"
        assert cfg.git.s3 is None

    def test_backend_inherits_s3_from_agfs(self):
        cfg = OpenVikingConfig(
            storage={"workspace": "/tmp/x", "agfs": {"backend": "s3", "s3": self.FULL_S3}}
        )
        assert cfg.git.backend == "s3"
        assert cfg.git.s3.bucket == "B"
        assert cfg.git.s3.region == "R"
        assert cfg.git.s3.endpoint == "E"
        assert cfg.git.s3.access_key == "AK"
        assert cfg.git.s3.secret_key == "SK"

    def test_memory_agfs_maps_to_local(self):
        cfg = OpenVikingConfig(
            storage={"workspace": "/tmp/x", "agfs": {"backend": "memory"}}
        )
        assert cfg.git.backend == "local"

    def test_explicit_git_backend_overrides_inheritance(self):
        cfg = OpenVikingConfig(
            storage={"workspace": "/tmp/x", "agfs": {"backend": "s3", "s3": self.FULL_S3}},
            git={"backend": "local"},
        )
        assert cfg.git.backend == "local"
        assert cfg.git.s3 is None

    def test_explicit_git_s3_field_overrides_only_that_field(self):
        cfg = OpenVikingConfig(
            storage={"workspace": "/tmp/x", "agfs": {"backend": "s3", "s3": self.FULL_S3}},
            git={"s3": {"bucket": "GB"}},
        )
        assert cfg.git.s3.bucket == "GB"
        # remaining fields inherited from agfs.s3
        assert cfg.git.s3.region == "R"
        assert cfg.git.s3.endpoint == "E"
        assert cfg.git.s3.access_key == "AK"
        assert cfg.git.s3.secret_key == "SK"

    def test_disabled_git_still_inherits_backend(self):
        cfg = OpenVikingConfig(
            storage={"workspace": "/tmp/x", "agfs": {"backend": "s3", "s3": self.FULL_S3}},
            git={"enabled": False},
        )
        assert cfg.git.enabled is False
        assert cfg.git.backend == "s3"


class TestGitS3ConfigParsing:
    """A5.1 — parsing of the s3 backend config."""

    def test_s3_config_defaults(self):
        s3 = GitS3Config()
        assert s3.bucket == ""
        assert s3.region == "us-east-1"
        assert s3.prefix == ".ovgit"
        assert s3.endpoint == ""
        assert s3.access_key is None
        assert s3.secret_key is None
        assert s3.cas_mode == "native"
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

    def test_redis_lock_cas_mode_rejected(self):
        with pytest.raises(ValidationError):
            GitS3Config(cas_mode="redis_lock")


class TestBuildGitConfigDictS3:
    """A5.2 — _build_git_config_dict output for the s3 backend."""

    def _build_s3(self, **s3_kwargs):
        s3_defaults = {"bucket": "my-bucket", "region": "cn-beijing"}
        s3_defaults.update(s3_kwargs)
        cfg = GitConfig(enabled=True, backend="s3", s3=GitS3Config(**s3_defaults))
        return _build_git_config_dict(cfg, Path("/tmp/storage"))

    def test_builds_header_and_s3_section(self):
        out = self._build_s3()
        assert out["backend"] == "s3"
        assert "s3" in out
        assert "local" not in out

    def test_builds_required_s3_keys(self):
        out = self._build_s3(prefix="gitobj", endpoint="https://tos.example.com")
        s3 = out["s3"]
        assert s3["bucket"] == "my-bucket"
        assert s3["region"] == "cn-beijing"
        assert s3["prefix"] == "gitobj"
        assert s3["endpoint"] == "https://tos.example.com"
        assert s3["cas_mode"] == "native"

    def test_use_path_style_is_bool(self):
        out_true = self._build_s3(use_path_style=True)
        assert out_true["s3"]["use_path_style"] is True
        out_false = self._build_s3(use_path_style=False)
        assert out_false["s3"]["use_path_style"] is False

    def test_credentials_emitted_when_present(self):
        out = self._build_s3(access_key="AK", secret_key="SK")
        assert out["s3"]["access_key"] == "AK"
        assert out["s3"]["secret_key"] == "SK"

    def test_credentials_omitted_when_empty(self):
        out = self._build_s3()
        assert "access_key" not in out["s3"]
        assert "secret_key" not in out["s3"]

    def test_missing_s3_section_raises(self):
        # Build a disabled config (skips model validation) then force s3=None to
        # exercise the builder's own guard.
        cfg = GitConfig(enabled=False, backend="s3", s3=None)
        with pytest.raises(ValueError):
            _build_git_config_dict(cfg, Path("/tmp/storage"))


class TestBuildGitConfigDictLocal:
    """A5.3 — regression: local backend still builds only a 'local' section."""

    def test_local_builds_local_section_only(self):
        cfg = GitConfig(enabled=True, backend="local", local=GitLocalConfig(base_dir="/tmp/g"))
        out = _build_git_config_dict(cfg, Path("/tmp/storage"))
        assert out["backend"] == "local"
        assert "local" in out
        assert "s3" not in out
        assert out["local"]["base_dir"] == "/tmp/g"

    def test_local_base_dir_defaults_to_storage_git(self):
        cfg = GitConfig(enabled=True, backend="local")
        out = _build_git_config_dict(cfg, Path("/tmp/storage"))
        assert out["local"]["base_dir"] == str(Path("/tmp/storage") / ".ovgit")
