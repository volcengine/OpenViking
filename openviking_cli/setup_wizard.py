"""openviking-server init - interactive setup wizard for OpenViking.

Guides users through model selection and configuration, with a focus on
local deployment via Ollama for macOS / Apple Silicon beginners.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import secrets
import select
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openviking_cli.utils.config.consts import DEFAULT_CONFIG_DIR, OPENVIKING_CONFIG_ENV
from openviking_cli.utils.ollama import (
    check_ollama_running,
    get_ollama_models,
    install_ollama,
    is_model_available,
    is_ollama_installed,
    ollama_pull_model,
    start_ollama,
)

_DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_DEFAULT_KIMI_BASE_URL = "https://api.kimi.com/coding"
_DEFAULT_GLM_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_DEFAULT_CODEX_MODEL = "gpt-5.4"
_DEFAULT_KIMI_MODEL = "kimi-code"
_DEFAULT_GLM_MODEL = "glm-4.6v"

# Sentinel returned by flows that hand the user off to manual config editing;
# distinguishes "handled elsewhere" from "cancelled" (None).
_CUSTOM_SETUP = object()

# Sentinel for "user chose to skip the VLM" (embedding-only setup).
_SKIP_VLM = object()

# Sentinel for "user navigated back to the previous step" (← key / [0] Back).
_GO_BACK = object()

# rich ships with typer (a core dependency); degrade gracefully without it.
try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel

    _console: Any = _RichConsole(highlight=False)
except ImportError:  # pragma: no cover
    _console = None

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


def _prompt_choice(
    prompt: str,
    options: list[tuple[str, str]],
    default: int = 1,
    *,
    allow_back: bool = False,
) -> int:
    """Display a selectable option list and return the 1-based selection index.

    On an interactive TTY this renders an arrow-key menu (↑/↓ or j/k to move,
    digits to jump, Enter or → to confirm, ← to go back when *allow_back* is
    set). Anywhere else (pipes, tests, dumb terminals) it falls back to the
    classic numbered prompt, where ``0`` means back.

    Returns 0 when *allow_back* is set and the user navigated back.
    """
    if _stdin_stdout_tty() and os.environ.get("TERM", "") != "dumb":
        try:
            import termios
        except ImportError:
            return _prompt_choice_numbered(prompt, options, default, allow_back=allow_back)
        try:
            return _prompt_choice_interactive(prompt, options, default, allow_back=allow_back)
        except (OSError, termios.error):
            pass
    return _prompt_choice_numbered(prompt, options, default, allow_back=allow_back)


def _prompt_choice_numbered(
    prompt: str,
    options: list[tuple[str, str]],
    default: int = 1,
    *,
    allow_back: bool = False,
) -> int:
    """Numbered fallback selector (non-TTY, pipes, tests)."""
    print(f"\n  {_bold(prompt)}\n")
    for i, (label, desc) in enumerate(options, 1):
        marker = "  "
        line = f"  {marker}[{i}] {label}"
        if desc:
            line += f"  {_dim(desc)}"
        print(line)
    if allow_back:
        print(f"    [0] Back  {_dim('(previous step)')}")

    while True:
        try:
            raw = input(f"\n  Select [{default}]: ").strip()
        except (EOFError, OSError):
            return default
        if not raw:
            return default
        try:
            choice = int(raw)
            if allow_back and choice == 0:
                return 0
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        low = "0" if allow_back else "1"
        print(f"  {_red('Please enter a number between ' + low + ' and ' + str(len(options)))}")


def _read_menu_key(fd: int) -> str:
    """Read one key press from *fd*, decoding arrow-key escape sequences.

    Reads at the file-descriptor level (``os.read``) — ``sys.stdin`` is
    buffered and its readahead would consume escape-sequence bytes behind
    ``select``'s back.
    """
    data = os.read(fd, 1)
    if data != b"\x1b":
        return data.decode("utf-8", errors="ignore")
    if not select.select([fd], [], [], 0.05)[0]:
        return "\x1b"
    if os.read(fd, 1) != b"[":
        return "\x1b"
    if not select.select([fd], [], [], 0.05)[0]:
        return "\x1b"
    return "\x1b[" + os.read(fd, 1).decode("utf-8", errors="ignore")


def _prompt_choice_interactive(
    prompt: str,
    options: list[tuple[str, str]],
    default: int = 1,
    *,
    allow_back: bool = False,
) -> int:
    """Arrow-key menu: ↑/↓/j/k move, digits jump, Enter/→ confirm, ← back."""
    import termios
    import tty

    n = len(options)
    idx = max(0, min(n - 1, default - 1))
    out = sys.stdout
    went_back = False

    def render(first: bool) -> None:
        if not first:
            out.write(f"\x1b[{n}A")
        for i, (label, desc) in enumerate(options):
            out.write("\x1b[2K")
            if i == idx:
                line = f"  \x1b[1;36m❯ {label}\x1b[0m"
            else:
                line = f"    {label}"
            if desc:
                line += f"  \x1b[2m{desc}\x1b[0m"
            out.write(line + "\n")
        out.flush()

    hint = "↑/↓ move · enter select"
    if allow_back:
        hint += " · ← back"
    print(f"\n  {_bold(prompt)}  {_dim(hint)}\n")
    render(True)

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    while True:
        tty.setraw(fd)
        try:
            key = _read_menu_key(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        if key in ("\r", "\n", "\x04", "\x1b[C"):  # Enter / Ctrl-D / → confirm
            break
        if key == "\x03":
            raise KeyboardInterrupt
        if allow_back and key == "\x1b[D":  # ← back to previous step
            went_back = True
            break
        if key in ("\x1b[A", "k"):
            idx = (idx - 1) % n
        elif key in ("\x1b[B", "j", "\t"):
            idx = (idx + 1) % n
        elif key.isdigit():
            jump = 9 if key == "0" else int(key) - 1
            if jump < n:
                idx = jump
        render(False)

    # Collapse the menu into a single confirmation line.
    out.write(f"\x1b[{n}A\x1b[0J")
    if went_back:
        out.write("  \x1b[2m← back\x1b[0m\n")
        out.flush()
        return 0
    out.write(f"  \x1b[1;36m❯\x1b[0m {options[idx][0]}\n")
    out.flush()
    return idx + 1


def _mask_secret(value: str, prefix: int = 7, suffix: int = 4) -> str:
    """Mask a secret string, showing only the first ``prefix`` and last ``suffix`` chars."""
    if not value:
        return ""
    if len(value) <= prefix + suffix:
        return "*" * len(value)
    return f"{value[:prefix]}{'*' * (len(value) - prefix - suffix)}{value[-suffix:]}"


def _masked_input(prompt: str) -> str:
    """Read a line of input, echoing ``*`` per character; on submit, rewrite
    the line to show ``prompt + _mask_secret(value)`` (prefix 7 + suffix 4).

    Falls back to plain ``input()`` when stdin/stdout aren't TTYs (tests,
    pipes) and to ``getpass.getpass`` (no echo at all) on platforms
    without ``termios`` (Windows).
    """
    import sys

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return input(prompt)

    try:
        import termios
        import tty
    except ImportError:
        import getpass

        return getpass.getpass(prompt)

    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    chars: list[str] = []
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            if ch == "\x04":  # Ctrl-D / EOF
                if not chars:
                    raise EOFError
                break
            if ch in ("\x7f", "\b"):  # Backspace / DEL
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if ch < " ":  # Other control chars — ignore
                continue
            chars.append(ch)
            sys.stdout.write("*")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        value = "".join(chars)
        # Rewrite the line: \r → clear → prompt + masked preview + \n.
        sys.stdout.write("\r\033[2K" + prompt + _mask_secret(value) + "\n")
        sys.stdout.flush()
    return value


def _prompt_required_input(prompt: str, default: str | None = None, *, mask: bool = False) -> str:
    """Prompt for a required free-text value. When ``mask`` is True, echo ``*`` per char."""
    reader = _masked_input if mask else input
    while True:
        try:
            prompt_text = f"  {prompt} [{default}]: " if default is not None else f"  {prompt}: "
            raw = reader(prompt_text).strip()
        except (EOFError, OSError):
            return default or ""
        if not raw and default is not None:
            return default
        if raw:
            return raw
        print(f"  {_red(prompt + ' is required')}")


def _prompt_api_key(prompt: str = "API Key") -> str:
    """Prompt for an API key with inline masked echo (no extra confirmation line)."""
    return _prompt_required_input(prompt, mask=True)


def _stdin_stdout_tty() -> bool:
    """True when both stdin and stdout are interactive TTYs."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


