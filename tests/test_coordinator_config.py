# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.service.coordinator import InProcessCoordinator, RedisCoordinator
from openviking_cli.utils.config.storage_config import CoordinationConfig, StorageConfig


class TestCoordinatorConfigDefaults:
    def test_default_backend_is_memory(self):
        assert StorageConfig().coordination.backend == "memory"

    def test_redis_sub_config_has_defaults(self):
        cfg = StorageConfig().coordination
        assert cfg.redis.key_prefix == "ov:coord:"
        assert cfg.redis.ttl_sec == 3600
        assert cfg.redis.dsn is None

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception, match="extra"):  # pydantic extra=forbid
            CoordinationConfig(backend="memory", unknown_field="x")

    def test_negative_ttl_rejected(self):
        with pytest.raises(ValueError):
            CoordinationConfig(backend="redis", redis={"ttl_sec": -1})

    def test_redis_null_rejected(self):
        """Explicitly setting redis=null must raise at config validation time."""
        with pytest.raises(Exception, match="none is not an allowed value|Input should be a valid"):
            CoordinationConfig(backend="redis", redis=None)


class TestBuildCoordinatorMemory:
    def test_memory_backend_returns_in_process(self):
        coord = StorageConfig(coordination={"backend": "memory"}).build_coordinator()
        assert isinstance(coord, InProcessCoordinator)

    def test_default_config_returns_in_process(self):
        coord = StorageConfig().build_coordinator()
        assert isinstance(coord, InProcessCoordinator)


class TestBuildCoordinatorRedis:
    def test_redis_backend_with_dsn_returns_redis_coordinator(self, monkeypatch):
        import fakeredis

        monkeypatch.setattr(
            "redis.Redis.from_url",
            staticmethod(lambda *a, **kw: fakeredis.FakeStrictRedis(decode_responses=True)),
            raising=False,
        )
        coord = StorageConfig(
            coordination={"backend": "redis", "redis": {"dsn": "redis://fake:6379/0"}}
        ).build_coordinator()
        assert isinstance(coord, RedisCoordinator)

    def test_redis_backend_reads_nested_key_prefix(self, monkeypatch):
        import fakeredis

        monkeypatch.setattr(
            "redis.Redis.from_url",
            staticmethod(lambda *a, **kw: fakeredis.FakeStrictRedis(decode_responses=True)),
            raising=False,
        )
        coord = StorageConfig(
            coordination={
                "backend": "redis",
                "redis": {"dsn": "redis://fake:6379/0", "key_prefix": "custom:"},
            }
        ).build_coordinator()
        assert coord._prefix == "custom:"

    def test_redis_backend_reads_nested_ttl(self, monkeypatch):
        import fakeredis

        monkeypatch.setattr(
            "redis.Redis.from_url",
            staticmethod(lambda *a, **kw: fakeredis.FakeStrictRedis(decode_responses=True)),
            raising=False,
        )
        coord = StorageConfig(
            coordination={
                "backend": "redis",
                "redis": {"dsn": "redis://fake:6379/0", "ttl_sec": 120},
            }
        ).build_coordinator()
        assert coord.default_ttl_sec == 120

    def test_redis_backend_falls_back_to_env_dsn(self, monkeypatch):
        import fakeredis

        monkeypatch.setenv("OPENVIKING_COORD_DSN", "redis://env-host:6379/0")
        monkeypatch.setattr(
            "redis.Redis.from_url",
            staticmethod(lambda *a, **kw: fakeredis.FakeStrictRedis(decode_responses=True)),
            raising=False,
        )
        coord = StorageConfig(coordination={"backend": "redis"}).build_coordinator()
        assert isinstance(coord, RedisCoordinator)

    def test_redis_backend_missing_dsn_raises(self, monkeypatch):
        monkeypatch.delenv("OPENVIKING_COORD_DSN", raising=False)
        with pytest.raises(ValueError, match="DSN"):
            StorageConfig(coordination={"backend": "redis"}).build_coordinator()


