# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""openviking-server doctor - validate OpenViking subsystems and report actionable diagnostics.

Unlike ``ov health`` (which pings a running server), ``openviking-server doctor`` checks
local prerequisites without requiring a server: config file, Python version,
native vector engine, AGFS, embedding provider, VLM provider, and disk space.
"""

from __future__ import annotations

import argparse
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
        raw = config_path.read_text(encoding="utf-8")
        raw = os.path.expandvars(raw)
        return json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None


def _is_placeholder_secret(value: Any) -> bool:
    return not value or (isinstance(value, str) and value.startswith("{"))


def _configured_dimension(dense: dict[str, Any]) -> Optional[int]:
    value = dense.get("dimension")
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_dense_embedder_for_live_validation(dense: dict[str, Any]):
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig

    dense_config = EmbeddingModelConfig.model_validate(dense)
    embedding_config = EmbeddingConfig.model_validate({"dense": dense_config.model_dump()})
    return embedding_config.get_embedder()


def _get_local_embedding_helpers():
    from openviking.models.embedder.local_embedders import (
        get_local_model_cache_path,
        get_local_model_spec,
    )

    return get_local_model_cache_path, get_local_model_spec


def _format_live_embedding_fix(provider: str, dense: dict[str, Any], exc: Exception) -> str:
    provider_label = provider or "embedding"
    api_base = dense.get("api_base")
    model = dense.get("model")
    lines = [f"Check embedding.dense.model for {provider_label} in ov.conf"]
    if api_base:
        lines.append(f"Verify embedding.dense.api_base is reachable: {api_base}")
    if model:
        lines.append(f"Confirm model '{model}' exists and supports embeddings")
    lines.append(f"Provider error: {type(exc).__name__}: {exc}")
    return "\n".join(lines)


def _run_live_embedding_validation(
    provider: str,
    model: str,
    dense: dict[str, Any],
) -> tuple[bool, str, Optional[str]]:
    embedder = None
    try:
        embedder = _build_dense_embedder_for_live_validation(dense)
        result = embedder.embed("OpenViking doctor validation probe", is_query=False)
        vector = getattr(result, "dense_vector", None)
        if not vector:
            return (
                False,
                f"{provider}/{model} (live validation returned no dense vector)",
                "Check embedding provider credentials, endpoint, and model configuration in ov.conf",
            )

        actual_dimension = len(vector)
        configured_dimension = _configured_dimension(dense)
        if configured_dimension is not None and configured_dimension != actual_dimension:
            return (
                False,
                (
                    f"{provider}/{model} "
                    f"(live dimension mismatch: config={configured_dimension}, actual={actual_dimension})"
                ),
                (
                    "Update embedding.dense.dimension to match the provider output "
                    f"({actual_dimension}) or switch to a model that returns {configured_dimension} dimensions"
                ),
            )

        detail = f"{provider}/{model} (live OK, dim={actual_dimension})"
        if configured_dimension is not None:
            detail = f"{provider}/{model} (live OK, dim={actual_dimension} matches config)"
        return True, detail, None
    except Exception as exc:
        return (
            False,
            f"{provider}/{model} (live validation failed: {type(exc).__name__})",
            _format_live_embedding_fix(provider, dense, exc),
        )
    finally:
        if embedder is not None:
            close = getattr(embedder, "close", None)
            if callable(close):
                close()


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
        raw = config_path.read_text(encoding="utf-8")
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


def check_embedding(live: bool = False) -> tuple[bool, str, Optional[str]]:
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
        get_local_model_cache_path, get_local_model_spec = _get_local_embedding_helpers()

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
            (
                f"{provider}/{model} "
                "(will auto-download during startup initialization)"
            ),
            None,
        )

    # Ollama doesn't need an API key
    if provider == "ollama":
        if not live:
            return True, f"{provider}/{model}", None
        return _run_live_embedding_validation(provider, model, dense)

    api_key = dense.get("api_key", "")
    if _is_placeholder_secret(api_key):
        return (
            False,
            f"{provider}/{model} (no API key)",
            "Set embedding.dense.api_key in ov.conf",
        )

    if live:
        return _run_live_embedding_validation(provider, model, dense)

    return True, f"{provider}/{model}", None


def check_vlm() -> tuple[bool, str, Optional[str]]:
    """Load VLM config and verify it's configured."""
    config_path = _find_config()
    if config_path is None:
        return False, "Cannot check (no config file)", None

    data = _load_config_json(config_path)
    if data is None:
        return False, "Cannot check (config unreadable)", None

    vlm = data.get("vlm", {})
    provider = vlm.get("provider", "")
    model = vlm.get("model", "")

    if not provider:
        return False, "No VLM provider configured", "Add vlm section to ov.conf"

    # Ollama via LiteLLM doesn't need a real API key
    if provider == "litellm" and model.startswith("ollama/"):
        return True, f"{provider}/{model}", None

    api_key = vlm.get("api_key", "")
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
    uses_embedding = dense.get("provider") == "ollama"
    uses_vlm = vlm.get("provider") == "litellm" and (vlm.get("model", "")).startswith("ollama/")

    if not uses_embedding and not uses_vlm:
        return True, "not configured", None

    from openviking_cli.utils.ollama import check_ollama_running, parse_ollama_url

    # Determine host/port from config
    if uses_embedding:
        host, port = parse_ollama_url(dense.get("api_base"))
    else:
        host, port = parse_ollama_url(vlm.get("api_base"))

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
    ("VLM", check_vlm),
    ("Ollama", check_ollama),
    ("Disk", check_disk),
]


def run_doctor(*, live_embedding: bool = False) -> int:
    """Run all diagnostic checks and print a formatted report.

    Returns 0 if all checks pass, 1 otherwise.
    """
    print("\nOpenViking Doctor\n")

    checks = [
        ("Config", check_config),
        ("Python", check_python),
        ("Native Engine", check_native_engine),
        ("AGFS", check_agfs),
        ("Embedding", lambda: check_embedding(live=live_embedding)),
        ("VLM", check_vlm),
        ("Ollama", check_ollama),
        ("Disk", check_disk),
    ]

    failed = 0
    max_label = max(len(label) for label, _ in checks)

    for label, check_fn in checks:
        try:
            ok, detail, fix = check_fn()
        except Exception as exc:
            ok, detail, fix = False, f"Unexpected error: {exc}", None

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


def _normalize_argv(argv: Optional[list[str]]) -> list[str]:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "doctor":
        return args[1:]
    return args


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="openviking-server doctor")
    parser.add_argument("--config", help="Path to ov.conf")
    parser.add_argument(
        "--live",
        "--live-embedding",
        dest="live_embedding",
        action="store_true",
        help="Perform a small live embedding validation for endpoint/model checks",
    )
    return parser.parse_args(_normalize_argv(argv))


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``openviking-server doctor``."""
    args = _parse_args(argv)
    if args.config:
        os.environ[OPENVIKING_CONFIG_ENV] = args.config
    return run_doctor(live_embedding=args.live_embedding)
