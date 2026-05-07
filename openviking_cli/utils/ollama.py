"""Shared Ollama utilities for OpenViking.

Used by both the ``openviking-server init`` setup wizard and the ``openviking-server``
bootstrap to detect, start, and health-check a local Ollama instance.

Design principle: **ensure running, never stop** — Ollama is a shared
service that other tools may depend on.  We start it if needed but never
tear it down on exit.

stdlib-only (no third-party dependencies).
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLLAMA_DEFAULT_HOST = "localhost"
OLLAMA_DEFAULT_PORT = 11434

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class OllamaStartResult:
    """Result of attempting to start / ensure Ollama is available."""

    success: bool
    stderr_output: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


def parse_ollama_url(api_base: str | None) -> tuple[str, int]:
    """Extract ``(host, port)`` from an Ollama *api_base* URL.

    Handles forms like ``http://localhost:11434/v1`` or
    ``http://gpu-server:11434``.  Falls back to defaults when *api_base*
    is ``None`` or unparseable.
    """
    if not api_base:
        return OLLAMA_DEFAULT_HOST, OLLAMA_DEFAULT_PORT
    try:
        parsed = urllib.parse.urlparse(api_base)
        host = parsed.hostname or OLLAMA_DEFAULT_HOST
        port = parsed.port or OLLAMA_DEFAULT_PORT
        return host, port
    except Exception:
        return OLLAMA_DEFAULT_HOST, OLLAMA_DEFAULT_PORT


# ---------------------------------------------------------------------------
# Ollama detection / health
# ---------------------------------------------------------------------------


def check_ollama_running(
    host: str = OLLAMA_DEFAULT_HOST,
    port: int = OLLAMA_DEFAULT_PORT,
) -> bool:
    """Return ``True`` if Ollama is responding at *host*:*port*."""
    try:
        url = f"http://{host}:{port}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def get_ollama_models(
    host: str = OLLAMA_DEFAULT_HOST,
    port: int = OLLAMA_DEFAULT_PORT,
) -> list[str]:
    """Fetch names of locally available Ollama models."""
    try:
        url = f"http://{host}:{port}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return [m["name"] for m in data.get("models", [])]
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, KeyError):
        return []


def is_model_available(model_name: str, available: list[str]) -> bool:
    """Check if *model_name* is available locally (prefix match for tag variants)."""
    for m in available:
        if m == model_name or m.startswith(model_name + "-"):
            return True
        # model_name without tag matches model with ":latest"
        if ":" not in model_name and m.split(":")[0] == model_name:
            return True
    return False


# ---------------------------------------------------------------------------
# Ollama installation & startup
# ---------------------------------------------------------------------------


def ollama_pull_model(model_name: str) -> bool:
    """Pull an Ollama model via CLI (shows native progress bar)."""
    try:
        result = subprocess.run(["ollama", "pull", model_name], check=False)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def is_ollama_installed() -> bool:
    """Check if the ``ollama`` CLI binary is on PATH."""
    return shutil.which("ollama") is not None


def install_ollama() -> bool:
    """Install Ollama automatically based on the current platform."""
    system = platform.system()

    if system == "Darwin":
        if shutil.which("brew"):
            result = subprocess.run(["brew", "install", "ollama"], check=False)
            if result.returncode == 0:
                return True
        result = subprocess.run(
            ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            check=False,
        )
        return result.returncode == 0

    elif system == "Linux":
        result = subprocess.run(
            ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            check=False,
        )
        return result.returncode == 0

    return False


def start_ollama(
    host: str = OLLAMA_DEFAULT_HOST,
    port: int = OLLAMA_DEFAULT_PORT,
) -> OllamaStartResult:
    """Start ``ollama serve`` in the background and wait for it to be ready.

    Unlike the old fire-and-forget approach, stderr is captured so that
    failure reasons are visible to the caller.

    Returns an :class:`OllamaStartResult` with ``success`` and any
    ``stderr_output`` on failure.
    """
    # Already running?
    if check_ollama_running(host, port):
        return OllamaStartResult(success=True, message="already running")

    stderr_file = tempfile.TemporaryFile(mode="w+")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
        )
    except FileNotFoundError:
        stderr_file.close()
        return OllamaStartResult(
            success=False,
            message="ollama command not found",
        )

    # Poll up to 15 seconds for readiness
    for _ in range(30):
        time.sleep(0.5)
        if check_ollama_running(host, port):
            stderr_file.close()
            return OllamaStartResult(success=True, message="started")

    # Timeout — read stderr for diagnostics
    stderr_output = ""
    try:
        stderr_file.seek(0)
        stderr_output = stderr_file.read()
    except Exception:
        pass
    finally:
        stderr_file.close()

    return OllamaStartResult(
        success=False,
        stderr_output=stderr_output,
        message="timeout waiting for Ollama to become ready",
    )


# ---------------------------------------------------------------------------
# Config detection
# ---------------------------------------------------------------------------


def detect_ollama_in_config(config) -> tuple[bool, str, int]:
    """Detect whether *config* uses Ollama and return ``(uses_ollama, host, port)``.

    *config* is an :class:`OpenVikingConfig` instance (imported lazily to
    avoid circular deps).

    Detection rules:
    - ``embedding.dense.provider == "ollama"``
    - ``vlm.provider == "litellm"`` **and** ``vlm.model`` starts with ``"ollama/"``
    """
    host, port = OLLAMA_DEFAULT_HOST, OLLAMA_DEFAULT_PORT
    uses_ollama = False

    # Check embedding
    dense = getattr(config.embedding, "dense", None)
    if dense is not None and getattr(dense, "provider", None) == "ollama":
        uses_ollama = True
        api_base = getattr(dense, "api_base", None)
        host, port = parse_ollama_url(api_base)

    # Check VLM
    vlm = getattr(config, "vlm", None)
    if vlm is not None:
        vlm_provider = getattr(vlm, "provider", None)
        vlm_model = getattr(vlm, "model", None) or ""
        if vlm_provider == "litellm" and vlm_model.startswith("ollama/"):
            if not uses_ollama:
                # Only use VLM's URL if embedding didn't already set it
                api_base = getattr(vlm, "api_base", None)
                host, port = parse_ollama_url(api_base)
            uses_ollama = True

    return uses_ollama, host, port


# ---------------------------------------------------------------------------
# Server-oriented ensure (non-interactive)
# ---------------------------------------------------------------------------


def ensure_ollama_for_server(
    host: str = OLLAMA_DEFAULT_HOST,
    port: int = OLLAMA_DEFAULT_PORT,
) -> OllamaStartResult:
    """Ensure Ollama is available — non-interactive, for server startup.

    - Already running → success.
    - Remote host (not localhost) → only probe, never attempt local start.
    - Not installed → warn, return failure (no interactive install).
    - Installed but not running → ``ollama serve`` with stderr capture.
    """
    # 1. Already running?
    if check_ollama_running(host, port):
        return OllamaStartResult(success=True, message=f"running at {host}:{port}")

    # 2. Remote host — can't start locally
    if host not in _LOCAL_HOSTS:
        return OllamaStartResult(
            success=False,
            message=f"Ollama at {host}:{port} is not reachable. Cannot auto-start remote Ollama.",
        )

    # 3. Not installed?
    if not is_ollama_installed():
        return OllamaStartResult(
            success=False,
            message="ollama is not installed. Install from https://ollama.com/download",
        )

    # 4. Installed but not running — start it
    return start_ollama(host, port)
