# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the server preflight module."""

from __future__ import annotations

from unittest.mock import patch

from openviking.server.preflight import run_preflight_checks
from openviking_cli.utils.ollama import OllamaStartResult


class TestRunPreflightChecks:
    @patch("openviking_cli.utils.ollama.ensure_ollama_for_server")
    @patch("openviking_cli.utils.ollama.detect_ollama_in_config")
    def test_noop_when_ollama_not_configured(self, mock_detect, mock_ensure, capsys):
        mock_detect.return_value = (False, "localhost", 11434)

        run_preflight_checks(ov_config=object())

        mock_ensure.assert_not_called()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    @patch("openviking_cli.utils.ollama.ensure_ollama_for_server")
    @patch("openviking_cli.utils.ollama.detect_ollama_in_config")
    def test_ensures_ollama_when_configured(self, mock_detect, mock_ensure, capsys):
        mock_detect.return_value = (True, "localhost", 11434)
        mock_ensure.return_value = OllamaStartResult(success=True, message="running")

        run_preflight_checks(ov_config=object())

        mock_ensure.assert_called_once_with("localhost", 11434)
        captured = capsys.readouterr()
        assert "Ollama is running at localhost:11434" in captured.out

    @patch("openviking_cli.utils.ollama.ensure_ollama_for_server")
    @patch("openviking_cli.utils.ollama.detect_ollama_in_config")
    def test_reports_failure_without_crashing(self, mock_detect, mock_ensure, capsys):
        mock_detect.return_value = (True, "gpu-server", 11434)
        mock_ensure.return_value = OllamaStartResult(
            success=False,
            message="Ollama at gpu-server:11434 is not reachable.",
            stderr_output="boom",
        )

        run_preflight_checks(ov_config=object())

        captured = capsys.readouterr()
        assert "Warning: Ollama not available at gpu-server:11434" in captured.err
        assert "Ollama stderr: boom" in captured.err

    @patch("openviking_cli.utils.ollama.detect_ollama_in_config")
    def test_swallows_unexpected_exceptions(self, mock_detect, capsys):
        mock_detect.side_effect = RuntimeError("kaboom")

        run_preflight_checks(ov_config=object())

        captured = capsys.readouterr()
        assert "Warning: Ollama pre-flight check failed: kaboom" in captured.err
