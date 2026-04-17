# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Environment preflight checks for OpenViking HTTP Server.

Encapsulates startup-time environment detection (Ollama availability, etc.)
so that ``bootstrap.py`` can stay focused on argument parsing and server
lifecycle. New environment checks should be added as additional internal
helpers invoked from :func:`run_preflight_checks`.
"""

from __future__ import annotations

import sys


def _check_ollama(ov_config) -> None:
    from openviking_cli.utils.ollama import detect_ollama_in_config, ensure_ollama_for_server

    uses_ollama, ollama_host, ollama_port = detect_ollama_in_config(ov_config)
    if not uses_ollama:
        return

    result = ensure_ollama_for_server(ollama_host, ollama_port)
    if result.success:
        print(f"Ollama is running at {ollama_host}:{ollama_port}")
    else:
        print(
            f"Warning: Ollama not available at {ollama_host}:{ollama_port}. "
            f"Embedding/VLM may fail. ({result.message})",
            file=sys.stderr,
        )
        if result.stderr_output:
            print(f"  Ollama stderr: {result.stderr_output}", file=sys.stderr)


def run_preflight_checks(ov_config) -> None:
    """Run all server-startup environment checks.

    *ov_config* is an :class:`OpenVikingConfig` instance (typically obtained
    via ``OpenVikingConfigSingleton.get_instance()``).
    """
    try:
        _check_ollama(ov_config)
    except Exception as e:
        print(f"Warning: Ollama pre-flight check failed: {e}", file=sys.stderr)
