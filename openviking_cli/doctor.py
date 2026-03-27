# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""ov doctor - validate OpenViking subsystems and report actionable diagnostics.

Unlike ``ov health`` (which pings a running server), ``ov doctor`` checks
local prerequisites without requiring a server: config file, Python version,
native vector engine, AGFS, embedding provider, VLM provider, and disk space.
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Optional

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

    missing = [key for key in ("embedding",) if key not in data]
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


def check_embedding() -> tuple[bool, str, Optional[str]]:
    """Load embedding config and verify provider connectivity."""
    config_path = _find_config()
    if config_path is None:
        return False, "Cannot check (no config file)", None

    data = _load_config_json(config_path)
    if data is None:
        return False, "Cannot check (config unreadable)", None

    embedding = data.get("embedding", {})
    dense = embedding.get("dense", {})
    provider = dense.get("provider", "unknown")
    model = dense.get("model", "unknown")

    if provider == "unknown":
        return False, "No embedding provider configured", "Add embedding.dense section to ov.conf"

    api_key = dense.get("api_key", "")
    if not api_key or api_key.startswith("{"):
        return (
            False,
            f"{provider}/{model} (no API key)",
            "Set embedding.dense.api_key in ov.conf",
        )

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

    api_key = vlm.get("api_key", "")
    if not api_key or api_key.startswith("{"):
        return (
            False,
            f"{provider}/{model} (no API key)",
            "Set vlm.api_key in ov.conf",
        )

    return True, f"{provider}/{model}", None


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


def main() -> int:
    """Entry point for ``ov doctor``."""
    return run_doctor()