# Environment variables that commonly carry each provider's API key, keyed by
# the internal provider string (BytePlus shares "volcengine").
_PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "volcengine": ["ARK_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "kimi": ["KIMI_API_KEY", "MOONSHOT_API_KEY"],
    "glm": ["GLM_API_KEY", "ZHIPUAI_API_KEY"],
}


def _prompt_api_key_with_env(env_vars: list[str] | None, prompt: str = "API Key") -> str:
    """Prompt for an API key, offering any matching environment variable first.

    Only engages the env-var shortcut on interactive TTYs so scripted or
    piped runs keep the plain prompt behavior.
    """
    if env_vars and _stdin_stdout_tty():
        for var in env_vars:
            value = os.environ.get(var, "").strip()
            if value and _prompt_confirm(f"Found ${var} ({_mask_secret(value)}). Use it?"):
                return value
    return _prompt_api_key(prompt)


def _prompt_required_int(prompt: str, default: int | None = None) -> int | None:
    """Prompt for a required integer value."""
    while True:
        try:
            prompt_text = f"  {prompt} [{default}]: " if default is not None else f"  {prompt}: "
            raw = input(prompt_text).strip()
        except (EOFError, OSError):
            return default
        if not raw:
            if default is not None:
                return default
            print(f"  {_red(prompt + ' is required')}")
            continue
        try:
            return int(raw)
        except ValueError:
            print(f"  {_red('Please enter a valid integer')}")


def _prompt_confirm(prompt: str, default: bool = True) -> bool:
    """Yes/no confirmation prompt."""
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    except (EOFError, OSError):
        return default
    if not raw:
        return default
    return raw in ("y", "yes")



def _rule(title: str) -> None:
    """Print a section rule (rich when available, plain bold header otherwise)."""
    if _console is not None:
        _console.print()
        _console.rule(f"[bold cyan]{title}[/bold cyan]", style="dim cyan")
    else:
        print(f"\n  {_bold(title)}")


def _print_banner() -> None:
    """Print the wizard banner."""
    if _console is not None:
        _console.print()
        _console.print(
            _RichPanel.fit(
                "[bold cyan]OpenViking[/bold cyan] [bold]Setup[/bold]\n"
                "[dim]Context database for AI agents — data in, context out[/dim]",
                border_style="cyan",
                padding=(0, 2),
            )
        )
    else:
        print(f"\n  {_bold('OpenViking Setup')}")
        print(f"  {'=' * 16}")


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


def _parse_size_gb(size_hint: str) -> float:
    """Parse a human size hint like ``~4.7 GB`` or ``~639 MB`` into GB."""
    match = re.search(r"([\d.]+)\s*(GB|MB)", size_hint, re.IGNORECASE)
    if not match:
        return 0.0
    value = float(match.group(1))
    return value / 1024 if match.group(2).upper() == "MB" else value


def _check_disk_before_pull(required_gb: float) -> bool:
    """Return True when there is enough free disk for a download of *required_gb*.

    When space looks too tight (less than the download plus a 5 GB margin),
    warn and let the user decide.
    """
    if required_gb <= 0:
        return True
    try:
        free_gb = shutil.disk_usage(Path.home()).free / (1024**3)
    except OSError:
        return True
    if free_gb >= required_gb + 5:
        return True
    print(
        f"  {_yellow(f'Low disk space: {free_gb:.0f} GB free, download needs ~{required_gb:.0f} GB.')}"
    )
    return _prompt_confirm("Continue anyway?", default=False)


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


def _ensure_model_pulled(
    model: str,
    size_hint: str,
    ollama_running: bool,
    available_models: list[str],
    *,
    ask: bool = True,
) -> None:
    """Pull *model* via Ollama when missing.

    With ``ask=True`` the user confirms each pull (with a disk-space check);
    ``ask=False`` is for flows where the user already approved the batch.
    """
    if not ollama_running or is_model_available(model, available_models):
        return
    if ask:
        if not _prompt_confirm(f"'{model}' not found locally. Pull now? ({size_hint})"):
            return
        if not _check_disk_before_pull(_parse_size_gb(size_hint)):
            return
    print()
    if not ollama_pull_model(model):
        print(f"  {_yellow('Pull failed. You can pull it later: ollama pull ' + model)}")
    else:
        print(f"  {_green('OK')} {model} pulled successfully")


def _ensure_codex_auth() -> bool:
    import importlib

    importlib.import_module("openviking.models.vlm")
    codex_auth = importlib.import_module("openviking.models.vlm.backends.codex_auth")

    print("\n  Checking Codex OAuth...", end=" ", flush=True)
    try:
        creds = codex_auth.resolve_codex_runtime_credentials(refresh_if_expiring=False)
        source = creds.get("source", "unknown")
        print(_green(f"ready via {source}"))
        return True
    except Exception:
        print(_yellow("not ready"))

    status = codex_auth.get_codex_auth_status()
    bootstrap_path = status.get("bootstrap_path")

    if status.get("bootstrap_available") and bootstrap_path:
        if _prompt_confirm(f"Import existing Codex CLI auth from {bootstrap_path}?"):
            try:
                path = codex_auth.bootstrap_codex_auth()
            except codex_auth.CodexAuthError as exc:
                print(f"  {_yellow(str(exc))}")
            else:
                if path is not None:
                    print(f"  {_green('OK')} Imported Codex OAuth into {path}")
                    return True

    if _prompt_confirm("Sign in to Codex now?"):
        try:
            path = codex_auth.login_codex_with_device_code()
        except codex_auth.CodexAuthError as exc:
            print(f"  {_yellow(str(exc))}")
        else:
            print(f"  {_green('OK')} Codex OAuth stored in {path}")
            return True

    print(
        f"  {_dim('You can finish setup now and re-run `openviking-server init` later to complete Codex sign-in.')}"
    )
    return False


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


@dataclass
class QueryPlannerPreset:
    label: str
    ollama_model: str  # For ollama pull
    litellm_model: str  # For config: "ollama/xxx"
    size_hint: str


EMBEDDING_PRESETS: list[EmbeddingPreset] = [
    EmbeddingPreset("Qwen3-Embedding 0.6B", "qwen3-embedding:0.6b", 1024, "~639 MB", 4),
    EmbeddingPreset("Qwen3-Embedding 4B", "qwen3-embedding:4b", 1024, "~2.5 GB", 8),
    EmbeddingPreset("Qwen3-Embedding 8B", "qwen3-embedding:8b", 1024, "~4.7 GB", 16),
    EmbeddingPreset("EmbeddingGemma 300M", "embeddinggemma:300m", 768, "~622 MB", 4),
]

VLM_PRESETS: list[VLMPreset] = [
    VLMPreset("Qwen 3.5 4B", "qwen3.5:4b", "ollama/qwen3.5:4b", "~3.4 GB", 8),
    VLMPreset("Qwen 3.5 9B", "qwen3.5:9b", "ollama/qwen3.5:9b", "~6.6 GB", 16),
    VLMPreset("Qwen 3.6 27B", "qwen3.6:27b", "ollama/qwen3.6:27b", "~17 GB, 256K ctx", 32),
    VLMPreset("Qwen 3.6 35B", "qwen3.6:35b", "ollama/qwen3.6:35b", "~24 GB, 256K ctx", 48),
    VLMPreset("Qwen 3.5 122B", "qwen3.5:122b", "ollama/qwen3.5:122b", "~81 GB", 128),
    VLMPreset("Gemma 4 E2B", "gemma4:e2b", "ollama/gemma4:e2b", "~7.2 GB", 16),
    VLMPreset("Gemma 4 E4B", "gemma4:e4b", "ollama/gemma4:e4b", "~9.6 GB", 16),
    VLMPreset("Gemma 4 26B", "gemma4:26b", "ollama/gemma4:26b", "~18 GB", 32),
    VLMPreset("Gemma 4 31B", "gemma4:31b", "ollama/gemma4:31b", "~20 GB", 48),
]

