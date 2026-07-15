"""Tests for the openviking-server init setup wizard."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

from openviking_cli.setup_wizard import (
    _CUSTOM_SETUP,
    _DEFAULT_KIMI_MODEL,
    _GO_BACK,
    _SKIP_VLM,
    CLOUD_PROVIDERS,
    EMBEDDING_PRESETS,
    LOCAL_GGUF_PRESETS,
    QUERY_PLANNER_PRESETS,
    VLM_PRESETS,
    _build_ollama_config,
    _build_query_planner_config,
    _config_path,
    _get_recommended_indices,
    _is_llamacpp_installed,
    _mask_secret,
    _masked_input,
    _next_backup_path,
    _parse_size_gb,
    _prompt_api_key,
    _prompt_api_key_with_env,
    _prompt_required_input,
    _prompt_required_int,
    _update_existing_config,
    _wizard_ollama,
    _wizard_query_planner,
    _wizard_server,
    _wizard_two_step,
    _workspace_path,
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
        vlm = VLM_PRESETS[0]  # qwen3.5:4b

        config = _build_ollama_config(embedding, vlm, "/tmp/ov_test")

        assert config["storage"]["workspace"] == "/tmp/ov_test"

        dense = config["embedding"]["dense"]
        assert dense["provider"] == "ollama"
        assert dense["model"] == "qwen3-embedding:0.6b"
        assert dense["dimension"] == 1024
        assert dense["api_base"] == "http://localhost:11434/v1"

        vlm_cfg = config["vlm"]
        assert vlm_cfg["provider"] == "litellm"
        assert vlm_cfg["model"] == "ollama/qwen3.5:4b"
        assert vlm_cfg["api_key"] == "no-key"
        assert vlm_cfg["api_base"] == "http://localhost:11434"
        # Ollama needs a larger context window (default 4096 truncates OV's
        # ~5k-token extraction prompt) and thinking disabled.
        assert vlm_cfg["extra_request_body"] == {"num_ctx": 16384, "think": False}

    def test_prompt_required_input_uses_default_on_empty(self):
        with patch("builtins.input", return_value=""):
            value = _prompt_required_input("Model", default=_DEFAULT_KIMI_MODEL)

        assert value == _DEFAULT_KIMI_MODEL

    def test_prompt_required_int_uses_default_on_empty(self):
        with patch("builtins.input", return_value=""):
            value = _prompt_required_int("Dimension", default=1024)

        assert value == 1024

    def test_all_presets_valid(self):
        """Every preset should produce a config with required fields."""
        for emb in EMBEDDING_PRESETS:
            for vlm in VLM_PRESETS:
                config = _build_ollama_config(emb, vlm, "/tmp/test")
                assert "embedding" in config
                assert "vlm" in config
                assert config["embedding"]["dense"]["dimension"] > 0


# ---------------------------------------------------------------------------
# Query planner
# ---------------------------------------------------------------------------


class TestQueryPlanner:
    def test_build_query_planner_config_structure(self):
        preset = QUERY_PLANNER_PRESETS[0]  # v4_q8
        config = _build_query_planner_config(preset)
        assert config["provider"] == "litellm"
        assert config["model"] == preset.litellm_model
        assert config["model"].startswith("ollama/")
        # litellm Ollama base URL must not carry the /v1 suffix
        assert config["api_base"] == "http://localhost:11434"
        assert config["temperature"] == 0.0
        assert config["extra_request_body"] == {"think": False}

    def test_presets_have_litellm_models(self):
        assert all(p.litellm_model.startswith("ollama/") for p in QUERY_PLANNER_PRESETS)

    def test_wizard_enables_v4_sets_planner_without_prompt_override(self, tmp_path):
        # Prompt selection happens at retrieval time from the configured model;
        # the wizard must not write a prompt override or prompts.templates_dir.
        config_dict: dict = {"embedding": {}, "vlm": {}}
        config_path = tmp_path / "ov.conf"
        with (
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard.get_ollama_models", return_value=[]),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=1),  # v4_q8
            patch("openviking_cli.setup_wizard.is_model_available", return_value=True),
            patch("builtins.print"),
        ):
            _wizard_query_planner(config_dict, ollama_running=True)

        assert config_dict["query_planner"]["model"] == QUERY_PLANNER_PRESETS[0].litellm_model
        assert config_dict["query_planner"]["api_base"] == "http://localhost:11434"
        assert "prompts" not in config_dict
        assert not (config_path.parent / "prompts").exists()

    def test_wizard_v4_sets_planner_and_returns_none(self, tmp_path):
        config_dict: dict = {"embedding": {}, "vlm": {}}
        config_path = tmp_path / "ov.conf"
        with (
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard.get_ollama_models", return_value=[]),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=2),  # v4_q8
            patch("openviking_cli.setup_wizard.is_model_available", return_value=True),
            patch("builtins.print"),
        ):
            _wizard_query_planner(config_dict, ollama_running=True)

        assert config_dict["query_planner"]["model"] == QUERY_PLANNER_PRESETS[1].litellm_model
        assert "prompts" not in config_dict

    def test_wizard_declined_leaves_config_untouched(self, tmp_path):
        # With an Ollama VLM present, the planner is offered; declining the
        # enable prompt must leave the config untouched.
        config_dict: dict = {"embedding": {}, "vlm": {}}
        with (
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=False),
            patch("builtins.print"),
        ):
            _wizard_query_planner(config_dict, ollama_running=True)
        assert "query_planner" not in config_dict
        assert "prompts" not in config_dict

    def test_wizard_no_ollama_vlm_defaults_to_no_without_recommend(self, tmp_path):
        # Cloud / non-Ollama-VLM setups (ollama_running is None) are still offered
        # the planner, but off by default and without the recommendation tag.
        config_dict: dict = {"embedding": {}, "vlm": {}}
        with (
            patch(
                "openviking_cli.setup_wizard._prompt_confirm", return_value=False
            ) as mock_confirm,
            patch("openviking_cli.setup_wizard._ensure_ollama") as mock_ensure,
            patch("builtins.print"),
        ):
            _wizard_query_planner(config_dict, ollama_running=None)

        enable_call = mock_confirm.call_args_list[0]
        assert enable_call.kwargs.get("default") is False
        assert "(recommended)" not in enable_call.args[0]
        mock_ensure.assert_not_called()  # declined before reaching install
        assert "query_planner" not in config_dict

    def test_wizard_no_ollama_vlm_opt_in_runs_install(self, tmp_path):
        # Opting in without an Ollama VLM runs the Ollama install flow.
        config_dict: dict = {"embedding": {}, "vlm": {}}
        with (
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._ensure_ollama", return_value=True) as mock_ensure,
            patch("openviking_cli.setup_wizard.get_ollama_models", return_value=[]),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=1),
            patch("openviking_cli.setup_wizard.is_model_available", return_value=True),
            patch("builtins.print"),
        ):
            _wizard_query_planner(config_dict, ollama_running=None)

        mock_ensure.assert_called_once()
        assert config_dict["query_planner"]["model"] == QUERY_PLANNER_PRESETS[0].litellm_model

    def test_wizard_ollama_vlm_defaults_to_yes_with_recommend(self, tmp_path):
        # With an Ollama VLM present (ollama_running is not None) the planner is
        # recommended, so the enable prompt is tagged and defaults to Yes.
        config_dict: dict = {"embedding": {}, "vlm": {}}
        with (
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True) as mock_confirm,
            patch("openviking_cli.setup_wizard.get_ollama_models", return_value=[]),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=1),
            patch("openviking_cli.setup_wizard.is_model_available", return_value=True),
            patch("builtins.print"),
        ):
            _wizard_query_planner(config_dict, ollama_running=True)

        enable_call = mock_confirm.call_args_list[0]
        assert enable_call.kwargs.get("default") is True
        assert "(recommended)" in enable_call.args[0]
        assert config_dict["query_planner"]["model"] == QUERY_PLANNER_PRESETS[0].litellm_model


# ---------------------------------------------------------------------------
# RAM-based recommendations
# ---------------------------------------------------------------------------


class TestRAMRecommendations:
    def test_low_ram(self):
        emb_idx, vlm_idx = _get_recommended_indices(4)
        assert EMBEDDING_PRESETS[emb_idx].model == "qwen3-embedding:0.6b"
        assert VLM_PRESETS[vlm_idx].ollama_model == "qwen3.5:4b"

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
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=2),
            patch("openviking_cli.setup_wizard._wizard_ollama", return_value=(config, True)),
            patch("openviking_cli.setup_wizard._wizard_query_planner", return_value=None),
            patch(
                "openviking_cli.setup_wizard._wizard_server",
                return_value={"host": "127.0.0.1"},
            ),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._write_config", return_value=True),
            patch("openviking_cli.setup_wizard._post_save_actions", return_value=None),
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

    def test_env_overrides_config_path_and_derives_workspace(self, tmp_path):
        config_path = tmp_path / "runtime" / "ov.conf"
        with patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False):
            assert _config_path() == config_path
            assert _workspace_path() == str(config_path.parent / "data")

    def test_run_init_writes_to_env_config_path(self, tmp_path):
        config_path = tmp_path / "runtime" / "ov.conf"
        config = {
            "embedding": {"dense": {"provider": "ollama", "model": "qwen", "dimension": 1024}},
            "storage": {"workspace": str(config_path.parent / "data")},
        }
        with (
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=2),
            patch("openviking_cli.setup_wizard._wizard_ollama", return_value=(config, True)),
            patch("openviking_cli.setup_wizard._wizard_query_planner", return_value=None),
            patch(
                "openviking_cli.setup_wizard._wizard_server",
                return_value={"host": "127.0.0.1"},
            ),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._write_config", return_value=True) as mock_write,
            patch("openviking_cli.setup_wizard._post_save_actions", return_value=None),
            patch("builtins.print"),
        ):
            assert run_init() == 0

        expected = dict(config)
        expected["server"] = {"host": "127.0.0.1"}
        mock_write.assert_called_once_with(expected, config_path)


# ---------------------------------------------------------------------------
# llama.cpp local embedding config
# ---------------------------------------------------------------------------


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


class TestCloudProviderOrdering:
    def test_volcengine_is_first_and_default(self):
        assert CLOUD_PROVIDERS[0].label == "VolcEngine (火山引擎)"
        assert CLOUD_PROVIDERS[0].provider == "volcengine"

    def test_byteplus_is_second(self):
        assert CLOUD_PROVIDERS[1].label == "BytePlus"
        assert CLOUD_PROVIDERS[1].default_api_base == (
            "https://ark.ap-southeast.bytepluses.com/api/v3"
        )

    def test_openai_is_third(self):
        assert CLOUD_PROVIDERS[2].label == "OpenAI"
        assert CLOUD_PROVIDERS[2].provider == "openai"


class TestBackupRotation:
    def test_first_backup_uses_bak_suffix(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        assert _next_backup_path(config_path) == tmp_path / "ov.conf.bak"

    def test_rotates_when_bak_exists(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        (tmp_path / "ov.conf.bak").write_text("old", encoding="utf-8")
        assert _next_backup_path(config_path) == tmp_path / "ov.conf.bak.1"

    def test_skips_existing_numbered_backups(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        (tmp_path / "ov.conf.bak").write_text("0", encoding="utf-8")
        (tmp_path / "ov.conf.bak.1").write_text("1", encoding="utf-8")
        (tmp_path / "ov.conf.bak.2").write_text("2", encoding="utf-8")
        assert _next_backup_path(config_path) == tmp_path / "ov.conf.bak.3"

    def test_write_config_rotates_existing_backup(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config_path.write_text('{"v":1}', encoding="utf-8")
        (tmp_path / "ov.conf.bak").write_text('{"old":true}', encoding="utf-8")

        config = _build_ollama_config(EMBEDDING_PRESETS[0], VLM_PRESETS[0], str(tmp_path / "data"))
        assert _write_config(config, config_path) is True

        # Original backup preserved, new one rotated to .bak.1
        assert (tmp_path / "ov.conf.bak").read_text() == '{"old":true}'
        assert (tmp_path / "ov.conf.bak.1").read_text() == '{"v":1}'


class TestApiKeyMasking:
    def test_mask_short_secret_is_fully_starred(self):
        assert _mask_secret("abc") == "***"
        assert _mask_secret("a" * 11) == "*" * 11

    def test_mask_long_secret_keeps_prefix_and_suffix(self):
        value = "sk-proj-1234567890ABCDEF"
        masked = _mask_secret(value)
        assert masked.startswith("sk-proj")
        assert masked.endswith("CDEF")
        assert "1234567890AB" not in masked
        assert len(masked) == len(value)

    def test_mask_empty(self):
        assert _mask_secret("") == ""

    def test_prompt_api_key_uses_masked_input(self):
        with patch(
            "openviking_cli.setup_wizard._masked_input",
            return_value="sk-proj-1234567890ABCDEF",
        ) as masked:
            value = _prompt_api_key("API Key")
        assert value == "sk-proj-1234567890ABCDEF"
        masked.assert_called_once()

    def test_prompt_api_key_does_not_print_extra_preview_line(self, capsys):
        with patch(
            "openviking_cli.setup_wizard._masked_input",
            return_value="sk-proj-1234567890ABCDEF",
        ):
            _prompt_api_key("API Key")
        # No extra "Using ..." confirmation line — the inline rewrite in
        # _masked_input is the only place the masked preview should appear.
        assert "Using API Key" not in capsys.readouterr().out

    def test_masked_input_falls_back_to_input_for_non_tty(self):
        with patch("builtins.input", return_value="paste-me") as plain:
            assert _masked_input("API Key: ") == "paste-me"
        plain.assert_called_once_with("API Key: ")

    def test_prompt_required_input_with_mask_routes_through_masked_input(self):
        with patch(
            "openviking_cli.setup_wizard._masked_input",
            return_value="hunter2",
        ) as masked:
            value = _prompt_required_input("API Key", mask=True)
        assert value == "hunter2"
        masked.assert_called_once()


class TestServerWizard:
    def test_local_mode_returns_loopback_host_and_port(self):
        with (
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=1),
            patch("openviking_cli.setup_wizard._prompt_required_int", return_value=1933),
            patch("builtins.print"),
        ):
            assert _wizard_server() == {"host": "127.0.0.1", "port": 1933}

    def test_local_mode_custom_port(self):
        with (
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=1),
            patch("openviking_cli.setup_wizard._prompt_required_int", return_value=8080),
            patch("builtins.print"),
        ):
            assert _wizard_server() == {"host": "127.0.0.1", "port": 8080}

    def test_remote_mode_manual_root_api_key(self):
        with (
            # bind=Remote, key source=Enter my own
            patch("openviking_cli.setup_wizard._prompt_choice", side_effect=[2, 2]),
            patch("openviking_cli.setup_wizard._prompt_required_int", return_value=1933),
            patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                return_value="my-secret-key",
            ),
            patch("builtins.print"),
        ):
            assert _wizard_server() == {
                "host": "0.0.0.0",
                "port": 1933,
                "root_api_key": "my-secret-key",
            }

    def test_remote_mode_generates_and_displays_root_api_key(self):
        with (
            # bind=Remote, key source=Generate one for me
            patch("openviking_cli.setup_wizard._prompt_choice", side_effect=[2, 1]),
            patch("openviking_cli.setup_wizard._prompt_required_int", return_value=1933),
            patch("builtins.print") as mock_print,
        ):
            result = _wizard_server()

        assert result is not None
        key = result["root_api_key"]
        # 64-char hex, matching the repo's root-key convention (token_hex(32)).
        assert len(key) == 64
        int(key, 16)  # raises if not hex
        assert result == {"host": "0.0.0.0", "port": 1933, "root_api_key": key}
        # The full key must be displayed to the user exactly once.
        output = "\n".join(
            " ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list
        )
        assert key in output

    def test_remote_mode_empty_manual_key_cancels(self):
        with (
            patch("openviking_cli.setup_wizard._prompt_choice", side_effect=[2, 2]),
            patch("openviking_cli.setup_wizard._prompt_required_int", return_value=1933),
            patch("openviking_cli.setup_wizard._prompt_required_input", return_value=""),
            patch("builtins.print"),
        ):
            assert _wizard_server() is None


class TestServerWizardCurrentDefaults:
    CURRENT = {"host": "0.0.0.0", "port": 2044, "root_api_key": "k" * 64}

    def test_current_port_seeds_default(self):
        with (
            patch("openviking_cli.setup_wizard._prompt_choice", side_effect=[1]),
            patch(
                "openviking_cli.setup_wizard._prompt_required_int", return_value=2044
            ) as mock_int,
            patch("builtins.print"),
        ):
            result = _wizard_server(current={"host": "127.0.0.1", "port": 2044})
        assert result == {"host": "127.0.0.1", "port": 2044}
        mock_int.assert_called_once_with("Port", default=2044)

    def test_remote_current_defaults_to_remote_and_keeps_key(self):
        with (
            # bind menu (defaults to Remote), key menu option 1 = Keep existing
            patch("openviking_cli.setup_wizard._prompt_choice", side_effect=[2, 1]) as mock_choice,
            patch("openviking_cli.setup_wizard._prompt_required_int", return_value=2044),
            patch("builtins.print"),
        ):
            result = _wizard_server(current=self.CURRENT)

        assert result == self.CURRENT
        # Bind menu default seeded to Remote (2) because current host is 0.0.0.0.
        bind_call = mock_choice.call_args_list[0]
        assert bind_call.kwargs.get("default") == 2
        # Key menu offers "Keep existing key" as the first, default option.
        key_call = mock_choice.call_args_list[1]
        assert key_call.args[1][0][0] == "Keep existing key"

    def test_remote_current_can_rotate_key(self):
        with (
            # bind=Remote, key menu option 2 = Generate one for me (after Keep)
            patch("openviking_cli.setup_wizard._prompt_choice", side_effect=[2, 2]),
            patch("openviking_cli.setup_wizard._prompt_required_int", return_value=2044),
            patch("builtins.print"),
        ):
            result = _wizard_server(current=self.CURRENT)

        assert result is not None
        assert result["root_api_key"] != self.CURRENT["root_api_key"]
        assert len(result["root_api_key"]) == 64


class TestCurrentConfigSeeding:
    def test_cloud_embedding_keeps_existing_key_and_model(self):
        from openviking_cli.setup_wizard import _prompt_cloud_embedding

        current = {
            "provider": "volcengine",
            "model": "doubao-embedding-vision-240000",
            "api_key": "old-key-1234567890",
            "api_base": CLOUD_PROVIDERS[0].default_api_base,
            "dimension": 2048,
        }
        with (
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=1) as mock_choice,
            # Keep the existing API key? -> yes
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            # Model prompt returns its default (the current model)
            patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                side_effect=lambda prompt, default=None, **kw: default,
            ),
            patch("openviking_cli.setup_wizard._prompt_api_key_with_env") as mock_env_key,
            patch("builtins.print"),
        ):
            dense = _prompt_cloud_embedding(current=current)

        assert dense == current
        mock_env_key.assert_not_called()  # key kept, never re-prompted
        # Provider menu default seeded to the current provider.
        assert mock_choice.call_args.kwargs.get("default") == 1

    def test_vlm_option_index_mapping(self):
        from openviking_cli.setup_wizard import _vlm_option_index_for

        assert _vlm_option_index_for({"provider": "volcengine", "api_base": "https://ark.cn"}) == 1
        assert (
            _vlm_option_index_for(
                {"provider": "volcengine", "api_base": "https://ark.ap-southeast.bytepluses.com"}
            )
            == 2
        )
        assert (
            _vlm_option_index_for({"provider": "openai", "api_base": "https://api.openai.com/v1"})
            == 3
        )
        assert _vlm_option_index_for({"provider": "openai-codex"}) == 4
        assert _vlm_option_index_for({"provider": "kimi"}) == 5
        assert _vlm_option_index_for({"provider": "glm"}) == 6
        assert _vlm_option_index_for({"provider": "openai", "api_base": "https://my.proxy"}) == 7
        assert _vlm_option_index_for({"provider": "litellm", "model": "ollama/qwen3.6:27b"}) == 8

    def test_run_init_menu_shows_current_values(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config_path.write_text(json.dumps(_existing_config()), encoding="utf-8")
        with (
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=5) as mock_choice,
            patch("builtins.print") as mock_print,
        ):
            assert run_init() == 0

        # Action menu descriptions carry the current values.
        options = mock_choice.call_args.args[1]
        labels = dict(options)
        assert "openai · old-vlm" in labels["Update VLM"]
        assert "ollama · qwen" in labels["Update embedding"]
        assert "127.0.0.1" in labels["Update server & auth"]
        # And the current-configuration block is printed above the menu.
        output = "\n".join(
            " ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list
        )
        assert "Current configuration" in output

    def test_update_section_shows_change_diff(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config_path.write_text(json.dumps(_existing_config()), encoding="utf-8")
        new_vlm = {"provider": "kimi", "model": "kimi-code", "api_base": "https://new"}
        with (
            patch("openviking_cli.setup_wizard._prompt_cloud_vlm", return_value=(new_vlm, None)),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._post_save_actions", return_value=None),
            patch("builtins.print") as mock_print,
        ):
            assert _update_existing_config(config_path, "vlm") == 0

        output = "\n".join(
            " ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list
        )
        assert "Change:" in output
        assert "openai · old-vlm" in output
        assert "kimi · kimi-code" in output


# ---------------------------------------------------------------------------
# VLM presets (qwen3.6 replaces qwen3.5 27B/35B)
# ---------------------------------------------------------------------------


class TestVLMPresets:
    def test_qwen36_replaces_qwen35_large_tiers(self):
        models = [p.ollama_model for p in VLM_PRESETS]
        assert "qwen3.6:27b" in models
        assert "qwen3.6:35b" in models
        assert "qwen3.5:27b" not in models
        assert "qwen3.5:35b" not in models

    def test_qwen36_litellm_models(self):
        by_ollama = {p.ollama_model: p for p in VLM_PRESETS}
        assert by_ollama["qwen3.6:27b"].litellm_model == "ollama/qwen3.6:27b"
        assert by_ollama["qwen3.6:35b"].litellm_model == "ollama/qwen3.6:35b"

    def test_very_high_ram_recommends_qwen36_27b(self):
        _, vlm_idx = _get_recommended_indices(128)
        assert VLM_PRESETS[vlm_idx].ollama_model == "qwen3.6:27b"


# ---------------------------------------------------------------------------
# Size-hint parsing
# ---------------------------------------------------------------------------


class TestSizeParsing:
    def test_parses_gb(self):
        assert _parse_size_gb("~4.7 GB") == 4.7
        assert _parse_size_gb("~17 GB, 256K ctx") == 17.0

    def test_parses_mb(self):
        assert abs(_parse_size_gb("~639 MB") - 639 / 1024) < 1e-9

    def test_unparseable_returns_zero(self):
        assert _parse_size_gb("~0.8B, recommended") == 0.0
        assert _parse_size_gb("") == 0.0


# ---------------------------------------------------------------------------
# Recommended all-Ollama fast path
# ---------------------------------------------------------------------------


class TestRecommendedOllamaSetup:
    def test_fast_path_builds_full_config_with_planner(self):
        with (
            patch("openviking_cli.setup_wizard._ensure_ollama", return_value=True),
            patch("openviking_cli.setup_wizard.get_ollama_models", return_value=[]),
            patch("openviking_cli.setup_wizard._get_system_ram_gb", return_value=16),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._check_disk_before_pull", return_value=True),
            patch("openviking_cli.setup_wizard.ollama_pull_model", return_value=True),
            patch("builtins.print"),
        ):
            config, ollama_running = _wizard_ollama()

        assert ollama_running is True
        assert config is not None
        # 16 GB RAM tier: qwen3-embedding:0.6b + qwen3.5:4b
        assert config["embedding"]["dense"]["model"] == "qwen3-embedding:0.6b"
        assert config["vlm"]["model"] == "ollama/qwen3.5:4b"
        assert config["query_planner"]["model"] == QUERY_PLANNER_PRESETS[0].litellm_model

    def test_decline_falls_back_to_per_model_selection(self):
        with (
            patch("openviking_cli.setup_wizard._ensure_ollama", return_value=True),
            patch("openviking_cli.setup_wizard.get_ollama_models", return_value=[]),
            patch("openviking_cli.setup_wizard._get_system_ram_gb", return_value=16),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=False),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=1),
            patch("openviking_cli.setup_wizard.is_model_available", return_value=True),
            patch("builtins.print"),
        ):
            config, _ = _wizard_ollama()

        assert config is not None
        assert config["embedding"]["dense"]["model"] == EMBEDDING_PRESETS[0].model
        assert config["vlm"]["model"] == VLM_PRESETS[0].litellm_model
        assert "query_planner" not in config


# ---------------------------------------------------------------------------
# Query planner: skip when already configured
# ---------------------------------------------------------------------------


class TestQueryPlannerAlreadyConfigured:
    def test_early_return_leaves_config_untouched(self):
        planner = _build_query_planner_config(QUERY_PLANNER_PRESETS[0])
        config_dict = {"embedding": {}, "vlm": {}, "query_planner": dict(planner)}
        with patch("openviking_cli.setup_wizard._prompt_confirm") as mock_confirm:
            _wizard_query_planner(config_dict, ollama_running=True)
        mock_confirm.assert_not_called()
        assert config_dict["query_planner"] == planner


# ---------------------------------------------------------------------------
# Cloud "Other (manual)" routes to custom instead of cancelling
# ---------------------------------------------------------------------------


class TestCloudOtherRoutesToCustom:
    def test_run_init_exits_cleanly_on_custom_sentinel(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        with (
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=1),
            patch(
                "openviking_cli.setup_wizard._wizard_two_step",
                return_value=(_CUSTOM_SETUP, None),
            ),
            patch("openviking_cli.setup_wizard._write_config") as mock_write,
            patch("builtins.print"),
        ):
            assert run_init() == 0
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# API key from environment variables
# ---------------------------------------------------------------------------


class TestEnvApiKey:
    def test_uses_env_var_when_confirmed_on_tty(self):
        with (
            patch.dict(os.environ, {"ARK_API_KEY": "env-key-1234567890"}, clear=False),
            patch("openviking_cli.setup_wizard._stdin_stdout_tty", return_value=True),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._prompt_api_key") as mock_prompt,
        ):
            value = _prompt_api_key_with_env(["ARK_API_KEY"])
        assert value == "env-key-1234567890"
        mock_prompt.assert_not_called()

    def test_falls_back_to_prompt_when_declined(self):
        with (
            patch.dict(os.environ, {"ARK_API_KEY": "env-key-1234567890"}, clear=False),
            patch("openviking_cli.setup_wizard._stdin_stdout_tty", return_value=True),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=False),
            patch("openviking_cli.setup_wizard._prompt_api_key", return_value="typed-key"),
        ):
            assert _prompt_api_key_with_env(["ARK_API_KEY"]) == "typed-key"

    def test_skips_env_shortcut_when_not_tty(self):
        with (
            patch.dict(os.environ, {"ARK_API_KEY": "env-key-1234567890"}, clear=False),
            patch("openviking_cli.setup_wizard._stdin_stdout_tty", return_value=False),
            patch("openviking_cli.setup_wizard._prompt_confirm") as mock_confirm,
            patch("openviking_cli.setup_wizard._prompt_api_key", return_value="typed-key"),
        ):
            assert _prompt_api_key_with_env(["ARK_API_KEY"]) == "typed-key"
        mock_confirm.assert_not_called()


# ---------------------------------------------------------------------------
# Partial reconfiguration of an existing config
# ---------------------------------------------------------------------------


def _existing_config() -> dict:
    return {
        "storage": {"workspace": "/tmp/ov"},
        "embedding": {"dense": {"provider": "ollama", "model": "qwen", "dimension": 1024}},
        "vlm": {"provider": "openai", "model": "old-vlm", "api_base": "https://old"},
        "server": {"host": "127.0.0.1"},
    }


class TestPartialUpdate:
    def test_update_vlm_only(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config_path.write_text(json.dumps(_existing_config()), encoding="utf-8")
        new_vlm = {"provider": "kimi", "model": "kimi-code", "api_base": "https://new"}

        with (
            patch("openviking_cli.setup_wizard._prompt_cloud_vlm", return_value=(new_vlm, None)),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._post_save_actions", return_value=None),
            patch("builtins.print"),
        ):
            assert _update_existing_config(config_path, "vlm") == 0

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["vlm"] == new_vlm
        assert data["embedding"] == _existing_config()["embedding"]  # untouched
        assert (tmp_path / "ov.conf.bak").exists()  # old config backed up

    def test_update_server_only(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config_path.write_text(json.dumps(_existing_config()), encoding="utf-8")

        with (
            patch(
                "openviking_cli.setup_wizard._wizard_server",
                return_value={"host": "0.0.0.0", "root_api_key": "rk"},
            ),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._post_save_actions", return_value=None),
            patch("builtins.print"),
        ):
            assert _update_existing_config(config_path, "server") == 0

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["server"] == {"host": "0.0.0.0", "root_api_key": "rk"}
        assert data["vlm"] == _existing_config()["vlm"]

    def test_embedding_dimension_change_requires_confirmation(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        original = _existing_config()
        config_path.write_text(json.dumps(original), encoding="utf-8")
        new_dense = {"provider": "local", "model": "bge-small-zh-v1.5-f16", "dimension": 512}

        with (
            patch(
                "openviking_cli.setup_wizard._prompt_embedding_flow",
                return_value=(new_dense, None),
            ),
            # Decline the dimension-change warning → nothing written.
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=False),
            patch("builtins.print"),
        ):
            assert _update_existing_config(config_path, "embedding") == 0

        assert json.loads(config_path.read_text(encoding="utf-8")) == original

    def test_unreadable_config_returns_error(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config_path.write_text("{not json", encoding="utf-8")
        with patch("builtins.print"):
            assert _update_existing_config(config_path, "vlm") == 1


class TestRunInitExistingConfigMenu:
    def test_dispatches_partial_update(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        config_path.write_text(json.dumps(_existing_config()), encoding="utf-8")
        with (
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=2),
            patch(
                "openviking_cli.setup_wizard._update_existing_config", return_value=0
            ) as mock_update,
            patch("builtins.print"),
        ):
            assert run_init() == 0
        mock_update.assert_called_once_with(config_path, "vlm")

    def test_cancel_leaves_config_untouched(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        original = json.dumps(_existing_config())
        config_path.write_text(original, encoding="utf-8")
        with (
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=5),
            patch("builtins.print"),
        ):
            assert run_init() == 0
        assert config_path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Two-step setup: embedding and VLM chosen independently
# ---------------------------------------------------------------------------


class TestTwoStepWizard:
    def test_mixed_local_embedding_cloud_vlm(self):
        dense = {"provider": "ollama", "model": "qwen3-embedding:0.6b", "dimension": 1024}
        vlm = {"provider": "openai", "model": "gpt-5.4", "api_base": "https://api.openai.com/v1"}
        with (
            patch(
                "openviking_cli.setup_wizard._prompt_embedding_flow",
                return_value=(dense, True),
            ),
            patch("openviking_cli.setup_wizard._prompt_cloud_vlm", return_value=(vlm, None)),
            patch("builtins.print"),
        ):
            config, ollama_running = _wizard_two_step()

        assert config is not None
        assert config["embedding"]["dense"] == dense
        assert config["vlm"] == vlm
        assert config["storage"]["workspace"] == _workspace_path()
        # Ollama state from the embedding step survives a non-Ollama VLM step.
        assert ollama_running is True

    def test_skip_vlm_produces_embedding_only_config(self):
        dense = {"provider": "local", "model": "bge-small-zh-v1.5-f16", "dimension": 512}
        with (
            patch(
                "openviking_cli.setup_wizard._prompt_embedding_flow",
                return_value=(dense, None),
            ),
            patch(
                "openviking_cli.setup_wizard._prompt_cloud_vlm",
                return_value=(_SKIP_VLM, None),
            ),
            patch("builtins.print"),
        ):
            config, _ = _wizard_two_step()

        assert config is not None
        assert config["embedding"]["dense"] == dense
        assert "vlm" not in config

    def test_embedding_key_offered_for_vlm_reuse(self):
        dense = {
            "provider": "volcengine",
            "model": "doubao-embedding-vision-251215",
            "api_key": "ve-key",
            "dimension": 1024,
        }
        with (
            patch(
                "openviking_cli.setup_wizard._prompt_embedding_flow",
                return_value=(dense, None),
            ),
            patch(
                "openviking_cli.setup_wizard._prompt_cloud_vlm",
                return_value=({"provider": "volcengine", "model": "m"}, None),
            ) as mock_vlm,
            patch("builtins.print"),
        ):
            _wizard_two_step()

        mock_vlm.assert_called_once_with(
            allow_skip=True, reuse_key=("volcengine", "ve-key"), allow_back=True
        )

    def test_cancel_in_vlm_step_cancels_setup(self):
        dense = {"provider": "openai", "model": "text-embedding-3-small", "dimension": 1536}
        with (
            patch(
                "openviking_cli.setup_wizard._prompt_embedding_flow",
                return_value=(dense, None),
            ),
            patch("openviking_cli.setup_wizard._prompt_cloud_vlm", return_value=(None, None)),
            patch("builtins.print"),
        ):
            config, _ = _wizard_two_step()
        assert config is None


class TestVLMKeyReuse:
    def test_reuse_accepted_skips_key_prompt(self):
        from openviking_cli.setup_wizard import _prompt_cloud_vlm

        with (
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=3),  # OpenAI
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                return_value="gpt-5.4",
            ),
            patch("openviking_cli.setup_wizard._prompt_api_key_with_env") as mock_key,
            patch("builtins.print"),
        ):
            vlm_config, _ = _prompt_cloud_vlm(reuse_key=("openai", "shared-key"))

        assert vlm_config["api_key"] == "shared-key"
        mock_key.assert_not_called()

    def test_different_provider_prompts_normally(self):
        from openviking_cli.setup_wizard import _prompt_cloud_vlm

        with (
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=3),  # OpenAI
            patch("openviking_cli.setup_wizard._prompt_confirm") as mock_confirm,
            patch(
                "openviking_cli.setup_wizard._prompt_required_input",
                return_value="gpt-5.4",
            ),
            patch(
                "openviking_cli.setup_wizard._prompt_api_key_with_env",
                return_value="typed-key",
            ),
            patch("builtins.print"),
        ):
            vlm_config, _ = _prompt_cloud_vlm(reuse_key=("volcengine", "other-key"))

        assert vlm_config["api_key"] == "typed-key"
        mock_confirm.assert_not_called()


# ---------------------------------------------------------------------------
# Back navigation (← / [0] Back)
# ---------------------------------------------------------------------------


class TestBackNavigation:
    def test_numbered_fallback_accepts_zero_as_back(self):
        from openviking_cli.setup_wizard import _prompt_choice_numbered

        with patch("builtins.input", return_value="0"), patch("builtins.print"):
            assert _prompt_choice_numbered("Q:", [("A", ""), ("B", "")], allow_back=True) == 0

    def test_numbered_fallback_rejects_zero_without_back(self):
        with patch("builtins.input", side_effect=["0", "2"]), patch("builtins.print"):
            from openviking_cli.setup_wizard import _prompt_choice_numbered

            assert _prompt_choice_numbered("Q:", [("A", ""), ("B", "")]) == 2

    def test_embedding_flow_backs_out_of_backend_menu(self):
        with (
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=0),
            patch("builtins.print"),
        ):
            from openviking_cli.setup_wizard import _prompt_embedding_flow

            dense, _ = _prompt_embedding_flow(allow_back=True)
        assert dense is _GO_BACK

    def test_vlm_menu_backs_out(self):
        from openviking_cli.setup_wizard import _prompt_cloud_vlm

        with (
            patch("openviking_cli.setup_wizard._prompt_choice", return_value=0),
            patch("builtins.print"),
        ):
            vlm_config, _ = _prompt_cloud_vlm(allow_back=True)
        assert vlm_config is _GO_BACK

    def test_two_step_back_from_vlm_reruns_embedding(self):
        dense1 = {"provider": "ollama", "model": "first", "dimension": 1024}
        dense2 = {"provider": "ollama", "model": "second", "dimension": 1024}
        with (
            patch(
                "openviking_cli.setup_wizard._prompt_embedding_flow",
                side_effect=[(dense1, None), (dense2, None)],
            ),
            patch(
                "openviking_cli.setup_wizard._prompt_cloud_vlm",
                side_effect=[(_GO_BACK, None), (_SKIP_VLM, None)],
            ),
            patch("builtins.print"),
        ):
            config, _ = _wizard_two_step()

        assert config is not None
        assert config["embedding"]["dense"] == dense2

    def test_run_init_back_from_two_step_reshows_mode_menu(self, tmp_path):
        config_path = tmp_path / "ov.conf"
        ollama_config = {
            "embedding": {"dense": {"provider": "ollama", "model": "q", "dimension": 1024}},
            "storage": {"workspace": str(tmp_path / "data")},
        }
        with (
            patch.dict(os.environ, {"OPENVIKING_CONFIG_FILE": str(config_path)}, clear=False),
            # First pass picks step-by-step, which backs out; second pass picks
            # the recommended Ollama mode.
            patch("openviking_cli.setup_wizard._prompt_choice", side_effect=[1, 2]),
            patch(
                "openviking_cli.setup_wizard._wizard_two_step",
                return_value=(_GO_BACK, None),
            ),
            patch(
                "openviking_cli.setup_wizard._wizard_ollama",
                return_value=(ollama_config, True),
            ),
            patch("openviking_cli.setup_wizard._wizard_query_planner", return_value=None),
            patch(
                "openviking_cli.setup_wizard._wizard_server",
                return_value={"host": "127.0.0.1"},
            ),
            patch("openviking_cli.setup_wizard._prompt_confirm", return_value=True),
            patch("openviking_cli.setup_wizard._write_config", return_value=True) as mock_write,
            patch("openviking_cli.setup_wizard._post_save_actions", return_value=None),
            patch("builtins.print"),
        ):
            assert run_init() == 0
        mock_write.assert_called_once()
