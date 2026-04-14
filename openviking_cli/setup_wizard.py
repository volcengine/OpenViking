"""openviking-server init - interactive setup wizard for OpenViking.

Guides users through model selection and configuration, with a focus on
local deployment via Ollama for macOS / Apple Silicon beginners.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openviking_cli.utils.config.consts import DEFAULT_CONFIG_DIR
from openviking_cli.utils.ollama import (
    check_ollama_running,
    get_ollama_models,
    install_ollama,
    is_model_available,
    is_ollama_installed,
    ollama_pull_model,
    start_ollama,
)

# ---------------------------------------------------------------------------
# ANSI helpers (same pattern as doctor.py)
# ---------------------------------------------------------------------------

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _green(t: str) -> str:
    return f"\033[32m{t}\033[0m" if _USE_COLOR else t


def _red(t: str) -> str:
    return f"\033[31m{t}\033[0m" if _USE_COLOR else t


def _yellow(t: str) -> str:
    return f"\033[33m{t}\033[0m" if _USE_COLOR else t


def _dim(t: str) -> str:
    return f"\033[2m{t}\033[0m" if _USE_COLOR else t


def _bold(t: str) -> str:
    return f"\033[1m{t}\033[0m" if _USE_COLOR else t


def _cyan(t: str) -> str:
    return f"\033[36m{t}\033[0m" if _USE_COLOR else t


# ---------------------------------------------------------------------------
# Interactive prompt helpers (stdlib only)
# ---------------------------------------------------------------------------


def _prompt_choice(prompt: str, options: list[tuple[str, str]], default: int = 1) -> int:
    """Display numbered options and return 1-based selection index."""
    print(f"\n  {_bold(prompt)}\n")
    for i, (label, desc) in enumerate(options, 1):
        marker = "  "
        line = f"  {marker}[{i}] {label}"
        if desc:
            line += f"  {_dim(desc)}"
        print(line)

    while True:
        try:
            raw = input(f"\n  Select [{default}]: ").strip()
        except EOFError:
            return default
        if not raw:
            return default
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        print(f"  {_red('Please enter a number between 1 and ' + str(len(options)))}")


def _prompt_input(prompt: str, default: str = "") -> str:
    """Prompt for free-text input with optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"  {prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return raw or default


def _prompt_confirm(prompt: str, default: bool = True) -> bool:
    """Yes/no confirmation prompt."""
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------


