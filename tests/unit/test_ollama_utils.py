"""Tests for the shared Ollama utility module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openviking_cli.utils.ollama import (
    OllamaStartResult,
    check_ollama_running,
    detect_ollama_in_config,
    ensure_ollama_for_server,
    parse_ollama_url,
    start_ollama,
)


# ---------------------------------------------------------------------------
# parse_ollama_url
# ---------------------------------------------------------------------------


class TestParseOllamaUrl:
    def test_localhost_with_v1_suffix(self):
        assert parse_ollama_url("http://localhost:11434/v1") == ("localhost", 11434)

    def test_custom_host(self):
        assert parse_ollama_url("http://gpu-server:11434") == ("gpu-server", 11434)

    def test_ip_address(self):
        assert parse_ollama_url("http://192.168.1.100:11434/v1") == ("192.168.1.100", 11434)

    def test_custom_port(self):
        assert parse_ollama_url("http://localhost:8080") == ("localhost", 8080)

    def test_none_returns_defaults(self):
        assert parse_ollama_url(None) == ("localhost", 11434)

    def test_empty_returns_defaults(self):
        assert parse_ollama_url("") == ("localhost", 11434)


# ---------------------------------------------------------------------------
# detect_ollama_in_config
# ---------------------------------------------------------------------------


def _make_config(
    embedding_provider="volcengine",
    embedding_api_base=None,
    vlm_provider="volcengine",
    vlm_model="doubao-seed",
    vlm_api_base=None,
):
    """Build a minimal config-like object for detect_ollama_in_config."""
    dense = SimpleNamespace(provider=embedding_provider, api_base=embedding_api_base)
    embedding = SimpleNamespace(dense=dense)
    vlm = SimpleNamespace(provider=vlm_provider, model=vlm_model, api_base=vlm_api_base)
    return SimpleNamespace(embedding=embedding, vlm=vlm)


class TestDetectOllamaInConfig:
    def test_embedding_ollama_detected(self):
        config = _make_config(
            embedding_provider="ollama",
            embedding_api_base="http://localhost:11434/v1",
        )
        uses, host, port = detect_ollama_in_config(config)
        assert uses is True
        assert host == "localhost"
        assert port == 11434

    def test_vlm_ollama_detected(self):
        config = _make_config(
            vlm_provider="litellm",
            vlm_model="ollama/gemma4:e4b",
            vlm_api_base="http://localhost:11434",
        )
        uses, host, port = detect_ollama_in_config(config)
        assert uses is True
        assert host == "localhost"
        assert port == 11434

    def test_both_detected_uses_embedding_url(self):
        config = _make_config(
            embedding_provider="ollama",
            embedding_api_base="http://gpu-server:11434/v1",
            vlm_provider="litellm",
            vlm_model="ollama/qwen3.5:9b",
            vlm_api_base="http://localhost:11434",
        )
        uses, host, port = detect_ollama_in_config(config)
        assert uses is True
        assert host == "gpu-server"  # embedding takes priority

    def test_neither_detected(self):
        config = _make_config()
        uses, host, port = detect_ollama_in_config(config)
        assert uses is False

    def test_litellm_non_ollama_model(self):
        config = _make_config(vlm_provider="litellm", vlm_model="anthropic/claude-3")
        uses, _, _ = detect_ollama_in_config(config)
        assert uses is False

    def test_custom_api_base(self):
        config = _make_config(
            embedding_provider="ollama",
            embedding_api_base="http://192.168.1.50:8080/v1",
        )
        uses, host, port = detect_ollama_in_config(config)
        assert uses is True
        assert host == "192.168.1.50"
        assert port == 8080


# ---------------------------------------------------------------------------
# ensure_ollama_for_server
# ---------------------------------------------------------------------------


class TestEnsureOllamaForServer:
    @patch("openviking_cli.utils.ollama.check_ollama_running", return_value=True)
    def test_already_running(self, mock_check):
        result = ensure_ollama_for_server()
        assert result.success is True
        assert "running" in result.message

    @patch("openviking_cli.utils.ollama.check_ollama_running", return_value=False)
    def test_remote_host_unreachable(self, mock_check):
        result = ensure_ollama_for_server(host="gpu-server", port=11434)
        assert result.success is False
        assert "remote" in result.message.lower()

    @patch("openviking_cli.utils.ollama.check_ollama_running", return_value=False)
    @patch("openviking_cli.utils.ollama.is_ollama_installed", return_value=False)
    def test_not_installed(self, mock_installed, mock_check):
        result = ensure_ollama_for_server()
        assert result.success is False
        assert "not installed" in result.message

    @patch("openviking_cli.utils.ollama.start_ollama")
    @patch("openviking_cli.utils.ollama.is_ollama_installed", return_value=True)
    @patch("openviking_cli.utils.ollama.check_ollama_running", return_value=False)
    def test_installed_starts_ollama(self, mock_check, mock_installed, mock_start):
        mock_start.return_value = OllamaStartResult(success=True, message="started")
        result = ensure_ollama_for_server()
        assert result.success is True
        mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# start_ollama
# ---------------------------------------------------------------------------


class TestStartOllama:
    @patch("openviking_cli.utils.ollama.check_ollama_running", return_value=True)
    def test_already_running_returns_success(self, mock_check):
        result = start_ollama()
        assert result.success is True
        assert result.message == "already running"

    @patch("openviking_cli.utils.ollama.check_ollama_running", return_value=False)
    @patch("subprocess.Popen", side_effect=FileNotFoundError)
    def test_command_not_found(self, mock_popen, mock_check):
        result = start_ollama()
        assert result.success is False
        assert "not found" in result.message

    @patch("openviking_cli.utils.ollama.check_ollama_running")
    @patch("subprocess.Popen")
    @patch("openviking_cli.utils.ollama.time.sleep")
    def test_start_success(self, mock_sleep, mock_popen, mock_check):
        # First call: not running (initial check), second call onward: running
        mock_check.side_effect = [False, False, True]
        mock_popen.return_value = MagicMock()

        result = start_ollama()
        assert result.success is True
        assert result.message == "started"

    @patch("openviking_cli.utils.ollama.check_ollama_running", return_value=False)
    @patch("subprocess.Popen")
    @patch("openviking_cli.utils.ollama.time.sleep")
    def test_start_timeout(self, mock_sleep, mock_popen, mock_check):
        mock_popen.return_value = MagicMock()

        result = start_ollama()
        assert result.success is False
        assert "timeout" in result.message.lower()
