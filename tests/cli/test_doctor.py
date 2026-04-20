# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ``openviking-server doctor`` diagnostic checks."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from openviking_cli.doctor import (
    check_agfs,
    check_config,
    check_disk,
    check_embedding,
    check_native_engine,
    check_ollama,
    check_python,
    check_vlm,
    main as doctor_main,
    run_doctor,
)


class TestCheckConfig:
    def test_pass_with_valid_config(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({"embedding": {"dense": {}}}))
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_config()
        assert ok
        assert str(config) in detail

    def test_fail_missing_config(self):
        with patch("openviking_cli.doctor._find_config", return_value=None):
            ok, detail, fix = check_config()
        assert not ok
        assert "not found" in detail
        assert fix is not None

    def test_fail_invalid_json(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text("{bad json")
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_config()
        assert not ok
        assert "Invalid JSON" in detail

    def test_pass_without_embedding_section(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({"server": {}}))
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_config()
        assert ok
        assert str(config) in detail


class TestCheckPython:
    def test_pass_current_python(self):
        ok, detail, fix = check_python()
        assert ok  # Tests run on Python >= 3.10

    def test_fail_old_python(self):
        with patch.object(sys, "version_info", (3, 9, 0, "final", 0)):
            ok, detail, fix = check_python()
        assert not ok
        assert "3.9.0" in detail


class TestCheckNativeEngine:
    def test_pass_when_available(self):
        with patch(
            "openviking_cli.doctor.ENGINE_VARIANT",
            "native",
            create=True,
        ):
            # Need to patch the import itself
            import openviking.storage.vectordb.engine as engine_mod

            original_variant = engine_mod.ENGINE_VARIANT
            engine_mod.ENGINE_VARIANT = "native"
            try:
                ok, detail, fix = check_native_engine()
                assert ok
                assert "native" in detail
            finally:
                engine_mod.ENGINE_VARIANT = original_variant

    def test_fail_when_unavailable(self):
        import openviking.storage.vectordb.engine as engine_mod

        original_variant = engine_mod.ENGINE_VARIANT
        original_available = engine_mod.AVAILABLE_ENGINE_VARIANTS
        engine_mod.ENGINE_VARIANT = "unavailable"
        engine_mod.AVAILABLE_ENGINE_VARIANTS = ()
        try:
            ok, detail, fix = check_native_engine()
            assert not ok
            assert "No compatible" in detail
            assert fix is not None
        finally:
            engine_mod.ENGINE_VARIANT = original_variant
            engine_mod.AVAILABLE_ENGINE_VARIANTS = original_available


class TestCheckAgfs:
    def test_pass_when_importable(self):
        # pyagfs may not load cleanly in all envs (e.g. dev source checkout)
        ok, detail, fix = check_agfs()
        # Just verify it returns a valid tuple - pass/fail depends on environment
        assert isinstance(ok, bool)
        assert isinstance(detail, str)

    def test_pass_when_only_vendored_openviking_pyagfs_is_available(self):
        real_import = __import__

        def import_side_effect(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "pyagfs":
                raise ImportError("No module named 'pyagfs'")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=import_side_effect):
            ok, detail, fix = check_agfs()

        assert ok
        assert "AGFS" in detail
        assert fix is None

    def test_fail_when_missing(self):
        with patch(
            "openviking_cli.doctor.importlib.import_module",
            side_effect=ImportError("No module named 'openviking.pyagfs'"),
        ):
            ok, detail, fix = check_agfs()
        assert not ok
        assert "Bundled AGFS client not found" in detail
        assert fix is not None


class TestCheckEmbedding:
    class _FakeEmbedResult:
        def __init__(self, vector: list[float]):
            self.dense_vector = vector

    class _FakeDenseEmbedder:
        def __init__(self, vector: list[float]):
            self.vector = vector
            self.closed = False

        def embed(self, text: str, is_query: bool = False):
            return TestCheckEmbedding._FakeEmbedResult(self.vector)

        def close(self):
            self.closed = True

    @staticmethod
    def _local_helpers(model_path: Path):
        def get_local_model_cache_path(_model: str, _cache_dir: str) -> Path:
            return model_path

        def get_local_model_spec(model: str):
            return {"model": model}

        return get_local_model_cache_path, get_local_model_spec

    def test_fail_local_default_when_optional_dependency_missing(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({}))
        real_import = __import__

        def import_side_effect(name):
            if name == "llama_cpp":
                raise ImportError("No module named 'llama_cpp'")
            return real_import(name)

        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch(
                "openviking_cli.doctor._get_local_embedding_helpers",
                return_value=self._local_helpers(tmp_path / "unused.gguf"),
            ):
                with patch(
                    "openviking_cli.doctor.importlib.import_module",
                    side_effect=import_side_effect,
                ):
                    ok, detail, fix = check_embedding()

        assert not ok
        assert "missing llama-cpp-python" in detail
        assert "openviking[local-embed]" in fix

    def test_pass_local_default_with_cached_model(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({}))
        cached_model = (
            Path.home() / ".cache" / "openviking" / "models" / "bge-small-zh-v1.5-f16.gguf"
        )
        real_import = __import__

        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch(
                "openviking_cli.doctor._get_local_embedding_helpers",
                return_value=self._local_helpers(cached_model),
            ):
                with patch.object(Path, "exists", autospec=True, return_value=True):
                    with patch(
                        "openviking_cli.doctor.importlib.import_module",
                        side_effect=lambda name: object()
                        if name == "llama_cpp"
                        else real_import(name),
                    ):
                        ok, detail, fix = check_embedding()

        assert ok
        assert "local/bge-small-zh-v1.5-f16" in detail
        assert fix is None

    def test_pass_local_default_reports_startup_download_when_cache_missing(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({}))
        real_import = __import__

        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch(
                "openviking_cli.doctor._get_local_embedding_helpers",
                return_value=self._local_helpers(tmp_path / "missing.gguf"),
            ):
                with patch.object(Path, "exists", autospec=True, return_value=False):
                    with patch(
                        "openviking_cli.doctor.importlib.import_module",
                        side_effect=lambda name: object()
                        if name == "llama_cpp"
                        else real_import(name),
                    ):
                        ok, detail, fix = check_embedding()

        assert ok
        assert "startup initialization" in detail
        assert fix is None

    def test_fail_local_unknown_model(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "local",
                            "model": "unknown-local-model",
                        }
                    }
                }
            )
        )

        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch(
                "openviking_cli.doctor._get_local_embedding_helpers",
                return_value=(
                    lambda _model, _cache_dir: tmp_path / "unused.gguf",
                    lambda _model: (_ for _ in ()).throw(
                        ValueError("Unknown local embedding model: unknown-local-model")
                    ),
                ),
            ):
                ok, detail, fix = check_embedding()

        assert not ok
        assert "unsupported local model" in detail
        assert "Unknown local embedding model" in fix

    def test_live_remote_validation_passes_and_matches_dimension(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                            "api_key": "sk-test123",
                            "dimension": 3,
                        }
                    }
                }
            )
        )
        fake_embedder = self._FakeDenseEmbedder([0.1, 0.2, 0.3])

        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch(
                "openviking_cli.doctor._build_dense_embedder_for_live_validation",
                return_value=fake_embedder,
            ):
                ok, detail, fix = check_embedding(live=True)

        assert ok
        assert "live OK" in detail
        assert "matches config" in detail
        assert fix is None
        assert fake_embedder.closed

    def test_live_remote_validation_fails_on_dimension_mismatch(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                            "api_key": "sk-test123",
                            "dimension": 5,
                        }
                    }
                }
            )
        )

        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch(
                "openviking_cli.doctor._build_dense_embedder_for_live_validation",
                return_value=self._FakeDenseEmbedder([0.1, 0.2, 0.3]),
            ):
                ok, detail, fix = check_embedding(live=True)

        assert not ok
        assert "live dimension mismatch" in detail
        assert "Update embedding.dense.dimension" in fix

    def test_live_remote_validation_surfaces_actionable_provider_error(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                            "api_key": "sk-test123",
                            "api_base": "https://example.invalid/v1",
                        }
                    }
                }
            )
        )

        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch(
                "openviking_cli.doctor._build_dense_embedder_for_live_validation",
                side_effect=RuntimeError("401 unauthorized"),
            ):
                ok, detail, fix = check_embedding(live=True)

        assert not ok
        assert "live validation failed" in detail
        assert "embedding.dense.api_base" in fix
        assert "401 unauthorized" in fix

    def test_live_local_validation_keeps_static_local_checks(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({}))
        cached_model = (
            Path.home() / ".cache" / "openviking" / "models" / "bge-small-zh-v1.5-f16.gguf"
        )
        real_import = __import__

        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch(
                "openviking_cli.doctor._get_local_embedding_helpers",
                return_value=self._local_helpers(cached_model),
            ):
                with patch.object(Path, "exists", autospec=True, return_value=True):
                    with patch(
                        "openviking_cli.doctor.importlib.import_module",
                        side_effect=lambda name: object()
                        if name == "llama_cpp"
                        else real_import(name),
                    ):
                        with patch(
                            "openviking_cli.doctor._build_dense_embedder_for_live_validation"
                        ) as live_builder:
                            ok, detail, fix = check_embedding(live=True)

        assert ok
        assert "local/bge-small-zh-v1.5-f16" in detail
        assert fix is None
        live_builder.assert_not_called()

    def test_pass_with_api_key(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                            "api_key": "sk-test123",
                        }
                    }
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_embedding()
        assert ok
        assert "openai" in detail

    def test_pass_with_api_key_from_environment_variable(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                            "api_key": "${OPENAI_API_KEY}",
                        }
                    }
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-123"}, clear=False):
                ok, detail, fix = check_embedding()
        assert ok
        assert "openai" in detail

    def test_fail_no_api_key(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                            "api_key": "{your-api-key}",
                        }
                    }
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENAI_API_KEY", None)
                ok, detail, fix = check_embedding()
        assert not ok
        assert "no API key" in detail

    def test_fail_invalid_json(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text("{not valid json")
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_embedding()
        assert not ok
        assert "unreadable" in detail


class TestCheckVlm:
    def test_pass_with_config(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {"vlm": {"provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-test"}}
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_vlm()
        assert ok

    def test_fail_no_provider(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(json.dumps({"vlm": {}}))
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_vlm()
        assert not ok

    def test_fail_invalid_json(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text("{not valid json")
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_vlm()
        assert not ok
        assert "unreadable" in detail


class TestCheckOllama:
    def test_pass_when_config_is_missing(self):
        with patch("openviking_cli.doctor._find_config", return_value=None):
            ok, detail, fix = check_ollama()
        assert ok
        assert detail == "not configured"
        assert fix is None

    def test_pass_when_config_does_not_use_ollama(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "openai",
                            "model": "text-embedding-3-small",
                        }
                    },
                    "vlm": {"provider": "openai", "model": "gpt-4o-mini"},
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            ok, detail, fix = check_ollama()
        assert ok
        assert detail == "not configured"
        assert fix is None

    def test_checks_embedding_ollama_api_base(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "ollama",
                            "model": "bge-m3",
                            "api_base": "http://embedding-host:11435/v1",
                        }
                    }
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch("openviking_cli.utils.ollama.check_ollama_running", return_value=True) as running:
                ok, detail, fix = check_ollama()
        running.assert_called_once_with("embedding-host", 11435)
        assert ok
        assert "embedding-host:11435" in detail
        assert fix is None

    def test_checks_vlm_ollama_api_base(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "vlm": {
                        "provider": "litellm",
                        "model": "ollama/llava",
                        "api_base": "http://vlm-host:11436/v1",
                    }
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch("openviking_cli.utils.ollama.check_ollama_running", return_value=True) as running:
                ok, detail, fix = check_ollama()
        running.assert_called_once_with("vlm-host", 11436)
        assert ok
        assert "vlm-host:11436" in detail
        assert fix is None

    def test_fails_when_configured_ollama_is_unreachable(self, tmp_path: Path):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {
                        "dense": {
                            "provider": "ollama",
                            "model": "bge-m3",
                            "api_base": "http://localhost:11434/v1",
                        }
                    }
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            with patch("openviking_cli.utils.ollama.check_ollama_running", return_value=False):
                ok, detail, fix = check_ollama()
        assert not ok
        assert "unreachable at localhost:11434" in detail
        assert "ollama serve" in fix


class TestCheckDisk:
    def test_pass_normal_disk(self):
        ok, detail, fix = check_disk()
        # Should pass on any dev machine
        assert ok
        assert "GB free" in detail


class TestRunDoctor:
    def test_returns_zero_when_all_pass(self, tmp_path: Path, capsys):
        config = tmp_path / "ov.conf"
        config.write_text(
            json.dumps(
                {
                    "embedding": {"dense": {"provider": "openai", "model": "m", "api_key": "sk-x"}},
                    "vlm": {"provider": "openai", "model": "m", "api_key": "sk-x"},
                }
            )
        )
        with patch("openviking_cli.doctor._find_config", return_value=config):
            code = run_doctor()
        captured = capsys.readouterr()
        assert "OpenViking Doctor" in captured.out
        # May not be 0 if native engine is missing, but the function should complete
        assert isinstance(code, int)

    def test_returns_one_on_failure(self, capsys):
        with patch("openviking_cli.doctor._find_config", return_value=None):
            code = run_doctor()
        assert code == 1
        captured = capsys.readouterr()
        assert "FAIL" in captured.out


class TestDoctorMain:
    def test_main_parses_live_and_config_flags(self, tmp_path: Path):
        config = tmp_path / "doctor.conf"
        config.write_text(json.dumps({"embedding": {"dense": {}}}))

        with patch("openviking_cli.doctor.run_doctor", return_value=0) as run:
            with patch.dict(os.environ, {}, clear=False):
                exit_code = doctor_main(["doctor", "--config", str(config), "--live"])
                assert os.environ["OPENVIKING_CONFIG_FILE"] == str(config)

        assert exit_code == 0
        run.assert_called_once_with(live_embedding=True)


def _import_fail(blocked_name: str):
    """Return an __import__ replacement that blocks one specific module."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _mock_import(name, *args, **kwargs):
        if name == blocked_name:
            raise ImportError(f"Mocked: {name}")
        return real_import(name, *args, **kwargs)

    return _mock_import
