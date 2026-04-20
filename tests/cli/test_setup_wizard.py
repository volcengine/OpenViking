"""Tests for the openviking-server init setup wizard."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import openviking_cli.setup_wizard as setup_wizard
from openviking_cli.setup_wizard import (
    BootstrapSmokeResult,
    CLOUD_PROVIDERS,
    EMBEDDING_PRESETS,
    LOCAL_GGUF_PRESETS,
    VLM_PRESETS,
    _build_cloud_config,
    _build_local_config,
    _build_ollama_config,
    _generate_root_api_key,
    _get_recommended_indices,
    _is_llamacpp_installed,
    _run_bootstrap_smoke,
    _with_root_api_key,
    _write_config,
)
from openviking_cli.utils.ollama import (
    check_ollama_running,
    get_ollama_models,
    is_model_available,
)

# ---------------------------------------------------------------------------
# Ollama detection
# ---------------------------------------------------------------------------


class TestOllamaDetection:
    def test_ollama_running(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("openviking_cli.utils.ollama.urllib.request.urlopen", return_value=mock_resp):
            assert check_ollama_running() is True

    def test_ollama_not_running(self):
        import urllib.error

        with patch(
            "openviking_cli.utils.ollama.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert check_ollama_running() is False

    def test_get_models(self):
        mock_data = json.dumps(
            {
                "models": [
                    {"name": "qwen3-embedding:0.6b", "size": 639000000},
                    {"name": "gemma4:e4b", "size": 9600000000},
                ]
            }
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("openviking_cli.utils.ollama.urllib.request.urlopen", return_value=mock_resp):
            models = get_ollama_models()
            assert "qwen3-embedding:0.6b" in models
            assert "gemma4:e4b" in models

    def test_get_models_error(self):
        import urllib.error

        with patch(
            "openviking_cli.utils.ollama.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert get_ollama_models() == []


# ---------------------------------------------------------------------------
# Model availability
# ---------------------------------------------------------------------------


class TestModelAvailability:
    def test_exact_match(self):
        available = ["qwen3-embedding:0.6b", "gemma4:e4b"]
        assert is_model_available("qwen3-embedding:0.6b", available) is True

    def test_no_match(self):
        available = ["qwen3-embedding:0.6b"]
        assert is_model_available("nomic-embed-text", available) is False

    def test_tagless_matches_latest(self):
        available = ["gemma:300m"]
        assert is_model_available("gemma", available) is True

    def test_prefix_variant(self):
        available = ["qwen3-embedding:0.6b-fp16"]
        assert is_model_available("qwen3-embedding:0.6b", available) is True


# ---------------------------------------------------------------------------
# Config building
# ---------------------------------------------------------------------------


class TestConfigBuilding:
    def test_ollama_config_structure(self):
        embedding = EMBEDDING_PRESETS[0]  # qwen3-embedding:0.6b
        vlm = VLM_PRESETS[0]  # qwen3.5:2b

        config = _build_ollama_config(embedding, vlm, "/tmp/ov_test")

        assert config["storage"]["workspace"] == "/tmp/ov_test"

        dense = config["embedding"]["dense"]
        assert dense["provider"] == "ollama"
        assert dense["model"] == "qwen3-embedding:0.6b"
        assert dense["dimension"] == 1024
        assert dense["api_base"] == "http://localhost:11434/v1"

        vlm_cfg = config["vlm"]
        assert vlm_cfg["provider"] == "litellm"
        assert vlm_cfg["model"] == "ollama/qwen3.5:2b"
        assert vlm_cfg["api_key"] == "no-key"
        assert vlm_cfg["api_base"] == "http://localhost:11434"

    def test_cloud_config_structure(self):
        provider = CLOUD_PROVIDERS[0]  # OpenAI

        config = _build_cloud_config(
            provider,
            embedding_api_key="sk-test",
            embedding_model="text-embedding-3-small",
            embedding_dim=1536,
            vlm_api_key="sk-test",
            vlm_model="gpt-4o-mini",
            workspace="/tmp/ov_test",
        )

        assert config["embedding"]["dense"]["api_key"] == "sk-test"
        assert config["vlm"]["api_key"] == "sk-test"
        assert config["vlm"]["provider"] == "openai"

    def test_all_presets_valid(self):
        """Every preset should produce a config with required fields."""
        for emb in EMBEDDING_PRESETS:
            for vlm in VLM_PRESETS:
                config = _build_ollama_config(emb, vlm, "/tmp/test")
                assert "embedding" in config
                assert "vlm" in config
                assert config["embedding"]["dense"]["dimension"] > 0


class TestRootAPIKeyHelpers:
    def test_generate_root_api_key_uses_secure_prefix_and_entropy(self):
        key1 = _generate_root_api_key()
        key2 = _generate_root_api_key()

        assert key1.startswith("ovk_")
        assert len(key1) >= 32
        assert key1 != key2

    def test_with_root_api_key_adds_server_section_without_mutating_input(self):
        config = {"storage": {"workspace": "/tmp/ov_test"}}

        updated = _with_root_api_key(config, "ovk-test-root-key")

        assert updated["server"]["root_api_key"] == "ovk-test-root-key"
        assert "server" not in config

    def test_with_root_api_key_preserves_existing_server_fields(self):
        config = {
            "storage": {"workspace": "/tmp/ov_test"},
            "server": {"host": "0.0.0.0"},
        }

        updated = _with_root_api_key(config, "ovk-test-root-key")

        assert updated["server"] == {
            "host": "0.0.0.0",
            "root_api_key": "ovk-test-root-key",
        }
        assert config["server"] == {"host": "0.0.0.0"}


# ---------------------------------------------------------------------------
# RAM-based recommendations
# ---------------------------------------------------------------------------


class TestRAMRecommendations:
    def test_low_ram(self):
        emb_idx, vlm_idx = _get_recommended_indices(4)
        assert EMBEDDING_PRESETS[emb_idx].model == "qwen3-embedding:0.6b"
        assert VLM_PRESETS[vlm_idx].ollama_model == "qwen3.5:2b"

    def test_medium_ram(self):
        emb_idx, vlm_idx = _get_recommended_indices(16)
        assert EMBEDDING_PRESETS[emb_idx].model == "qwen3-embedding:0.6b"
        assert VLM_PRESETS[vlm_idx].ollama_model == "qwen3.5:4b"

    def test_high_ram(self):
        emb_idx, vlm_idx = _get_recommended_indices(32)
        assert EMBEDDING_PRESETS[emb_idx].model == "qwen3-embedding:8b"

    def test_very_high_ram(self):
        emb_idx, vlm_idx = _get_recommended_indices(128)
        assert EMBEDDING_PRESETS[emb_idx].model == "qwen3-embedding:8b"


# ---------------------------------------------------------------------------
# Config writing
# ---------------------------------------------------------------------------


class TestConfigWriting:
    def test_write_new_config(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config = _build_ollama_config(EMBEDDING_PRESETS[0], VLM_PRESETS[0], str(tmp_path / "data"))

        assert _write_config(config, config_path) is True
        assert config_path.exists()

        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        assert loaded["embedding"]["dense"]["provider"] == "ollama"

    def test_backup_existing(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config_path.write_text('{"old": true}', encoding="utf-8")

        config = _build_ollama_config(EMBEDDING_PRESETS[0], VLM_PRESETS[0], str(tmp_path / "data"))
        assert _write_config(config, config_path) is True

        backup = tmp_path / "ov.conf.bak"
        assert backup.exists()
        assert json.loads(backup.read_text())["old"] is True

    def test_creates_parent_dirs(self, tmp_path):
        config_path = tmp_path / "subdir" / "ov.conf"
        config = _build_ollama_config(EMBEDDING_PRESETS[0], VLM_PRESETS[0], "/tmp/data")

        assert _write_config(config, config_path) is True
        assert config_path.exists()


class TestBootstrapSmoke:
    def test_bootstrap_smoke_passes_with_mocked_loader_and_singleton(self, tmp_path):
        config_path = tmp_path / "ov.conf"

        class FakeSingleton:
            reset_calls = 0
            initialize_calls: list[str] = []

            @classmethod
            def reset_instance(cls):
                cls.reset_calls += 1

            @classmethod
            def initialize(cls, config_path: str):
                cls.initialize_calls.append(config_path)

        load_server_config = MagicMock(return_value="server-config")
        validate_server_config = MagicMock()

        result = _run_bootstrap_smoke(
            config_path,
            load_server_config_fn=load_server_config,
            validate_server_config_fn=validate_server_config,
            config_singleton_cls=FakeSingleton,
        )

        assert result == BootstrapSmokeResult(ok=True, detail="bootstrap config load passed")
        load_server_config.assert_called_once_with(str(config_path))
        validate_server_config.assert_called_once_with("server-config")
        assert FakeSingleton.initialize_calls == [str(config_path)]
        assert FakeSingleton.reset_calls == 2

    def test_bootstrap_smoke_reports_loader_failure(self, tmp_path):
        config_path = tmp_path / "ov.conf"

        class FakeSingleton:
            reset_calls = 0

            @classmethod
            def reset_instance(cls):
                cls.reset_calls += 1

            @classmethod
            def initialize(cls, config_path: str):
                raise AssertionError("should not be called")

        result = _run_bootstrap_smoke(
            config_path,
            load_server_config_fn=MagicMock(side_effect=ValueError("invalid ov.conf")),
            validate_server_config_fn=MagicMock(),
            config_singleton_cls=FakeSingleton,
        )

        assert result.ok is False
        assert "invalid ov.conf" in result.detail
        assert FakeSingleton.reset_calls == 2


class TestRunInitWiring:
    def test_run_init_generates_root_key_and_runs_bootstrap_smoke(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        base_config = _build_ollama_config(
            EMBEDDING_PRESETS[0],
            VLM_PRESETS[0],
            str(tmp_path / "data"),
        )

        with patch.object(setup_wizard, "_DEFAULT_CONFIG_PATH", config_path):
            with patch.object(setup_wizard, "_prompt_choice", side_effect=[2, 1]):
                with patch.object(setup_wizard, "_prompt_confirm", return_value=True):
                    with patch.object(setup_wizard, "_wizard_ollama", return_value=base_config):
                        with patch.object(
                            setup_wizard,
                            "_generate_root_api_key",
                            return_value="ovk-generated-test-key",
                        ):
                            with patch.object(setup_wizard, "_write_config", return_value=True) as write_mock:
                                with patch.object(
                                    setup_wizard,
                                    "_run_bootstrap_smoke",
                                    return_value=BootstrapSmokeResult(
                                        ok=True,
                                        detail="bootstrap config load passed",
                                    ),
                                ) as smoke_mock:
                                    exit_code = setup_wizard.run_init()

        assert exit_code == 0
        assert "server" not in base_config

        written_config = write_mock.call_args.args[0]
        assert written_config["server"]["root_api_key"] == "ovk-generated-test-key"
        smoke_mock.assert_called_once_with(config_path)


# ---------------------------------------------------------------------------
# llama.cpp local embedding config
# ---------------------------------------------------------------------------


class TestLocalConfigBuilding:
    def test_local_config_with_builtin_model(self):
        preset = LOCAL_GGUF_PRESETS[0]
        config = _build_local_config(
            model_name=preset.model_name,
            dimension=preset.dimension,
            workspace="/tmp/ov_test",
        )

        assert config["storage"]["workspace"] == "/tmp/ov_test"

        dense = config["embedding"]["dense"]
        assert dense["provider"] == "local"
        assert dense["model"] == "bge-small-zh-v1.5-f16"
        assert dense["dimension"] == 512
        assert "model_path" not in dense
        assert "vlm" not in config

    def test_local_config_with_ollama_vlm(self):
        config = _build_local_config(
            model_name="bge-small-zh-v1.5-f16",
            dimension=512,
            workspace="/tmp/ov_test",
            vlm_config={
                "provider": "litellm",
                "model": "ollama/qwen3.5:2b",
                "api_key": "no-key",
                "api_base": "http://localhost:11434",
            },
        )

        assert config["embedding"]["dense"]["provider"] == "local"
        assert config["vlm"]["provider"] == "litellm"
        assert config["vlm"]["model"] == "ollama/qwen3.5:2b"

    def test_local_config_with_cloud_vlm(self):
        config = _build_local_config(
            model_name="bge-small-zh-v1.5-f16",
            dimension=512,
            workspace="/tmp/ov_test",
            vlm_config={
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
            },
        )

        assert config["embedding"]["dense"]["provider"] == "local"
        assert config["vlm"]["provider"] == "openai"
        assert config["vlm"]["model"] == "gpt-4o-mini"

    def test_local_config_without_vlm(self):
        config = _build_local_config(
            model_name="bge-small-zh-v1.5-f16",
            dimension=512,
            workspace="/tmp/ov_test",
        )

        assert "vlm" not in config

    def test_local_config_with_cache_dir(self):
        config = _build_local_config(
            model_name="bge-small-zh-v1.5-f16",
            dimension=512,
            workspace="/tmp/ov_test",
            cache_dir="/custom/cache",
        )

        assert config["embedding"]["dense"]["cache_dir"] == "/custom/cache"


class TestLlamaCppDetection:
    def test_llamacpp_installed(self):
        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}):
            assert _is_llamacpp_installed() is True

    def test_llamacpp_not_installed(self):
        import importlib

        with patch.object(importlib, "import_module", side_effect=ImportError("no module")):
            assert _is_llamacpp_installed() is False


class TestLocalGGUFPresets:
    def test_presets_have_valid_dimensions(self):
        for preset in LOCAL_GGUF_PRESETS:
            assert preset.dimension > 0
            assert preset.model_name
            assert preset.label