# Lightweight query-planner models (intent analysis / query planning). All run
# locally via Ollama. Runtime prompt selection is handled by the retrieval
# intent analyzer based on the configured model name.
QUERY_PLANNER_PRESETS: list[QueryPlannerPreset] = [
    QueryPlannerPreset(
        "ov_intent_analysis_sft v7_q8",
        "guoxuter/ov_intent_analysis_sft:v7_q8",
        "ollama/guoxuter/ov_intent_analysis_sft:v7_q8",
        "~0.8B, recommended",
    ),
    QueryPlannerPreset(
        "ov_intent_analysis_sft v4_q8",
        "guoxuter/ov_intent_analysis_sft:v4_q8",
        "ollama/guoxuter/ov_intent_analysis_sft:v4_q8",
        "~0.8B",
    ),
]

# Approximate download size of the q8 query-planner models (size_hint above
# carries the parameter count, not the artifact size).
_QUERY_PLANNER_DOWNLOAD_GB = 0.9

# Recommended defaults indexed by RAM tier. qwen3.5:4b is the smallest VLM we
# recommend — smaller models fail OV's memory extraction (they copy the prompt's
# few-shot examples into fabricated memories), so there is no sub-4B tier.
_RAM_TIERS: list[tuple[int, int, int]] = [
    # (max_ram_gb, embedding_preset_index, vlm_preset_index)
    (8, 0, 0),  # ≤8 GB: qwen3-embedding:0.6b + qwen3.5:4b
    (16, 0, 0),  # 8-16 GB: qwen3-embedding:0.6b + qwen3.5:4b
    (32, 2, 1),  # 16-32 GB: qwen3-embedding:8b + qwen3.5:9b
    (64, 2, 6),  # 32-64 GB: qwen3-embedding:8b + gemma4:e4b
]
_RAM_DEFAULT_EMBED = 2  # ≥64 GB: qwen3-embedding:8b
_RAM_DEFAULT_VLM = 2  # ≥64 GB: qwen3.6:27b


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
        "VolcEngine (火山引擎)",
        "volcengine",
        "https://ark.cn-beijing.volces.com/api/v3",
        "doubao-embedding-vision-251215",
        1024,
        "doubao-seed-2-0-code-preview-260215",
    ),
    CloudProvider(
        "BytePlus",
        "volcengine",
        "https://ark.ap-southeast.bytepluses.com/api/v3",
        "skylark-embedding-vision-251215",
        1024,
        "doubao-seed-2-0-code-preview-260215",
    ),
    CloudProvider(
        "OpenAI",
        "openai",
        "https://api.openai.com/v1",
        "text-embedding-3-small",
        1536,
        "gpt-5.4",
    ),
]


def _get_cloud_provider_by_label(label: str) -> CloudProvider:
    for provider in CLOUD_PROVIDERS:
        if provider.label == label:
            return provider
    raise ValueError(f"Unknown cloud provider: {label}")


_WIZARD_VLM_OPTIONS: list[tuple[str, str]] = [
    ("VolcEngine (火山引擎)", "(API)"),
    ("BytePlus", "(API)"),
    ("OpenAI", "(API)"),
    ("OpenAI Codex", "(Subscription)"),
    ("Kimi", "(Subscription API Key)"),
    ("GLM", "(Subscription API Key)"),
    ("Custom (OpenAI-compatible)", "(Any OpenAI-compatible endpoint)"),
    ("Local via Ollama", "(no API key, runs on this machine)"),
]

_WIZARD_VLM_OLLAMA_CHOICE = 8  # index of "Local via Ollama" in _WIZARD_VLM_OPTIONS


# ---------------------------------------------------------------------------
# llama.cpp local embedding presets
# ---------------------------------------------------------------------------


@dataclass
class LocalGGUFPreset:
    label: str
    model_name: str  # key in LOCAL_DENSE_MODEL_SPECS
    dimension: int
    size_hint: str


LOCAL_GGUF_PRESETS: list[LocalGGUFPreset] = [
    LocalGGUFPreset("BGE-small-zh v1.5 (f16)", "bge-small-zh-v1.5-f16", 512, "~24 MB"),
]


def _is_llamacpp_installed() -> bool:
    try:
        importlib.import_module("llama_cpp")
        return True
    except ImportError:
        return False


def _install_llamacpp() -> bool:
    """Attempt to install llama-cpp-python via pip.

    On the first attempt, uses the default build flags.  If compilation
    fails (common on ARM with older binutils that reject advanced
    ``-march`` extensions), retries with ``GGML_NATIVE=OFF`` to produce
    a generic build.
    """
    pip_cmd = [sys.executable, "-m", "pip", "install", "openviking[local-embed]"]

    try:
        subprocess.run(pip_cmd, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    print(f"  {_yellow('Native build failed, retrying with generic CPU flags...')}")
    env = os.environ.copy()
    prev = env.get("CMAKE_ARGS", "")
    env["CMAKE_ARGS"] = f"{prev} -DGGML_NATIVE=OFF -DLLAMA_NATIVE=OFF".strip()
    try:
        subprocess.run(pip_cmd, check=True, env=env)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _check_gguf_model_cached(model_name: str, cache_dir: str | None = None) -> bool:
    from openviking.models.embedder.local_embedders import get_local_model_cache_path

    return get_local_model_cache_path(model_name, cache_dir).exists()


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
            "dense": _ollama_dense_config(embedding),
        },
        "vlm": _ollama_vlm_config(vlm),
    }


def _ollama_dense_config(embedding: EmbeddingPreset) -> dict[str, Any]:
    """Build the dense-embedding config block for an Ollama-served model."""
    return {
        "provider": "ollama",
        "model": embedding.model,
        "api_base": "http://localhost:11434/v1",
        "dimension": embedding.dimension,
        "input": "text",
    }


def _ollama_vlm_config(vlm: VLMPreset) -> dict[str, Any]:
    """Build the VLM config block for an Ollama-served model.

    ``extra_request_body`` raises Ollama's context window past its 4096-token
    default (OV's memory-extraction prompt alone is ~5k tokens, so the default
    silently truncates the conversation) and disables thinking, which otherwise
    makes thinking models emit only reasoning and stall.
    """
    return {
        "provider": "litellm",
        "model": vlm.litellm_model,
        "api_key": "no-key",
        "api_base": "http://localhost:11434",
        "temperature": 0.0,
        "max_retries": 2,
        "extra_request_body": {"num_ctx": 16384, "think": False},
    }




def _build_query_planner_config(preset: QueryPlannerPreset) -> dict[str, Any]:
    """Build the ``query_planner`` config block for an Ollama-served model.

    Uses the litellm provider with the bare Ollama base URL (no ``/v1``) to
    match how the wizard configures the Ollama VLM, and disables thinking for
    lower latency on the small planner model.
    """
    return {
        "provider": "litellm",
        "model": preset.litellm_model,
        "api_key": "no-key",
        "api_base": "http://localhost:11434",
        "temperature": 0.0,
        "timeout": 60,
        "extra_request_body": {"think": False},
    }


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

_PIP_LOCAL_EMBED = 'pip install "openviking[local-embed]"'


