# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""openviking-server doctor - validate OpenViking subsystems and report actionable diagnostics.

Unlike ``ov health`` (which pings a running server), ``openviking-server doctor`` checks
local prerequisites without requiring a server: config file, Python version,
native vector engine, AGFS, embedding provider, VLM provider, and disk space.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.vlm_config import VLMConfig

# ANSI helpers (disabled when stdout is not a terminal)
_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m" if _USE_COLOR else text


def _red(text: str) -> str:
    return f"\033[31m{text}\033[0m" if _USE_COLOR else text


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m" if _USE_COLOR else text


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if _USE_COLOR else text


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


def _find_config() -> Optional[Path]:
    return resolve_config_path(None, OPENVIKING_CONFIG_ENV, "ov.conf")


def _load_config_json(config_path: Path) -> Optional[dict]:
    """Parse ov.conf as JSON. Returns None if the file is unreadable or not valid JSON."""
    try:
        raw = config_path.read_text(encoding="utf-8-sig")
        raw = os.path.expandvars(raw)
        return json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None


def check_config() -> tuple[bool, str, Optional[str]]:
    """Validate ov.conf exists and is valid JSON with required sections."""
    config_path = _find_config()
    if config_path is None:
        return (
            False,
            "Configuration file not found",
            f"Create ~/.openviking/ov.conf or set {OPENVIKING_CONFIG_ENV}",
        )

    try:
        raw = config_path.read_text(encoding="utf-8-sig")
        raw = os.path.expandvars(raw)
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"Invalid JSON in {config_path}", f"Fix syntax error: {exc}"

    missing = [key for key in () if key not in data]
    if missing:
        return (
            False,
            f"{config_path} missing required sections: {', '.join(missing)}",
            "Add the missing sections (see examples/ov.conf.example)",
        )

    return True, str(config_path), None


def check_python() -> tuple[bool, str, Optional[str]]:
    """Verify Python >= 3.10."""
    version = sys.version_info
    version_str = f"{version[0]}.{version[1]}.{version[2]}"
    if version >= (3, 10):
        return True, f"{version_str} (>= 3.10 required)", None
    return (
        False,
        f"{version_str} (>= 3.10 required)",
        "Upgrade Python to 3.10 or later",
    )


def check_native_engine() -> tuple[bool, str, Optional[str]]:
    """Check if the native vector engine (PersistStore) is available."""
    try:
        from openviking.storage.vectordb.engine import (
            AVAILABLE_ENGINE_VARIANTS,
            ENGINE_VARIANT,
        )
    except ImportError as exc:
        return (
            False,
            f"Cannot import engine module: {exc}",
            "pip install openviking --upgrade --force-reinstall",
        )

    if ENGINE_VARIANT == "unavailable":
        variants = ", ".join(AVAILABLE_ENGINE_VARIANTS) if AVAILABLE_ENGINE_VARIANTS else "none"
        machine = platform.machine()
        return (
            False,
            f"No compatible engine variant (platform: {machine}, packaged: {variants})",
            'pip install openviking --upgrade --force-reinstall\n  Alt: Use vectordb.backend = "volcengine" instead of "local"',
        )

    return True, f"variant={ENGINE_VARIANT}", None


def check_agfs() -> tuple[bool, str, Optional[str]]:
    """Verify the bundled OpenViking AGFS client loads."""
    try:
        pyagfs = importlib.import_module("openviking.pyagfs")

        version = getattr(pyagfs, "__version__", "unknown")
        return True, f"AGFS SDK {version}", None
    except ImportError:
        return (
            False,
            "Bundled AGFS client not found",
            "pip install openviking --upgrade --force-reinstall",
        )


def _embedding_probe_label(provider: str, model: str, dense: dict[str, Any]) -> str:
    api_base = dense.get("api_base")
    dimension = dense.get("dimension")
    parts = [f"{provider}/{model}"]
    if api_base:
        parts.append(f"api_base={api_base}")
    if dimension:
        parts.append(f"dimension={dimension}")
    return " ".join(parts)


