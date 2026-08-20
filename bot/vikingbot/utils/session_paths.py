"""Portable filesystem names derived from logical Bot session keys."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vikingbot.config.schema import SessionKey


_WINDOWS_UNSAFE_CHARS = frozenset('<>:"/\\|?*%')
_WINDOWS_RESERVED_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
# Leave room for file extensions and atomic-write suffixes below NAME_MAX=255.
_MAX_COMPONENT_BYTES = 180
_LONG_NAME_HASH_LENGTH = 16


def _percent_encode(character: str) -> str:
    return "".join(f"%{byte:02X}" for byte in character.encode("utf-8", errors="surrogatepass"))


def portable_path_component(value: str) -> str:
    """Return a readable path component valid on Windows, macOS, and Linux.

    The logical value is not changed. Only its filesystem representation is
    escaped, using UTF-8 percent encoding for Windows-reserved characters,
    controls, literal percent signs, and trailing spaces or periods.
    """
    chunks: list[str] = []
    for index, character in enumerate(value):
        codepoint = ord(character)
        is_control = codepoint < 32 or codepoint == 127
        is_surrogate = 0xD800 <= codepoint <= 0xDFFF
        is_trailing_windows_char = index == len(value) - 1 and character in {".", " "}
        if (
            character not in _WINDOWS_UNSAFE_CHARS
            and not is_control
            and not is_surrogate
            and not is_trailing_windows_char
        ):
            chunks.append(character)
        else:
            chunks.append(_percent_encode(character))

    candidate = "".join(chunks)
    stem = candidate.split(".", 1)[0].rstrip(" .").upper()
    if stem in _WINDOWS_RESERVED_STEMS and candidate:
        candidate = f"{_percent_encode(candidate[0])}{candidate[1:]}"
        chunks = [candidate]

    if len(candidate.encode("utf-8", errors="surrogatepass")) <= _MAX_COMPONENT_BYTES:
        return candidate

    digest = hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()
    suffix = f"~{digest[:_LONG_NAME_HASH_LENGTH]}"
    remaining = _MAX_COMPONENT_BYTES - len(suffix)
    prefix: list[str] = []
    for chunk in chunks:
        chunk_size = len(chunk.encode("utf-8", errors="surrogatepass"))
        if chunk_size > remaining:
            break
        prefix.append(chunk)
        remaining -= chunk_size
    return f"{''.join(prefix).rstrip(' .')}{suffix}"


def portable_session_name(session_key: SessionKey) -> str:
    """Return the canonical filesystem name for a logical session key."""
    return portable_path_component(session_key.safe_name())


def workspace_name(session_key: SessionKey, sandbox_mode: Any, *, portable: bool) -> str:
    """Return a workspace directory name for the configured sandbox mode."""
    mode = str(getattr(sandbox_mode, "value", sandbox_mode))
    if mode == "shared":
        return "shared"
    logical_name = session_key.channel_key() if mode == "per-channel" else session_key.safe_name()
    return portable_path_component(logical_name) if portable else logical_name


def resolve_workspace_path(parent: Path, session_key: SessionKey, sandbox_mode: Any) -> Path:
    """Prefer a portable workspace while reusing a safe existing legacy one."""
    canonical = parent / workspace_name(session_key, sandbox_mode, portable=True)
    if canonical.exists():
        return canonical

    legacy_name = workspace_name(session_key, sandbox_mode, portable=False)
    if "/" in legacy_name or "\\" in legacy_name:
        return canonical
    legacy = parent / legacy_name
    return legacy if legacy != canonical and legacy.exists() else canonical