def _config_path() -> Path:
    """Where init writes ov.conf — honors OPENVIKING_CONFIG_FILE."""
    override = os.environ.get(OPENVIKING_CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG_DIR / "ov.conf"


def _workspace_path() -> str:
    """Workspace lives next to ov.conf so a single mount captures everything."""
    return str(_config_path().parent / "data")


def _next_backup_path(config_path: Path) -> Path:
    """Return a non-conflicting backup path: .bak, then .bak.1, .bak.2, ..."""
    base = config_path.with_suffix(".conf.bak")
    if not base.exists():
        return base
    i = 1
    while True:
        candidate = base.with_suffix(f".bak.{i}")
        if not candidate.exists():
            return candidate
        i += 1


def _write_config(config_dict: dict[str, Any], config_path: Path) -> bool:
    """Write config dict as JSON. Backs up existing file as .bak (rotates on conflict)."""
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            backup = _next_backup_path(config_path)
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
# Shared selection helpers
# ---------------------------------------------------------------------------


def _select_embedding_preset(
    ollama_running: bool,
    available_models: list[str],
    rec_idx: int,
    *,
    allow_back: bool = False,
) -> EmbeddingPreset | None:
    """Interactive Ollama embedding preset selection. None when the user went back."""
    embed_options: list[tuple[str, str]] = []
    for i, p in enumerate(EMBEDDING_PRESETS):
        rec = " *" if i == rec_idx else ""
        avail = ""
        if ollama_running and is_model_available(p.model, available_models):
            avail = _green(" [downloaded]")
        embed_options.append(
            (
                f"{p.label}",
                f"({p.dimension}d, {p.size_hint}){avail}{rec}",
            )
        )

    choice = _prompt_choice(
        "Embedding model — powers semantic search:",
        embed_options,
        default=rec_idx + 1,
        allow_back=allow_back,
    )
    if choice == 0:
        return None
    return EMBEDDING_PRESETS[choice - 1]


def _select_vlm_preset(
    ollama_running: bool,
    available_models: list[str],
    rec_idx: int,
    *,
    allow_back: bool = False,
) -> VLMPreset | None:
    """Interactive Ollama VLM preset selection. None when the user went back."""
    vlm_options: list[tuple[str, str]] = []
    for i, p in enumerate(VLM_PRESETS):
        rec = " *" if i == rec_idx else ""
        avail = ""
        if ollama_running and is_model_available(p.ollama_model, available_models):
            avail = _green(" [downloaded]")
        vlm_options.append((f"{p.label}", f"({p.size_hint}){avail}{rec}"))

    choice = _prompt_choice(
        "Vision-language model (VLM) — parses documents & images:",
        vlm_options,
        default=rec_idx + 1,
        allow_back=allow_back,
    )
    if choice == 0:
        return None
    return VLM_PRESETS[choice - 1]


def _prompt_ollama_vlm(
    *, allow_back: bool = False, current_litellm_model: str | None = None
) -> tuple[dict[str, Any] | None | object, bool | None]:
    """Ollama VLM selection: ensure Ollama, pick a preset, pull it.

    Returns ``(vlm_config, ollama_running)``; ``vlm_config`` is None on cancel
    or ``_GO_BACK`` when the user navigated back from the preset list.
    *current_litellm_model* seeds the preset default when it matches one.
    """
    ollama_running = _ensure_ollama()
    if not ollama_running:
        if not _prompt_confirm("Continue without Ollama?", default=False):
            return None, ollama_running

    available_models = get_ollama_models() if ollama_running else []
    ram_gb = _get_system_ram_gb()
    _, rec_vlm_idx = _get_recommended_indices(ram_gb)
    if current_litellm_model:
        for i, p in enumerate(VLM_PRESETS):
            if p.litellm_model == current_litellm_model:
                rec_vlm_idx = i  # default to the currently configured model
                break

    vlm = _select_vlm_preset(ollama_running, available_models, rec_vlm_idx, allow_back=allow_back)
    if vlm is None:
        return _GO_BACK, ollama_running
    _ensure_model_pulled(vlm.ollama_model, vlm.size_hint, ollama_running, available_models)

    return _ollama_vlm_config(vlm), ollama_running


def _prompt_vlm_api_key(
    provider: str,
    reuse_key: tuple[str, str] | None,
    reuse_prompt: str = "Reuse the embedding API key for the VLM?",
) -> str:
    """Get a VLM API key: offer same-provider reuse first, then env vars, then prompt."""
    if reuse_key and reuse_key[0] == provider and reuse_key[1]:
        if _prompt_confirm(reuse_prompt):
            return reuse_key[1]
    return _prompt_api_key_with_env(_PROVIDER_ENV_KEYS.get(provider))


def _vlm_option_index_for(current: dict[str, Any]) -> int:
    """1-based ``_WIZARD_VLM_OPTIONS`` index matching an existing VLM config."""
    provider = str(current.get("provider") or "")
    api_base = str(current.get("api_base") or "")
    model = str(current.get("model") or "")
    if provider == "volcengine":
        return 2 if "bytepluses" in api_base else 1
    if provider == "openai-codex":
        return 4
    if provider == "kimi":
        return 5
    if provider == "glm":
        return 6
    if provider == "litellm" and model.startswith("ollama/"):
        return _WIZARD_VLM_OLLAMA_CHOICE
    if provider == "openai":
        return 3 if "api.openai.com" in api_base else 7
    return 1


def _prompt_cloud_vlm(
    allow_skip: bool = False,
    reuse_key: tuple[str, str] | None = None,
    allow_back: bool = False,
    current: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None | object, bool | None]:
    """VLM provider selection (cloud APIs, subscriptions, or local Ollama).

    Returns ``(vlm_config, ollama_running)``; ``vlm_config`` is None on cancel,
    the ``_SKIP_VLM`` sentinel when *allow_skip* is set and taken, or
    ``_GO_BACK`` when *allow_back* is set and the user navigated back.
    ``ollama_running`` is only set when the local Ollama option was taken.
    *reuse_key* is an optional ``(provider, api_key)`` from the embedding step,
    offered for reuse when the VLM provider matches. *current* (the existing
    VLM config, if any) seeds the menu position, model default, and offers to
    keep the existing API key.
    """
    current = current or {}
    options = list(_WIZARD_VLM_OPTIONS)
    if allow_skip:
        options.append(("Skip for now", "(embedding only — add a VLM later)"))
    default_idx = _vlm_option_index_for(current) if current else 1

    # An existing same-provider key takes priority as the reuse candidate.
    if current.get("api_key") and current.get("provider"):
        reuse_key = (str(current["provider"]), str(current["api_key"]))
        reuse_prompt = f"Keep the existing API key ({_mask_secret(str(current['api_key']))})?"
    else:
        reuse_prompt = "Reuse the embedding API key for the VLM?"

    def _default_model(branch_provider: str, fallback: str) -> str:
        if current.get("model") and current.get("provider") == branch_provider:
            return str(current["model"])
        return fallback

    while True:
        vlm_mode = _prompt_choice(
            "VLM provider:", options, default=default_idx, allow_back=allow_back
        )

        if vlm_mode == 0:
            return _GO_BACK, None

        if allow_skip and vlm_mode == len(options):
            return _SKIP_VLM, None

        if vlm_mode == _WIZARD_VLM_OLLAMA_CHOICE:
            current_ollama_model = (
                str(current["model"])
                if current.get("provider") == "litellm"
                and str(current.get("model", "")).startswith("ollama/")
                else None
            )
            vlm_config, ollama_running = _prompt_ollama_vlm(
                allow_back=True, current_litellm_model=current_ollama_model
            )
            if vlm_config is _GO_BACK:
                continue  # back to the provider menu
            return vlm_config, ollama_running

        break

    if vlm_mode == 1:
        vlm_choice = _get_cloud_provider_by_label("VolcEngine (火山引擎)")
        print(f"\n  {_bold('VolcEngine VLM configuration')}")
        vlm_api_key = _prompt_vlm_api_key(vlm_choice.provider, reuse_key, reuse_prompt)
        if not vlm_api_key:
            print(f"  {_red('API key is required')}")
            return None, None
        vlm_model = _prompt_required_input(
            "Model", default=_default_model(vlm_choice.provider, vlm_choice.default_vlm_model)
        )
        vlm_api_base = vlm_choice.default_api_base
        vlm_provider = vlm_choice.provider
    elif vlm_mode == 2:
        vlm_choice = _get_cloud_provider_by_label("BytePlus")
        print(f"\n  {_bold('BytePlus VLM configuration')}")
        vlm_api_key = _prompt_vlm_api_key(vlm_choice.provider, reuse_key, reuse_prompt)
        if not vlm_api_key:
            print(f"  {_red('API key is required')}")
            return None, None
        vlm_model = _prompt_required_input(
            "Model", default=_default_model(vlm_choice.provider, vlm_choice.default_vlm_model)
        )
        vlm_api_base = vlm_choice.default_api_base
        vlm_provider = vlm_choice.provider
    elif vlm_mode == 3:
        vlm_choice = _get_cloud_provider_by_label("OpenAI")
        print(f"\n  {_bold('OpenAI VLM configuration')}")
        vlm_api_key = _prompt_vlm_api_key(vlm_choice.provider, reuse_key, reuse_prompt)
        if not vlm_api_key:
            print(f"  {_red('API key is required')}")
            return None, None
        vlm_model = _prompt_required_input(
            "Model", default=_default_model(vlm_choice.provider, vlm_choice.default_vlm_model)
        )
        vlm_api_base = vlm_choice.default_api_base
        vlm_provider = vlm_choice.provider
    elif vlm_mode == 4:
        _ensure_codex_auth()
        print(f"\n  {_bold('Codex VLM configuration')}")
        vlm_model = _prompt_required_input(
            "Model", default=_default_model("openai-codex", _DEFAULT_CODEX_MODEL)
        )
        vlm_api_base = _DEFAULT_CODEX_BASE_URL
        vlm_api_key = None
        vlm_provider = "openai-codex"
    elif vlm_mode == 5:
        print(f"\n  {_bold('Kimi VLM configuration')}")
        vlm_api_key = _prompt_vlm_api_key("kimi", reuse_key, reuse_prompt)
        if not vlm_api_key:
            print(f"  {_red('API key is required')}")
            return None, None
        vlm_model = _prompt_required_input(
            "Model", default=_default_model("kimi", _DEFAULT_KIMI_MODEL)
        )
        vlm_api_base = _DEFAULT_KIMI_BASE_URL
        vlm_provider = "kimi"
    elif vlm_mode == 6:
        print(f"\n  {_bold('GLM VLM configuration')}")
        vlm_api_key = _prompt_vlm_api_key("glm", reuse_key, reuse_prompt)
        if not vlm_api_key:
            print(f"  {_red('API key is required')}")
            return None, None
        vlm_model = _prompt_required_input(
            "Model", default=_default_model("glm", _DEFAULT_GLM_MODEL)
        )
        vlm_api_base = _DEFAULT_GLM_BASE_URL
        vlm_provider = "glm"
    else:
        print(f"\n  {_bold('Custom OpenAI-compatible VLM configuration')}")
        same_openai = current.get("provider") == "openai"
        vlm_api_base = _prompt_required_input(
            "API Base URL",
            default=str(current["api_base"]) if same_openai and current.get("api_base") else None,
        )
        vlm_api_key = _prompt_vlm_api_key("openai", reuse_key, reuse_prompt)
        vlm_model = _prompt_required_input(
            "Model",
            default=str(current["model"]) if same_openai and current.get("model") else None,
        )
        vlm_provider = "openai"

    vlm_config: dict[str, Any] = {
        "provider": vlm_provider,
        "model": vlm_model,
        "api_base": vlm_api_base,
        "temperature": 0.0,
        "max_retries": 2,
    }
    if vlm_api_key:
        vlm_config["api_key"] = vlm_api_key
    return vlm_config, None


def _cloud_provider_index_for(section: dict[str, Any]) -> int:
    """1-based CLOUD_PROVIDERS index matching an existing config section."""
    provider = str(section.get("provider") or "")
    api_base = str(section.get("api_base") or "")
    for i, p in enumerate(CLOUD_PROVIDERS, 1):
        if p.provider == provider and p.default_api_base == api_base:
            return i
    for i, p in enumerate(CLOUD_PROVIDERS, 1):
        if p.provider == provider:
            return i
    return 1


def _prompt_cloud_embedding(
    *, allow_back: bool = False, current: dict[str, Any] | None = None
) -> dict[str, Any] | None | object:
    """Cloud embedding provider selection.

    Returns the dense-embedding config dict, ``None`` on cancel,
    ``_CUSTOM_SETUP`` when the user picked manual configuration, or
    ``_GO_BACK`` when *allow_back* is set and the user navigated back.
    *current* (the existing dense config, if any) seeds every default so an
    update run can keep values by just pressing Enter.
    """
    current = current or {}
    provider_options = [(p.label, "") for p in CLOUD_PROVIDERS]
    provider_options.append(("Other (manual)", ""))
    default_idx = _cloud_provider_index_for(current) if current else 1
    choice = _prompt_choice(
        "Embedding provider:", provider_options, default=default_idx, allow_back=allow_back
    )

    if choice == 0:
        return _GO_BACK

    if choice > len(CLOUD_PROVIDERS):
        _wizard_custom()
        return _CUSTOM_SETUP

    provider = CLOUD_PROVIDERS[choice - 1]
    same_provider = bool(current) and current.get("provider") == provider.provider

    print(f"\n  {_bold('Embedding configuration')}")
    embedding_api_key = ""
    if same_provider and current.get("api_key"):
        masked = _mask_secret(str(current["api_key"]))
        if _prompt_confirm(f"Keep the existing API key ({masked})?"):
            embedding_api_key = str(current["api_key"])
    if not embedding_api_key:
        embedding_api_key = _prompt_api_key_with_env(_PROVIDER_ENV_KEYS.get(provider.provider))
    if not embedding_api_key:
        print(f"  {_red('API key is required')}")
        return None

    default_model = (
        str(current["model"])
        if same_provider and current.get("model")
        else provider.default_embedding_model
    )
    embedding_model = _prompt_required_input("Model", default=default_model)
    if (
        same_provider
        and embedding_model == current.get("model")
        and isinstance(current.get("dimension"), int)
    ):
        embedding_dim = current["dimension"]
        print(f"  {_dim(f'Dimension: {embedding_dim} (kept from current config)')}")
    elif embedding_model == provider.default_embedding_model:
        embedding_dim = provider.default_embedding_dim
        print(f"  {_dim(f'Dimension: {embedding_dim} (auto-filled for {embedding_model})')}")
    else:
        embedding_dim = _prompt_required_int("Dimension", default=provider.default_embedding_dim)
        if embedding_dim is None:
            print(f"  {_red('Dimension is required')}")
            return None

    return {
        "provider": provider.provider,
        "model": embedding_model,
        "api_key": embedding_api_key,
        "api_base": (
            str(current["api_base"])
            if same_provider and current.get("api_base")
            else provider.default_api_base
        ),
        "dimension": embedding_dim,
    }


def _prompt_llamacpp_embedding() -> LocalGGUFPreset | None:
    """llama.cpp embedding setup: ensure runtime, pick a GGUF preset, download it."""
    print("\n  Checking llama-cpp-python...", end=" ", flush=True)

    if _is_llamacpp_installed():
        print(_green("installed"))
    else:
        print(_yellow("not installed"))
        print(f"\n  {_dim('llama-cpp-python is required for local CPU embedding.')}")
        if _prompt_confirm(f"Install now? ({_PIP_LOCAL_EMBED})"):
            print()
            if _install_llamacpp():
                print(f"  {_green('OK')} llama-cpp-python installed")
            else:
                print(f"  {_red('Installation failed.')}")
                print(f"  {_dim('Try manually: ' + _PIP_LOCAL_EMBED)}")
                if not _prompt_confirm(
                    "Continue anyway? (config will be generated)", default=False
                ):
                    return None
        else:
            print(f"\n  {_dim('Install later: ' + _PIP_LOCAL_EMBED)}")
            if not _prompt_confirm("Continue anyway? (config will be generated)", default=False):
                return None

    model_options: list[tuple[str, str]] = []
    for p in LOCAL_GGUF_PRESETS:
        cached = ""
        try:
            if _check_gguf_model_cached(p.model_name):
                cached = _green(" [downloaded]")
        except Exception:
            pass
        model_options.append(
            (
                p.label,
                f"({p.dimension}d, {p.size_hint}){cached}",
            )
        )

    model_choice = _prompt_choice("Embedding model:", model_options, default=1)
    preset = LOCAL_GGUF_PRESETS[model_choice - 1]

    # Download if not cached
    try:
        if not _check_gguf_model_cached(preset.model_name):
            if _prompt_confirm(
                f"Model '{preset.model_name}' not downloaded yet. Download now? ({preset.size_hint})"
            ):
                print(f"\n  {_dim('Downloading...')}", end=" ", flush=True)
                try:
                    import requests

                    from openviking.models.embedder.local_embedders import (
                        get_local_model_cache_path,
                        get_local_model_spec,
                    )

                    spec = get_local_model_spec(preset.model_name)
                    target = get_local_model_cache_path(preset.model_name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(target.suffix + ".part")
                    with requests.get(spec.download_url, stream=True, timeout=(10, 300)) as resp:
                        resp.raise_for_status()
                        total = int(resp.headers.get("content-length", 0))
                        downloaded = 0
                        with tmp.open("wb") as fh:
                            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    fh.write(chunk)
                                    downloaded += len(chunk)
                                    if total > 0:
                                        pct = downloaded * 100 // total
                                        print(
                                            f"\r  {_dim(f'Downloading... {pct}%')}",
                                            end=" ",
                                            flush=True,
                                        )
                    os.replace(tmp, target)
                    print(f"\r  {_green('OK')} Model downloaded to {target}         ")
                except Exception as exc:
                    print(f"\r  {_yellow(f'Download failed: {exc}')}")
                    print(f"  {_dim('Model will be auto-downloaded on first server start.')}")
            else:
                print(f"  {_dim('Model will be auto-downloaded on first server start.')}")
    except Exception:
        pass

    return preset


# ---------------------------------------------------------------------------
# Wizard flows
# ---------------------------------------------------------------------------


def _wizard_ollama() -> tuple[dict[str, Any] | None, bool | None]:
    """Ollama-based local model setup flow.

    Returns ``(config, ollama_running)`` so later steps (e.g. the query planner)
    can reuse the Ollama state instead of re-running the install flow.
    """
    # Ensure Ollama is installed and running
    ollama_running = _ensure_ollama()

    if not ollama_running:
        if not _prompt_confirm(
            "Continue without Ollama? (config will be generated but models won't be pulled)",
            default=False,
        ):
            return None, ollama_running

    available_models = get_ollama_models() if ollama_running else []

    # System RAM
    ram_gb = _get_system_ram_gb()
    rec_embed_idx, rec_vlm_idx = _get_recommended_indices(ram_gb)
    if ram_gb > 0:
        print(f"\n  {_dim(f'Detected {ram_gb} GB RAM')}")

    # --- Recommended one-shot setup ---
    rec_embed = EMBEDDING_PRESETS[rec_embed_idx]
    rec_vlm = VLM_PRESETS[rec_vlm_idx]
    rec_planner = QUERY_PLANNER_PRESETS[0]
    total_gb = (
        _parse_size_gb(rec_embed.size_hint)
        + _parse_size_gb(rec_vlm.size_hint)
        + _QUERY_PLANNER_DOWNLOAD_GB
    )

    ram_note = f" (for {ram_gb} GB RAM)" if ram_gb > 0 else ""
    print(f"\n  {_bold('Recommended local setup' + ram_note)}")
    print(f"    Embedding      {rec_embed.model}  {_dim('(' + rec_embed.size_hint + ')')}")
    print(f"    VLM            {rec_vlm.ollama_model}  {_dim('(' + rec_vlm.size_hint + ')')}")
    print(f"    Query planner  {rec_planner.ollama_model}  {_dim('(~0.9 GB)')}")
    print(
        f"    {_dim(f'Total download ~{total_gb:.0f} GB — already-downloaded models are skipped')}"
    )

    if _prompt_confirm("Use this recommended setup?"):
        if _check_disk_before_pull(total_gb):
            for model, hint in (
                (rec_embed.model, rec_embed.size_hint),
                (rec_vlm.ollama_model, rec_vlm.size_hint),
                (rec_planner.ollama_model, f"~{_QUERY_PLANNER_DOWNLOAD_GB} GB"),
            ):
                _ensure_model_pulled(model, hint, ollama_running, available_models, ask=False)
            config = _build_ollama_config(rec_embed, rec_vlm, _workspace_path())
            config["query_planner"] = _build_query_planner_config(rec_planner)
            return config, ollama_running
        print(f"  {_dim('Pick smaller models below instead.')}")

    # --- Per-model selection ---
    embedding = _select_embedding_preset(ollama_running, available_models, rec_embed_idx)
    _ensure_model_pulled(embedding.model, embedding.size_hint, ollama_running, available_models)

    vlm = _select_vlm_preset(ollama_running, available_models, rec_vlm_idx)
    _ensure_model_pulled(vlm.ollama_model, vlm.size_hint, ollama_running, available_models)

    return _build_ollama_config(embedding, vlm, _workspace_path()), ollama_running


def _wizard_two_step() -> tuple[dict[str, Any] | None | object, bool | None]:
    """Step-by-step setup: embedding first, then VLM — each cloud or local.

    Returns ``(config, ollama_running)``. ``config`` may also be the
    ``_CUSTOM_SETUP`` sentinel when the user was routed to manual editing, or
    ``_GO_BACK`` when the user backed out to the mode menu.
    """
    while True:
        _rule("Step 1/2 · Embedding — powers semantic search")
        dense, ollama_running = _prompt_embedding_flow(allow_back=True)
        if dense is _GO_BACK:
            return _GO_BACK, None
        if dense is _CUSTOM_SETUP:
            return _CUSTOM_SETUP, None
        if dense is None:
            return None, ollama_running
        print(f"\n  {_green('✓')} Embedding: {_summarize_model(dense)}")

        _rule("Step 2/2 · VLM — parses documents & extracts memories")
        reuse_key = None
        if isinstance(dense, dict) and dense.get("api_key"):
            reuse_key = (dense["provider"], dense["api_key"])
        vlm_config, vlm_ollama = _prompt_cloud_vlm(
            allow_skip=True, reuse_key=reuse_key, allow_back=True
        )
        if vlm_config is _GO_BACK:
            continue  # back to Step 1
        if vlm_config is None:
            return None, ollama_running
        if vlm_ollama is not None:
            ollama_running = vlm_ollama

        if vlm_config is _SKIP_VLM:
            print(f"\n  {_green('✓')} VLM: {_dim('skipped — add one later with init')}")
        else:
            print(f"\n  {_green('✓')} VLM: {_summarize_model(vlm_config)}")

        config: dict[str, Any] = {
            "storage": {"workspace": _workspace_path()},
            "embedding": {"dense": dense},
        }
        if vlm_config is not _SKIP_VLM:
            config["vlm"] = vlm_config
        return config, ollama_running



def _wizard_server(current: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Prompt for server binding, port, and auth (root API key when remote).

    Auth mode is auto-detected by the server from ``root_api_key``: setting a
    key switches to ``api_key`` mode, no key means ``dev`` mode (which the
    server only permits on localhost) — so this step never writes
    ``auth_mode`` explicitly. *current* (the existing server config, if any)
    seeds all defaults so an update run can keep values by pressing Enter.
    """
    current = current or {}
    _rule("Server & auth")
    if current:
        print(f"  {_dim('Current: ' + _summarize_server(current))}")
    print(f"  {_dim('Local: only this machine can reach the server (no auth, dev mode).')}")
    print(f"  {_dim('Remote: bind to 0.0.0.0 for Docker / LAN access — requires API-key auth.')}")

    is_remote_now = str(current.get("host") or "127.0.0.1") not in (
        "127.0.0.1",
        "localhost",
        "::1",
    )
    mode = _prompt_choice(
        "Bind server host to:",
        [
            ("Local (127.0.0.1)", "(default, safer — no auth needed)"),
            ("Remote (0.0.0.0)", "(Docker / remote access — root API key required)"),
        ],
        default=2 if is_remote_now else 1,
    )

    try:
        port_default = int(current.get("port") or 1933)
    except (TypeError, ValueError):
        port_default = 1933
    port = _prompt_required_int("Port", default=port_default)
    if port is None:
        port = port_default

    if mode == 1:
        return {"host": "127.0.0.1", "port": port}

    print(f"\n  {_dim('Non-local binding switches auth to api_key mode: every client must send')}")
    print(f"  {_dim('the root API key as a Bearer token (Authorization: Bearer <key>).')}")

    existing_key = str(current.get("root_api_key") or "")
    key_options: list[tuple[str, str]] = []
    if existing_key:
        key_options.append(("Keep existing key", f"({_mask_secret(existing_key)})"))
    key_options.append(
        ("Generate one for me", "(recommended — 64-char random key, shown once below)")
    )
    key_options.append(("Enter my own", ""))

    key_source = _prompt_choice("Root API key:", key_options, default=1)
    offset = 1 if existing_key else 0

    if existing_key and key_source == 1:
        root_api_key = existing_key
    elif key_source == 1 + offset:
        root_api_key = secrets.token_hex(32)
        print(f"\n  {_green('Generated root API key — copy it now, clients need it:')}")
        print(f"\n    {_bold(root_api_key)}\n")
        print(f"  {_dim('It is also saved in ov.conf (server.root_api_key).')}")
        print(f"  {_dim('Clients authenticate with: Authorization: Bearer <this key>')}")
    else:
        root_api_key = _prompt_api_key("Root API Key")
        if not root_api_key:
            print(f"  {_red('Root API key is required for remote binding')}")
            return None

    return {"host": "0.0.0.0", "port": port, "root_api_key": root_api_key}


def _wizard_query_planner(config_dict: dict[str, Any], ollama_running: bool | None = None) -> None:
    """Optionally configure a lightweight local query-planner model.

    When this setup already uses an Ollama VLM (*ollama_running* is not
    ``None``) the planner rides on that running Ollama at near-zero extra cost,
    so it is recommended and enabled by default. Otherwise (Cloud / non-Ollama
    VLM) it is still offered but off by default and without the recommendation,
    and enabling it runs the Ollama install flow.

    Mutates *config_dict* in place to add ``query_planner``. Prompt selection is
    resolved at retrieval time from the configured model name.
    """
    if "query_planner" in config_dict:
        return  # already configured (e.g. by the recommended Ollama setup)

    _rule("Query planner (optional)")
    print(f"  {_dim('A small local model that plans retrieval before search — skips')}")
    print(f"  {_dim('lookups for small talk and emits focused queries otherwise, saving tokens.')}")

    # Recommend it and default to yes only when an Ollama VLM is already running;
    # cloud / non-Ollama setups would need a fresh Ollama install, so default to
    # no and drop the recommendation.
    has_ollama_vlm = ollama_running is not None
    prompt = "Enable a lightweight local query planner via Ollama?"
    if has_ollama_vlm:
        prompt += " (recommended)"
    if not _prompt_confirm(prompt, default=has_ollama_vlm):
        return

    if ollama_running is None:
        ollama_running = _ensure_ollama()
        if not ollama_running:
            if not _prompt_confirm(
                "Continue without Ollama? (config will be written but the model won't be pulled)",
                default=False,
            ):
                return

    available_models = get_ollama_models() if ollama_running else []

    options = [(p.label, p.size_hint) for p in QUERY_PLANNER_PRESETS]
    choice = _prompt_choice("Query planner model:", options, default=1)
    preset = QUERY_PLANNER_PRESETS[choice - 1]

    _ensure_model_pulled(
        preset.ollama_model,
        f"~{_QUERY_PLANNER_DOWNLOAD_GB} GB",
        bool(ollama_running),
        available_models,
    )

    config_dict["query_planner"] = _build_query_planner_config(preset)


def _wizard_custom() -> dict[str, Any] | None:
    """Custom configuration - point user to example config."""
    config_path = _config_path()
    example = Path(__file__).parent.parent / "examples" / "ov.conf.example"
    if example.exists():
        print(f"\n  Example config: {_cyan(str(example))}")
    print(f"  Config path:    {_cyan(str(config_path))}")

    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))
    if editor:
        if _prompt_confirm(f"Open {config_path} in {editor}?"):
            config_path.parent.mkdir(parents=True, exist_ok=True)
            if not config_path.exists():
                # Copy example as starting point
                try:
                    config_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass
            subprocess.run([editor, str(config_path)], check=False)
    else:
        print(f"\n  {_dim('Set $EDITOR to open the config file automatically.')}")
    return None


# ---------------------------------------------------------------------------
# Partial reconfiguration of an existing ov.conf
# ---------------------------------------------------------------------------


def _prompt_embedding_flow(
    *, allow_back: bool = False, current: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None | object, bool | None]:
    """Embedding selection across all backends (main flow and section updates).

    Returns ``(dense_config, ollama_running)``; ``dense_config`` is None on
    cancel, the ``_CUSTOM_SETUP`` sentinel for manual editing, or ``_GO_BACK``
    when *allow_back* is set and the user backed out of the backend menu.
    *current* (the existing dense config, if any) seeds all defaults.
    """
    current = current or {}
    backend_default = {"ollama": 2, "local": 3}.get(str(current.get("provider") or ""), 1)
    while True:
        choice = _prompt_choice(
            "Embedding setup:",
            [
                ("Cloud API", "(VolcEngine, BytePlus, OpenAI)"),
                ("Local via Ollama", "(no API key, runs on this machine)"),
                ("Lightweight CPU embedding", "(llama.cpp, ~24 MB, no Ollama needed)"),
            ],
            default=backend_default,
            allow_back=allow_back,
        )

        if choice == 0:
            return _GO_BACK, None

        if choice == 1:
            dense = _prompt_cloud_embedding(allow_back=True, current=current)
            if dense is _GO_BACK:
                continue  # back to the backend menu
            return dense, None

        if choice == 2:
            ollama_running = _ensure_ollama()
            if not ollama_running:
                if not _prompt_confirm("Continue without Ollama?", default=False):
                    return None, ollama_running
            available_models = get_ollama_models() if ollama_running else []
            ram_gb = _get_system_ram_gb()
            rec_idx, _ = _get_recommended_indices(ram_gb)
            if current.get("provider") == "ollama":
                for i, p in enumerate(EMBEDDING_PRESETS):
                    if p.model == current.get("model"):
                        rec_idx = i  # default to the currently configured model
                        break
            preset = _select_embedding_preset(
                ollama_running, available_models, rec_idx, allow_back=True
            )
            if preset is None:
                continue  # back to the backend menu
            _ensure_model_pulled(preset.model, preset.size_hint, ollama_running, available_models)
            return _ollama_dense_config(preset), ollama_running

        gguf = _prompt_llamacpp_embedding()
        if gguf is None:
            return None, None
        return {
            "provider": "local",
            "model": gguf.model_name,
            "dimension": gguf.dimension,
        }, None


def _update_existing_config(config_path: Path, section: str) -> int:
    """Update a single section (vlm / embedding / server) of an existing ov.conf."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  {_red(f'Cannot read existing config: {exc}')}")
        print(f"  {_dim('Fix or remove the file, then re-run `openviking-server init`.')}")
        return 1
    if not isinstance(data, dict):
        print(f"  {_red('Existing config is not a JSON object; cannot update in place.')}")
        return 1

    if section == "vlm":
        current_vlm = _config_section(data, "vlm")
        print(f"\n  Current VLM: {_summarize_model(current_vlm)}")
        vlm_config, _ = _prompt_cloud_vlm(current=current_vlm)
        if vlm_config is None:
            print("\n  Setup cancelled.\n")
            return 0
        old_summary, new_summary = _summarize_model(current_vlm), _summarize_model(vlm_config)
        data["vlm"] = vlm_config
    elif section == "embedding":
        old_dense = _config_section(data, "embedding", "dense")
        old_dim = old_dense.get("dimension")
        print(f"\n  Current embedding: {_summarize_model(old_dense)}")
        dense, _ = _prompt_embedding_flow(current=old_dense)
        if dense is _CUSTOM_SETUP:
            return 0
        if dense is None:
            print("\n  Setup cancelled.\n")
            return 0
        if old_dim and dense.get("dimension") != old_dim:
            print(
                f"\n  {_yellow('Embedding dimension changes from ' + str(old_dim) + ' to ' + str(dense.get('dimension')) + '.')}"
            )
            print(
                f"  {_yellow('Existing vector indexes become unusable — data must be re-ingested.')}"
            )
            if not _prompt_confirm("Continue?", default=False):
                print("\n  Setup cancelled.\n")
                return 0
        old_summary, new_summary = _summarize_model(old_dense), _summarize_model(dense)
        data.setdefault("embedding", {})["dense"] = dense
    else:  # server
        current_server = _config_section(data, "server")
        server_dict = _wizard_server(current=current_server)
        if server_dict is None:
            print("\n  Setup cancelled.\n")
            return 0
        old_summary, new_summary = (
            _summarize_server(current_server),
            _summarize_server(server_dict),
        )
        data["server"] = server_dict

    print(f"\n  {_bold('Change:')} {old_summary}")
    print(f"      {_cyan('→')}    {new_summary}")

    if not _prompt_confirm("\n  Save configuration?"):
        print("\n  Setup cancelled.\n")
        return 0
    if not _write_config(data, config_path):
        return 1

    print(f"  {_green('OK')} Configuration updated\n")
    _post_save_actions()
    return 0


# ---------------------------------------------------------------------------
# Post-save actions
# ---------------------------------------------------------------------------


def _post_save_actions() -> None:
    """Offer to validate the fresh config and start the server right away."""
    if _prompt_confirm("Validate the setup now? (runs `openviking-server doctor`)"):
        try:
            from openviking_cli.doctor import run_doctor

            run_doctor()
        except Exception as exc:
            print(f"  {_yellow(f'doctor failed to run: {exc}')}")

    if _prompt_confirm("Start the server now?", default=False):
        # Replace the wizard process with the server so Ctrl-C etc. behave
        # exactly as a direct `openviking-server` invocation.
        os.execv(sys.executable, [sys.executable, "-m", "openviking_cli.server_bootstrap"])


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def _summarize_model(section: dict[str, Any]) -> str:
    """One-line, secret-free description of a model config block."""
    if not section:
        return _dim("(not configured)")
    provider = section.get("provider", "?")
    model = section.get("model", "?")
    text = f"{provider} · {model}"
    if section.get("dimension"):
        text += f" ({section['dimension']}d)"
    return text


def _summarize_server(server: dict[str, Any]) -> str:
    """One-line, secret-free description of the server config block."""
    if not server:
        return _dim("(defaults: 127.0.0.1:1933 · auth dev)")
    host = server.get("host", "127.0.0.1")
    port = server.get("port", 1933)
    auth = "api_key (root key set)" if server.get("root_api_key") else "dev (no auth)"
    return f"{host}:{port} · auth {auth}"


def _config_section(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Safely walk nested dict keys, returning {} on any non-dict hop."""
    node: Any = data
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def _load_config_data(config_path: Path) -> dict[str, Any] | None:
    """Best-effort read of the existing ov.conf; None when unreadable."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _print_current_config(data: dict[str, Any]) -> None:
    """Show the non-secret parts of the existing configuration."""
    print(f"\n  {_bold('Current configuration')}")
    print(f"    Embedding:     {_summarize_model(_config_section(data, 'embedding', 'dense'))}")
    print(f"    VLM:           {_summarize_model(_config_section(data, 'vlm'))}")
    print(f"    Query planner: {_summarize_model(_config_section(data, 'query_planner'))}")
    print(f"    Server:        {_summarize_server(_config_section(data, 'server'))}")


def run_init() -> int:
    """Run the interactive setup wizard."""
    config_path = _config_path()
    workspace = _workspace_path()

    _print_banner()
    print(f"  {_dim(f'Data will be stored under {workspace} unless you edit ov.conf later.')}\n")

    # Existing config: show what is configured and offer section-level updates
    # instead of forcing a redo.
    if config_path.exists():
        print(f"  {_yellow('Existing config found:')} {config_path}")
        data = _load_config_data(config_path)
        vlm_now = emb_now = server_now = ""
        if data is not None:
            _print_current_config(data)
            vlm_now = f"(now: {_summarize_model(_config_section(data, 'vlm'))})"
            emb_now = f"(now: {_summarize_model(_config_section(data, 'embedding', 'dense'))})"
            server_now = f"(now: {_summarize_server(_config_section(data, 'server'))})"
        action = _prompt_choice(
            "What would you like to do?",
            [
                ("Start over", "(full setup, current config backed up as .bak)"),
                ("Update VLM", vlm_now),
                ("Update embedding", emb_now),
                ("Update server & auth", server_now),
                ("Cancel", ""),
            ],
            default=1,
        )
        if action == 5:
            print("  Setup cancelled.\n")
            return 0
        if action in (2, 3, 4):
            section = {2: "vlm", 3: "embedding", 4: "server"}[action]
            return _update_existing_config(config_path, section)

    config_dict: dict[str, Any] | None = None
    # Tracks whether Ollama was already set up by the chosen mode, so the query
    # planner reuses that state instead of re-running the install flow. ``None``
    # means the mode never touched Ollama (e.g. Cloud).
    ollama_running: bool | None = None

    # Setup mode; ← inside step-by-step returns here.
    while True:
        mode = _prompt_choice(
            "Choose setup mode:",
            [
                (
                    "Step-by-step setup",
                    "(pick embedding & VLM separately — cloud, local, or mixed)",
                ),
                ("Recommended local setup", "(all-Ollama, sized to your RAM, one confirm)"),
                ("Manual", "(edit ov.conf directly)"),
            ],
            default=1,
        )

        if mode == 1:
            result, ollama_running = _wizard_two_step()
            if result is _GO_BACK:
                continue  # back to the mode menu
            if result is _CUSTOM_SETUP:
                return 0
            config_dict = result  # type: ignore[assignment]
        elif mode == 2:
            config_dict, ollama_running = _wizard_ollama()
        else:
            _wizard_custom()
            return 0
        break

    if config_dict is None:
        print("\n  Setup cancelled.\n")
        return 0

    _wizard_query_planner(config_dict, ollama_running)

    server_dict = _wizard_server()
    if server_dict is None:
        print("\n  Setup cancelled.\n")
        return 0
    config_dict["server"] = server_dict

    # Summary — providers/models/dimensions are shown, secrets never are.
    emb = config_dict.get("embedding", {}).get("dense", {})
    vlm = config_dict.get("vlm", {})

    _rule("Summary")
    print(f"    Embedding:     {_summarize_model(emb)}")
    if emb.get("model_path"):
        print("    Model path: custom local model (hidden)")
    print(f"    VLM:           {_summarize_model(vlm)}")
    planner = config_dict.get("query_planner", {})
    print(f"    Query planner: {_summarize_model(planner)}")
    print(f"    Server:        {_summarize_server(server_dict)}")
    if server_dict.get("root_api_key"):
        print("    Root API key: configured (hidden)")
    print("    Workspace:  configured (hidden)")
    print("    Config:     default config location")

    if not _prompt_confirm("\n  Save configuration?"):
        print("\n  Setup cancelled.\n")
        return 0

    # Write
    if not _write_config(config_dict, config_path):
        return 1

    print(f"  {_green('OK')} Configuration written to the default config location\n")

    # Post-init tips
    print(f"  {_bold('Next steps:')}")
    if emb.get("provider") == "local":
        print(f"    Install runtime:   {_cyan(_PIP_LOCAL_EMBED)}")
    print(f"    Start the server:  {_cyan('openviking-server')}")
    print(f"    Validate setup:    {_cyan('openviking-server doctor')}")
    print()

    _post_save_actions()

    return 0


def main() -> int:
    """Entry point for ``openviking-server init``."""
    try:
        return run_init()
    except KeyboardInterrupt:
        print("\n\n  Setup cancelled.\n")
        return 130