def _probe_embedding_provider(
    embedding: dict[str, Any],
    dense: dict[str, Any],
) -> tuple[bool, str, Optional[str]]:
    """Create the configured embedder, make one request, and validate vector shape."""
    from openviking.models.embedder.base import embed_compat
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig

    provider = dense.get("provider", "local")
    model = dense.get("model", "bge-small-zh-v1.5-f16")
    label = _embedding_probe_label(provider, model, dense)

    try:
        model_config = EmbeddingModelConfig(**dense)
        config_kwargs = {
            key: embedding[key]
            for key in ("max_concurrent", "max_retries", "text_source", "max_input_tokens")
            if key in embedding
        }
        embedding_config = EmbeddingConfig(dense=model_config, **config_kwargs)
        embedder = embedding_config.get_embedder()
        expected_dimension = (
            model_config.dimension
            if model_config.dimension is not None
            else embedder.get_dimension()
        )
    except Exception as exc:
        return (
            False,
            f"{label} (invalid embedding config: {exc})",
            "Fix embedding.dense provider/model/api_base/dimension in ov.conf",
        )

    async def _run_probe():
        return await asyncio.wait_for(
            embed_compat(embedder, "OpenViking doctor embedding probe", is_query=True),
            timeout=10.0,
        )

    try:
        result = asyncio.run(_run_probe())
    except TimeoutError:
        return (
            False,
            f"{label} (probe timed out)",
            "Check embedding.dense.api_base and make sure the embedding provider is reachable",
        )
    except Exception as exc:
        return (
            False,
            f"{label} (probe failed: {exc})",
            "Check the embedding provider service, model name, API key, and api_base",
        )

    vector = getattr(result, "dense_vector", None)
    if not vector:
        return (
            False,
            f"{label} (probe returned no dense vector)",
            "Use a dense embedding model or configure embedding.dense correctly",
        )

    actual_dimension = len(vector)
    if actual_dimension != expected_dimension:
        return (
            False,
            f"{label} (dimension mismatch: expected {expected_dimension}, got {actual_dimension})",
            "Set embedding.dense.dimension to match the provider output, or use a matching model/index",
        )

    return True, f"{label} probe ok (dimension={actual_dimension})", None


def check_embedding() -> tuple[bool, str, Optional[str]]:
    """Load embedding config and verify provider connectivity."""
    config_path = _find_config()
    if config_path is None:
        return False, "Cannot check (no config file)", None

    data = _load_config_json(config_path)
    if data is None:
        return False, "Cannot check (config unreadable)", None

    embedding = data.get("embedding", {}) or {}
    dense = embedding.get("dense", {}) or {}
    provider = dense.get("provider", "local")
    model = dense.get("model", "bge-small-zh-v1.5-f16")

    if provider == "local":
        from openviking.models.embedder.local_embedders import (
            get_local_model_cache_path,
            get_local_model_spec,
        )

        try:
            get_local_model_spec(model)
        except ValueError as exc:
            return (
                False,
                f"{provider}/{model} (unsupported local model)",
                str(exc),
            )

        try:
            importlib.import_module("llama_cpp")
        except ImportError:
            return (
                False,
                f"{provider}/{model} (missing llama-cpp-python)",
                'pip install "openviking[local-embed]"',
            )

        model_path = dense.get("model_path", "")
        cache_dir = Path(dense.get("cache_dir", "~/.cache/openviking/models")).expanduser()
        if model_path:
            if not Path(model_path).expanduser().exists():
                return (
                    False,
                    f"{provider}/{model} (model_path missing)",
                    f"Download the GGUF model to {Path(model_path).expanduser()} or update embedding.dense.model_path",
                )
            return True, f"{provider}/{model} ({Path(model_path).expanduser()})", None

        cached_file = get_local_model_cache_path(model, str(cache_dir))
        if cached_file.exists():
            return True, f"{provider}/{model} ({cached_file})", None
        return (
            True,
            f"{provider}/{model} (will auto-download during startup initialization)",
            None,
        )

    api_key = dense.get("api_key", "")
    api_base = dense.get("api_base", "")
    if provider != "ollama" and not api_base and (not api_key or api_key.startswith("{")):
        return (
            False,
            f"{provider}/{model} (no API key)",
            "Set embedding.dense.api_key in ov.conf",
        )

    return _probe_embedding_provider(embedding, dense)


def check_vlm() -> tuple[bool, str, Optional[str]]:
    """Load VLM config and verify it's configured."""
    config_path = _find_config()
    if config_path is None:
        return False, "Cannot check (no config file)", None

    data = _load_config_json(config_path)
    if data is None:
        return False, "Cannot check (config unreadable)", None

    raw_vlm = data.get("vlm", {})
    normalized_vlm = VLMConfig.sync_provider_backend(dict(raw_vlm))
    vlm = VLMConfig.model_construct(**normalized_vlm)
    _, provider = vlm.get_provider_config()
    model = vlm.model or ""

    if not provider:
        return False, "No VLM provider configured", "Add vlm section to ov.conf"

    if provider == "openai-codex":
        api_key = vlm._get_effective_api_key()
        if api_key and not api_key.startswith("{"):
            return True, f"openai-codex/{model} (explicit api_key)", None

        importlib.import_module("openviking.models.vlm")
        codex_auth = importlib.import_module("openviking.models.vlm.backends.codex_auth")

        try:
            creds = codex_auth.resolve_codex_runtime_credentials()
            source = creds.get("source", "unknown")
            return True, f"openai-codex/{model} (oauth via {source})", None
        except Exception as exc:
            status = codex_auth.get_codex_auth_status()
            store_path = status.get("store_path") or "~/.openviking/codex_auth.json"
            bootstrap_path = status.get("bootstrap_path") or "~/.codex/auth.json"
            return (
                False,
                f"openai-codex/{model} ({exc})",
                "Run `openviking-server init` and choose `OpenAI Codex` to create OV-owned auth state\n"
                f"Or bootstrap once from {bootstrap_path} into {store_path}",
            )

    # Ollama via LiteLLM doesn't need a real API key
    if provider == "litellm" and model.startswith("ollama/"):
        return True, f"{provider}/{model}", None

    api_key = vlm._get_effective_api_key()
    if not api_key or api_key.startswith("{"):
        return (
            False,
            f"{provider}/{model} (no API key)",
            "Set vlm.api_key in ov.conf",
        )

    return True, f"{provider}/{model}", None