def _get_system_ram_gb() -> int:
    """Get total system RAM in GB."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) // (1024**3)
    except (ValueError, OSError, AttributeError):
        pass
    # Windows fallback
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys // (1024**3)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Ollama interaction (delegates to openviking_cli.utils.ollama)
# ---------------------------------------------------------------------------


def _ensure_ollama() -> bool:
    """Make sure Ollama is installed and running (interactive). Returns True if ready."""
    print("\n  Checking Ollama...", end=" ", flush=True)

    if is_ollama_installed():
        if check_ollama_running():
            print(_green("running at localhost:11434"))
            return True
        print(_yellow("installed but not running"))
        print(f"  {_dim('Starting Ollama...')}", end=" ", flush=True)
        result = start_ollama()
        if result.success:
            print(_green("ready"))
        else:
            msg = result.stderr_output or result.message
            print(_yellow(f"failed ({msg})"))
        return result.success

    # Not installed
    print(_yellow("not installed"))
    if not _prompt_confirm("Install Ollama now?"):
        print(f"\n  {_dim('Manual install: https://ollama.com/download')}")
        return False

    print()
    if not install_ollama():
        print(f"  {_red('Installation failed.')}")
        print(f"  {_dim('Try manually: https://ollama.com/download')}")
        return False

    print(f"  {_green('OK')} Ollama installed")
    print(f"  {_dim('Starting Ollama...')}", end=" ", flush=True)
    result = start_ollama()
    if result.success:
        print(_green("ready"))
    else:
        msg = result.stderr_output or result.message
        print(_yellow(f"failed ({msg})"))
    return result.success


# ---------------------------------------------------------------------------
# Model presets
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingPreset:
    label: str
    model: str  # Ollama model name
    dimension: int
    size_hint: str
    min_ram_gb: int  # Minimum recommended RAM


@dataclass
class VLMPreset:
    label: str
    ollama_model: str  # For ollama pull
    litellm_model: str  # For config: "ollama/xxx"
    size_hint: str
    min_ram_gb: int  # Minimum recommended RAM


EMBEDDING_PRESETS: list[EmbeddingPreset] = [
    EmbeddingPreset("Qwen3-Embedding 0.6B", "qwen3-embedding:0.6b", 1024, "~639 MB", 4),
    EmbeddingPreset("Qwen3-Embedding 4B", "qwen3-embedding:4b", 1024, "~2.5 GB", 8),
    EmbeddingPreset("Qwen3-Embedding 8B", "qwen3-embedding:8b", 1024, "~4.7 GB", 16),
    EmbeddingPreset("EmbeddingGemma 300M", "embeddinggemma:300m", 768, "~622 MB", 4),
]

VLM_PRESETS: list[VLMPreset] = [
    VLMPreset("Qwen 3.5 2B", "qwen3.5:2b", "ollama/qwen3.5:2b", "~2.7 GB", 4),
    VLMPreset("Qwen 3.5 4B", "qwen3.5:4b", "ollama/qwen3.5:4b", "~3.4 GB", 8),
    VLMPreset("Qwen 3.5 9B", "qwen3.5:9b", "ollama/qwen3.5:9b", "~6.6 GB", 16),
    VLMPreset("Qwen 3.5 27B", "qwen3.5:27b", "ollama/qwen3.5:27b", "~17 GB", 32),
    VLMPreset("Qwen 3.5 35B", "qwen3.5:35b", "ollama/qwen3.5:35b", "~24 GB", 48),
    VLMPreset("Qwen 3.5 122B", "qwen3.5:122b", "ollama/qwen3.5:122b", "~81 GB", 128),
    VLMPreset("Gemma 4 E2B", "gemma4:e2b", "ollama/gemma4:e2b", "~7.2 GB", 16),
    VLMPreset("Gemma 4 E4B", "gemma4:e4b", "ollama/gemma4:e4b", "~9.6 GB", 16),
    VLMPreset("Gemma 4 26B", "gemma4:26b", "ollama/gemma4:26b", "~18 GB", 32),
    VLMPreset("Gemma 4 31B", "gemma4:31b", "ollama/gemma4:31b", "~20 GB", 48),
]

# Recommended defaults indexed by RAM tier
_RAM_TIERS: list[tuple[int, int, int]] = [
    # (max_ram_gb, embedding_preset_index, vlm_preset_index)
    (8, 0, 0),  # ≤8 GB: qwen3-embedding:0.6b + qwen3.5:2b
    (16, 0, 1),  # 8-16 GB: qwen3-embedding:0.6b + qwen3.5:4b
    (32, 2, 2),  # 16-32 GB: qwen3-embedding:8b + qwen3.5:9b
    (64, 2, 7),  # 32-64 GB: qwen3-embedding:8b + gemma4:e4b
]
_RAM_DEFAULT_EMBED = 2  # ≥64 GB: qwen3-embedding:8b
_RAM_DEFAULT_VLM = 3  # ≥64 GB: qwen3.5:27b


def _get_recommended_indices(ram_gb: int) -> tuple[int, int]:
    """Return (embedding_index, vlm_index) for the RAM tier (0-based)."""
    for max_ram, emb_idx, vlm_idx in _RAM_TIERS:
        if ram_gb <= max_ram:
            return emb_idx, vlm_idx
    return _RAM_DEFAULT_EMBED, _RAM_DEFAULT_VLM


# ---------------------------------------------------------------------------
# Cloud provider presets
# ---------------------------------------------------------------------------


@dataclass
class CloudProvider:
    label: str
    provider: str
    default_api_base: str
    default_embedding_model: str
    default_embedding_dim: int
    default_vlm_model: str


CLOUD_PROVIDERS: list[CloudProvider] = [
    CloudProvider(
        "OpenAI",
        "openai",
        "https://api.openai.com/v1",
        "text-embedding-3-small",
        1536,
        "gpt-4o-mini",
    ),
    CloudProvider(
        "Volcengine (Doubao)",
        "volcengine",
        "https://ark.cn-beijing.volces.com/api/v3",
        "doubao-embedding-vision-251215",
        1024,
        "doubao-seed-2-0-pro-260215",
    ),
]


# ---------------------------------------------------------------------------
# Config building
# ---------------------------------------------------------------------------


def _build_ollama_config(
    embedding: EmbeddingPreset,
    vlm: VLMPreset,
    workspace: str,
) -> dict[str, Any]:
    """Build ov.conf dict for Ollama-based setup."""
    return {
        "storage": {"workspace": workspace},
        "embedding": {
            "dense": {
                "provider": "ollama",
                "model": embedding.model,
                "api_base": "http://localhost:11434/v1",
                "dimension": embedding.dimension,
                "input": "text",
            },
        },
        "vlm": {
            "provider": "litellm",
            "model": vlm.litellm_model,
            "api_key": "no-key",
            "api_base": "http://localhost:11434",
            "temperature": 0.0,
            "max_retries": 2,
        },
    }


def _build_cloud_config(
    provider: CloudProvider,
    embedding_api_key: str,
    embedding_model: str,
    embedding_dim: int,
    vlm_api_key: str,
    vlm_model: str,
    workspace: str,
    embedding_api_base: str | None = None,
    vlm_api_base: str | None = None,
) -> dict[str, Any]:
    """Build ov.conf dict for cloud API setup."""
    return {
        "storage": {"workspace": workspace},
        "embedding": {
            "dense": {
                "provider": provider.provider,
                "model": embedding_model,
                "api_key": embedding_api_key,
                "api_base": embedding_api_base or provider.default_api_base,
                "dimension": embedding_dim,
            },
        },
        "vlm": {
            "provider": provider.provider,
            "model": vlm_model,
            "api_key": vlm_api_key,
            "api_base": vlm_api_base or provider.default_api_base,
            "temperature": 0.0,
            "max_retries": 2,
        },
    }


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "ov.conf"
_DEFAULT_WORKSPACE = str(DEFAULT_CONFIG_DIR / "data")


def _write_config(config_dict: dict[str, Any], config_path: Path) -> bool:
    """Write config dict as JSON. Backs up existing file as .bak."""
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            backup = config_path.with_suffix(".conf.bak")
            config_path.rename(backup)
            print(f"  {_dim('Existing config backed up to ' + str(backup))}")
        config_path.write_text(
            json.dumps(config_dict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return True
    except OSError as exc:
        print(f"  {_red(f'Failed to write config: {exc}')}")
        return False


# ---------------------------------------------------------------------------
# Wizard flows
# ---------------------------------------------------------------------------


def _wizard_ollama() -> dict[str, Any] | None:
    """Ollama-based local model setup flow."""
    # Ensure Ollama is installed and running
    ollama_running = _ensure_ollama()

    if not ollama_running:
        if not _prompt_confirm(
            "Continue without Ollama? (config will be generated but models won't be pulled)",
            default=False,
        ):
            return None

    available_models = get_ollama_models() if ollama_running else []

    # System RAM
    ram_gb = _get_system_ram_gb()
    rec_embed_idx, rec_vlm_idx = _get_recommended_indices(ram_gb)
    if ram_gb > 0:
        print(f"\n  {_dim(f'Detected {ram_gb} GB RAM')}")

    # --- Embedding selection ---
    embed_options: list[tuple[str, str]] = []
    for i, p in enumerate(EMBEDDING_PRESETS):
        rec = " *" if i == rec_embed_idx else ""
        avail = ""
        if ollama_running and is_model_available(p.model, available_models):
            avail = _green(" [downloaded]")
        embed_options.append(
            (
                f"{p.label}",
                f"({p.dimension}d, {p.size_hint}){avail}{rec}",
            )
        )

    embed_choice = _prompt_choice("Embedding model:", embed_options, default=rec_embed_idx + 1)
    embedding = EMBEDDING_PRESETS[embed_choice - 1]

    # Pull embedding model
    if ollama_running and not is_model_available(embedding.model, available_models):
        if _prompt_confirm(f"'{embedding.model}' not found locally. Pull now?"):
            print()
            if not ollama_pull_model(embedding.model):
                print(
                    f"  {_yellow('Pull failed. You can pull it later: ollama pull ' + embedding.model)}"
                )
            else:
                print(f"  {_green('OK')} {embedding.model} pulled successfully")

    # --- VLM selection ---
    vlm_options: list[tuple[str, str]] = []
    for i, p in enumerate(VLM_PRESETS):
        rec = " *" if i == rec_vlm_idx else ""
        avail = ""
        if ollama_running and is_model_available(p.ollama_model, available_models):
            avail = _green(" [downloaded]")
        vlm_options.append(
            (
                f"{p.label}",
                f"({p.size_hint}){avail}{rec}",
            )
        )

    vlm_choice = _prompt_choice("Language model (VLM):", vlm_options, default=rec_vlm_idx + 1)
    vlm = VLM_PRESETS[vlm_choice - 1]

    # Pull VLM model
    if ollama_running and not is_model_available(vlm.ollama_model, available_models):
        if _prompt_confirm(f"'{vlm.ollama_model}' not found locally. Pull now?"):
            print()
            if not ollama_pull_model(vlm.ollama_model):
                print(
                    f"  {_yellow('Pull failed. You can pull it later: ollama pull ' + vlm.ollama_model)}"
                )
            else:
                print(f"  {_green('OK')} {vlm.ollama_model} pulled successfully")

    # Workspace
    workspace = _prompt_input("Workspace", default=_DEFAULT_WORKSPACE)

    return _build_ollama_config(embedding, vlm, workspace)


def _wizard_cloud() -> dict[str, Any] | None:
    """Cloud API model setup flow."""
    # Provider selection
    provider_options = [(p.label, "") for p in CLOUD_PROVIDERS]
    provider_options.append(("Other (manual)", ""))
    choice = _prompt_choice("Cloud provider:", provider_options, default=1)

    if choice > len(CLOUD_PROVIDERS):
        # Manual / Other
        print(f"\n  See example config: {_cyan('examples/ov.conf.example')}")
        print(f"  Edit {_cyan(str(_DEFAULT_CONFIG_PATH))} manually.\n")
        return None

    provider = CLOUD_PROVIDERS[choice - 1]

    # Embedding config
    print(f"\n  {_bold('Embedding configuration')}")
    embedding_api_key = _prompt_input("API Key")
    if not embedding_api_key:
        print(f"  {_red('API key is required')}")
        return None
    embedding_model = _prompt_input("Model", default=provider.default_embedding_model)
    embedding_dim_str = _prompt_input("Dimension", default=str(provider.default_embedding_dim))
    try:
        embedding_dim = int(embedding_dim_str)
    except ValueError:
        embedding_dim = provider.default_embedding_dim
    embedding_api_base = _prompt_input("API Base", default=provider.default_api_base)

    # VLM config
    print(f"\n  {_bold('VLM configuration')}")
    vlm_api_key = _prompt_input("API Key (same as above?)", default=embedding_api_key)
    vlm_model = _prompt_input("Model", default=provider.default_vlm_model)
    vlm_api_base = _prompt_input("API Base", default=provider.default_api_base)

    # Workspace
    workspace = _prompt_input("Workspace", default=_DEFAULT_WORKSPACE)

    return _build_cloud_config(
        provider,
        embedding_api_key,
        embedding_model,
        embedding_dim,
        vlm_api_key,
        vlm_model,
        workspace,
        embedding_api_base,
        vlm_api_base,
    )


def _wizard_custom() -> dict[str, Any] | None:
    """Custom configuration - point user to example config."""
    example = Path(__file__).parent.parent / "examples" / "ov.conf.example"
    if example.exists():
        print(f"\n  Example config: {_cyan(str(example))}")
    print(f"  Config path:    {_cyan(str(_DEFAULT_CONFIG_PATH))}")

    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))
    if editor:
        if _prompt_confirm(f"Open {_DEFAULT_CONFIG_PATH} in {editor}?"):
            _DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if not _DEFAULT_CONFIG_PATH.exists():
                # Copy example as starting point
                try:
                    _DEFAULT_CONFIG_PATH.write_text(
                        example.read_text(encoding="utf-8"), encoding="utf-8"
                    )
                except OSError:
                    pass
            subprocess.run([editor, str(_DEFAULT_CONFIG_PATH)], check=False)
    else:
        print(f"\n  {_dim('Set $EDITOR to open the config file automatically.')}")
    return None


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_init() -> int:
    """Run the interactive setup wizard."""
    print(f"\n  {_bold('OpenViking Setup')}")
    print(f"  {'=' * 16}\n")

    # Check for existing config
    if _DEFAULT_CONFIG_PATH.exists():
        print(f"  {_yellow('Existing config found:')} {_DEFAULT_CONFIG_PATH}")
        if not _prompt_confirm("Overwrite? (current config will be backed up as .bak)"):
            print("  Setup cancelled.\n")
            return 0

    # Deployment mode
    mode = _prompt_choice(
        "Choose setup mode:",
        [
            ("Local models via Ollama", "(recommended for macOS / Apple Silicon)"),
            ("Cloud API", "(OpenAI, Volcengine, etc.)"),
            ("Custom", "(manual editing)"),
        ],
        default=1,
    )

    config_dict: dict[str, Any] | None = None

    if mode == 1:
        config_dict = _wizard_ollama()
    elif mode == 2:
        config_dict = _wizard_cloud()
    else:
        _wizard_custom()
        return 0

    if config_dict is None:
        print("\n  Setup cancelled.\n")
        return 0

    # Summary
    emb = config_dict.get("embedding", {}).get("dense", {})
    vlm = config_dict.get("vlm", {})
    ws = config_dict.get("storage", {}).get("workspace", _DEFAULT_WORKSPACE)

    print(f"\n  {_bold('Summary:')}")
    print(
        f"    Embedding:  {emb.get('provider', '')} / {emb.get('model', '')} ({emb.get('dimension', '')}d)"
    )
    print(f"    VLM:        {vlm.get('provider', '')} / {vlm.get('model', '')}")
    print(f"    Workspace:  {ws}")
    print(f"    Config:     {_DEFAULT_CONFIG_PATH}")

    if not _prompt_confirm("\n  Save configuration?"):
        print("\n  Setup cancelled.\n")
        return 0

    # Write
    if not _write_config(config_dict, _DEFAULT_CONFIG_PATH):
        return 1

    print(f"  {_green('OK')} Configuration written to {_DEFAULT_CONFIG_PATH}\n")

    # Post-init tips
    print(f"  {_bold('Next steps:')}")
    print(f"    Start the server:  {_cyan('openviking-server')}")
    print(f"    Validate setup:    {_cyan('openviking-server doctor')}")
    print()

    return 0


def main() -> int:
    """Entry point for ``openviking-server init``."""
    try:
        return run_init()
    except KeyboardInterrupt:
        print("\n\n  Setup cancelled.\n")
        return 130
