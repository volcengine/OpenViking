"""Tests for the openviking-server init setup wizard."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from openviking_cli.setup_wizard import (
    _DEFAULT_CODEX_MODEL,
    _DEFAULT_GLM_MODEL,
    _DEFAULT_KIMI_MODEL,
    _DEFAULT_WORKSPACE,
    CLOUD_PROVIDERS,
    EMBEDDING_PRESETS,
    LOCAL_GGUF_PRESETS,
    VLM_PRESETS,
    _build_cloud_config,
    _build_local_config,
    _build_ollama_config,
    _get_recommended_indices,
    _is_llamacpp_installed,
    _prompt_required_input,
    _prompt_required_int,
    _wizard_cloud,
    _write_config,
    run_init,
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
            vlm_model="gpt-4o-mini",
            workspace="/tmp/ov_test",
            vlm_api_key="sk-test",
        )

        assert config["embedding"]["dense"]["api_key"] == "sk-test"
        assert config["vlm"]["api_key"] == "sk-test"
        assert config["vlm"]["provider"] == "openai"

    def test_cloud_config_supports_codex_vlm(self):
        provider = CLOUD_PROVIDERS[1]  # Volcengine

        config = _build_cloud_config(
            provider,
            embedding_api_key="ve-test",
            embedding_model="doubao-embedding-vision-250615",
            embedding_dim=1024,
            vlm_model="gpt-5.3-codex",
            workspace="/tmp/ov_test",
            vlm_provider="openai-codex",
            vlm_api_base="https://chatgpt.com/backend-api/codex",
        )

        assert config["embedding"]["dense"]["provider"] == "volcengine"
        assert config["embedding"]["dense"]["api_key"] == "ve-test"
        assert config["vlm"]["provider"] == "openai-codex"
        assert config["vlm"]["model"] == "gpt-5.3-codex"
        assert config["vlm"]["api_base"] == "https://chatgpt.com/backend-api/codex"
        assert "api_key" not in config["vlm"]

    def test_cloud_wizard_codex_uses_default_base_and_workspace(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[2, 3],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "ve-test",
                    "doubao-embedding-vision-250615",
                    "gpt-5.3-codex",
                ],
            ):
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=1024,
                ):
                    with patch("openviking_cli.setup_wizard._ensure_codex_auth", return_value=True):
                        config = _wizard_cloud()

        assert config is not None
        assert config["storage"]["workspace"] == _DEFAULT_WORKSPACE
        assert config["vlm"]["provider"] == "openai-codex"
        assert config["vlm"]["api_base"] == "https://chatgpt.com/backend-api/codex"
        assert "api_key" not in config["vlm"]

    def test_cloud_wizard_supports_openai_vlm_option(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[1, 1],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "embed-test",
                    "text-embedding-3-small",
                    "openai-vlm-test",
                    "gpt-5.4",
                ],
            ):
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=1536,
                ):
                    config = _wizard_cloud()

        assert config is not None
        assert config["storage"]["workspace"] == _DEFAULT_WORKSPACE
        assert config["vlm"]["provider"] == "openai"
        assert config["vlm"]["api_key"] == "openai-vlm-test"
        assert config["vlm"]["api_base"] == CLOUD_PROVIDERS[0].default_api_base

    def test_cloud_wizard_supports_volcengine_vlm_option(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[1, 2],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "embed-test",
                    "text-embedding-3-small",
                    "ve-vlm-test",
                    "doubao-seed-2-0-code-preview-260215",
                ],
            ):
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=1536,
                ):
                    config = _wizard_cloud()

        assert config is not None
        assert config["storage"]["workspace"] == _DEFAULT_WORKSPACE
        assert config["vlm"]["provider"] == "volcengine"
        assert config["vlm"]["api_key"] == "ve-vlm-test"
        assert config["vlm"]["api_base"] == CLOUD_PROVIDERS[1].default_api_base
        assert config["vlm"]["model"] == "doubao-seed-2-0-code-preview-260215"

    def test_cloud_wizard_supports_kimi_vlm(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[2, 4],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "ve-test",
                    "doubao-embedding-vision-250615",
                    "kimi-test",
                    "kimi-code",
                ],
            ):
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=1024,
                ):
                    config = _wizard_cloud()

        assert config is not None
        assert config["vlm"]["provider"] == "kimi"
        assert config["vlm"]["api_key"] == "kimi-test"
        assert config["vlm"]["model"] == "kimi-code"
        assert config["vlm"]["api_base"] == "https://api.kimi.com/coding"
        assert config["storage"]["workspace"] == _DEFAULT_WORKSPACE

    def test_cloud_wizard_supports_glm_vlm(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[2, 5],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "ve-test",
                    "doubao-embedding-vision-250615",
                    "glm-test",
                    "glm-4.6v",
                ],
            ):
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=1024,
                ):
                    config = _wizard_cloud()

        assert config is not None
        assert config["vlm"]["provider"] == "glm"
        assert config["vlm"]["api_key"] == "glm-test"
        assert config["vlm"]["model"] == "glm-4.6v"
        assert config["vlm"]["api_base"] == "https://api.z.ai/api/coding/paas/v4"
        assert config["storage"]["workspace"] == _DEFAULT_WORKSPACE

    def test_prompt_required_input_uses_default_on_empty(self):
        with patch("builtins.input", return_value=""):
            value = _prompt_required_input("Model", default=_DEFAULT_KIMI_MODEL)

        assert value == _DEFAULT_KIMI_MODEL

    def test_prompt_required_int_uses_default_on_empty(self):
        with patch("builtins.input", return_value=""):
            value = _prompt_required_int("Dimension", default=1024)

        assert value == 1024

    def test_cloud_wizard_uses_requested_defaults_when_inputs_are_empty(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[2, 3],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "ve-test",
                    CLOUD_PROVIDERS[1].default_embedding_model,
                    _DEFAULT_CODEX_MODEL,
                ],
            ) as prompt_input:
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=CLOUD_PROVIDERS[1].default_embedding_dim,
                ) as prompt_int:
                    with patch("openviking_cli.setup_wizard._ensure_codex_auth", return_value=True):
                        config = _wizard_cloud()

        assert config is not None
        assert config["embedding"]["dense"]["model"] == CLOUD_PROVIDERS[1].default_embedding_model
        assert config["embedding"]["dense"]["dimension"] == CLOUD_PROVIDERS[1].default_embedding_dim
        assert config["vlm"]["model"] == _DEFAULT_CODEX_MODEL
        prompt_input.assert_any_call("Model", default=CLOUD_PROVIDERS[1].default_embedding_model)
        prompt_input.assert_any_call("Model", default=_DEFAULT_CODEX_MODEL)
        prompt_int.assert_called_once_with(
            "Dimension", default=CLOUD_PROVIDERS[1].default_embedding_dim
        )

    def test_cloud_wizard_kimi_uses_requested_default_model(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[2, 4],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "ve-test",
                    CLOUD_PROVIDERS[1].default_embedding_model,
                    "kimi-test",
                    _DEFAULT_KIMI_MODEL,
                ],
            ) as prompt_input:
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=CLOUD_PROVIDERS[1].default_embedding_dim,
                ):
                    config = _wizard_cloud()

        assert config is not None
        assert config["vlm"]["model"] == _DEFAULT_KIMI_MODEL
        prompt_input.assert_any_call("Model", default=_DEFAULT_KIMI_MODEL)

    def test_cloud_wizard_glm_uses_requested_default_model(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[2, 5],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "ve-test",
                    CLOUD_PROVIDERS[1].default_embedding_model,
                    "glm-test",
                    _DEFAULT_GLM_MODEL,
                ],
            ) as prompt_input:
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=CLOUD_PROVIDERS[1].default_embedding_dim,
                ):
                    config = _wizard_cloud()

        assert config is not None
        assert config["vlm"]["model"] == _DEFAULT_GLM_MODEL
        prompt_input.assert_any_call("Model", default=_DEFAULT_GLM_MODEL)

    def test_cloud_wizard_volcengine_uses_requested_default_model(self):
        with patch(
            "openviking_cli.setup_wizard._prompt_choice",
            side_effect=[1, 2],
        ):
            with patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=[
                    "embed-test",
                    CLOUD_PROVIDERS[0].default_embedding_model,
                    "ve-vlm-test",
                    CLOUD_PROVIDERS[1].default_vlm_model,
                ],
            ) as prompt_input:
                with patch(
                    "openviking_cli.setup_wizard._prompt_required_int",
                    return_value=CLOUD_PROVIDERS[0].default_embedding_dim,
                ):
                    config = _wizard_cloud()

        assert config is not None
        assert config["vlm"]["provider"] == "volcengine"
        assert config["vlm"]["api_key"] == "ve-vlm-test"
        assert config["vlm"]["model"] == CLOUD_PROVIDERS[1].default_vlm_model
        prompt_input.assert_any_call("Model", default=CLOUD_PROVIDERS[1].default_vlm_model)

    def test_all_presets_valid(self):
        """Every preset should produce a config with required fields."""
        for emb in EMBEDDING_PRESETS:
            for vlm in VLM_PRESETS:
                config = _build_ollama_config(emb, vlm, "/tmp/test")
                assert "embedding" in config
                assert "vlm" in config
                assert config["embedding"]["dense"]["dimension"] > 0


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

    def test_run_init_redacts_summary_output(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config = {
            "embedding": {
                "dense": {
                    "provider": "local",
                    "model": "secret-model",
                    "dimension": 1024,
                    "model_path": "/very/secret/model.gguf",
                }
            },
            "vlm": {
                "provider": "openai",
                "model": "secret-vlm",
            },
            "storage": {
                "workspace": "/very/secret/workspace",
            },
        }

        with (
            patch("openviking_cli.setup_wizard._DEFAULT_CONFIG_PATH", config_path),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=2),
            patch("openviking_cli.setup_wizard._wizard_ollama", return_value=config),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._write_config", return_value=True),
            patch("builtins.print") as mock_print,
        ):
            assert run_init() == 0

        output = "\n".join(
            " ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list
        )
        assert "/very/secret/model.gguf" not in output
        assert "/very/secret/workspace" not in output
        assert str(config_path) not in output
        assert "custom local model (hidden)" in output
        assert "default config location" in output


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