def check_ollama() -> tuple[bool, str, Optional[str]]:
    """Check Ollama connectivity if the config uses an Ollama provider."""
    config_path = _find_config()
    if config_path is None:
        return True, "not configured", None

    data = _load_config_json(config_path)
    if data is None:
        return True, "not configured", None

    # Detect whether config uses Ollama
    dense = data.get("embedding", {}).get("dense", {})
    vlm = data.get("vlm", {})
    query_planner = data.get("query_planner") or {}
    uses_embedding = dense.get("provider") == "ollama"
    uses_vlm = vlm.get("provider") == "litellm" and (vlm.get("model", "")).startswith("ollama/")
    uses_query_planner = query_planner.get("provider") == "litellm" and (
        query_planner.get("model", "")
    ).startswith("ollama/")

    if not uses_embedding and not uses_vlm and not uses_query_planner:
        return True, "not configured", None

    from openviking_cli.utils.ollama import check_ollama_running, parse_ollama_url

    # Determine host/port from config (embedding -> vlm -> query_planner)
    if uses_embedding:
        host, port = parse_ollama_url(dense.get("api_base"))
    elif uses_vlm:
        host, port = parse_ollama_url(vlm.get("api_base"))
    else:
        host, port = parse_ollama_url(query_planner.get("api_base"))

    if check_ollama_running(host, port):
        return True, f"running at {host}:{port}", None

    return (
        False,
        f"unreachable at {host}:{port}",
        "Run 'ollama serve' or check your Ollama configuration",
    )


def check_disk() -> tuple[bool, str, Optional[str]]:
    """Check free disk space in the workspace directory."""
    config_path = _find_config()
    workspace = Path.home() / ".openviking"

    if config_path:
        data = _load_config_json(config_path)
        if data is not None:
            ws = data.get("storage", {}).get("workspace", "")
            if ws:
                workspace = Path(ws).expanduser()

    check_path = workspace if workspace.exists() else Path.home()

    usage = shutil.disk_usage(check_path)
    free_gb = usage.free / (1024**3)

    if free_gb < 1.0:
        return (
            False,
            f"{free_gb:.1f} GB free in {check_path}",
            "Free up disk space (OpenViking needs at least 1 GB for vector storage)",
        )

    return True, f"{free_gb:.1f} GB free in {check_path}", None


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

_CHECKS = [
    ("Config", check_config),
    ("Python", check_python),
    ("Native Engine", check_native_engine),
    ("AGFS", check_agfs),
    ("Embedding", check_embedding),
    ("VLM", check_vlm),
    ("Ollama", check_ollama),
    ("Disk", check_disk),
]


def run_doctor() -> int:
    """Run all diagnostic checks and print a formatted report.

    Returns 0 if all checks pass, 1 otherwise.
    """
    print("\nOpenViking Doctor\n")

    failed = 0
    max_label = max(len(label) for label, _ in _CHECKS)

    for label, check_fn in _CHECKS:
        try:
            ok, detail, fix = check_fn()
        except Exception as exc:
            ok, detail, fix = False, f"Unexpected error: {type(exc).__name__}: {exc}", None

        pad = " " * (max_label - len(label) + 1)
        if ok:
            status = _green("PASS")
            print(f"  {label}:{pad}{status}  {detail}")
        else:
            status = _red("FAIL")
            print(f"  {label}:{pad}{status}  {detail}")
            failed += 1
            if fix:
                for line in fix.split("\n"):
                    print(f"  {' ' * (max_label + 2)}{_dim('Fix: ' + line)}")

    print()
    if failed:
        print(f"  {_red(f'{failed} check(s) failed.')} See above for fix suggestions.\n")
        return 1

    print(f"  {_green('All checks passed.')}\n")
    return 0


def main() -> int:
    """Entry point for ``openviking-server doctor``."""
    return run_doctor()