class TestBuildCoordinatorCustomBackend:
    def test_custom_backend_class_path_is_called(self, tmp_path):
        """backend = 'module.ClassName' loads the class and calls from_config."""
        import sys

        plugin_src = tmp_path / "ov_custom_coord.py"
        plugin_src.write_text(
            "from openviking.service.coordinator import InProcessCoordinator\n"
            "class MyCoord:\n"
            "    @classmethod\n"
            "    def from_config(cls, cfg):\n"
            "        return InProcessCoordinator()\n"
        )
        sys.path.insert(0, str(tmp_path))
        try:
            coord = StorageConfig(
                coordination={"backend": "ov_custom_coord.MyCoord"}
            ).build_coordinator()
            assert isinstance(coord, InProcessCoordinator)
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("ov_custom_coord", None)

    def test_custom_backend_receives_coordination_config(self, tmp_path):
        """from_config receives the CoordinationConfig with correct nested values."""
        import sys

        plugin_src = tmp_path / "ov_cfg_capture.py"
        plugin_src.write_text(
            "from openviking.service.coordinator import InProcessCoordinator\n"
            "received = []\n"
            "class CaptureCoord:\n"
            "    @classmethod\n"
            "    def from_config(cls, cfg):\n"
            "        received.append(cfg)\n"
            "        return InProcessCoordinator()\n"
        )
        sys.path.insert(0, str(tmp_path))
        try:
            StorageConfig(
                coordination={
                    "backend": "ov_cfg_capture.CaptureCoord",
                    "redis": {"key_prefix": "p:", "ttl_sec": 99},
                }
            ).build_coordinator()
            import ov_cfg_capture

            assert len(ov_cfg_capture.received) == 1
            cfg = ov_cfg_capture.received[0]
            assert cfg.redis.key_prefix == "p:"
            assert cfg.redis.ttl_sec == 99
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("ov_cfg_capture", None)

    def test_custom_backend_receives_custom_params(self, tmp_path):
        """from_config can read custom_params for third-party configuration."""
        import sys

        plugin_src = tmp_path / "ov_params_capture.py"
        plugin_src.write_text(
            "from openviking.service.coordinator import InProcessCoordinator\n"
            "received = []\n"
            "class ParamsCoord:\n"
            "    @classmethod\n"
            "    def from_config(cls, cfg):\n"
            "        received.append(cfg.custom_params)\n"
            "        return InProcessCoordinator()\n"
        )
        sys.path.insert(0, str(tmp_path))
        try:
            StorageConfig(
                coordination={
                    "backend": "ov_params_capture.ParamsCoord",
                    "custom_params": {"pool_size": 10, "ssl": True},
                }
            ).build_coordinator()
            import ov_params_capture

            assert ov_params_capture.received[0] == {"pool_size": 10, "ssl": True}
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("ov_params_capture", None)

    def test_custom_backend_unknown_no_dot_raises(self):
        with pytest.raises(ValueError, match="Built-in backends"):
            StorageConfig(coordination={"backend": "unknown_backend"}).build_coordinator()

    def test_custom_backend_module_not_found_raises(self):
        with pytest.raises(ValueError, match="cannot import module"):
            StorageConfig(coordination={"backend": "nonexistent_xyz.MyCoord"}).build_coordinator()

    def test_custom_backend_class_not_found_raises(self, tmp_path):
        """Class name that doesn't exist in the module raises a clear error."""
        import sys

        plugin_src = tmp_path / "ov_empty_mod.py"
        plugin_src.write_text("# empty\n")
        sys.path.insert(0, str(tmp_path))
        try:
            with pytest.raises(ValueError, match="not found in module"):
                StorageConfig(
                    coordination={"backend": "ov_empty_mod.NoSuchClass"}
                ).build_coordinator()
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("ov_empty_mod", None)

    def test_custom_backend_missing_from_config_raises(self, tmp_path):
        """Class without from_config raises a clear error."""
        import sys

        plugin_src = tmp_path / "ov_no_from_config.py"
        plugin_src.write_text("class BareCoord: pass\n")
        sys.path.insert(0, str(tmp_path))
        try:
            with pytest.raises(ValueError, match="from_config"):
                StorageConfig(
                    coordination={"backend": "ov_no_from_config.BareCoord"}
                ).build_coordinator()
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("ov_no_from_config", None)

    def test_custom_backend_from_config_returns_none_raises(self, tmp_path):
        import sys

        plugin_src = tmp_path / "ov_none_coord.py"
        plugin_src.write_text(
            "class NullCoord:\n    @classmethod\n    def from_config(cls, cfg): return None\n"
        )
        sys.path.insert(0, str(tmp_path))
        try:
            with pytest.raises(ValueError, match="returned None"):
                StorageConfig(
                    coordination={"backend": "ov_none_coord.NullCoord"}
                ).build_coordinator()
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("ov_none_coord", None)
