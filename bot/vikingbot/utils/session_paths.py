"""Portable filesystem names derived from logical Bot session keys."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import unquote

if TYPE_CHECKING:
    from vikingbot.config.schema import SessionKey


_WINDOWS_UNSAFE_CHARS = frozenset('<>:"/\\|?*%')
_WINDOWS_INVALID_CHARS = _WINDOWS_UNSAFE_CHARS - {"%"}
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
DEFAULT_OPENAPI_CHANNEL_ID = "default"
# Scoped OpenAPI generations use a colon. The Bot's /new command rotates the
# OpenViking history by appending a hyphen and a short nonce.
_STUDIO_OPENAPI_CHAT_ID = re.compile(
    r"(vikingbot-web-(?:[0-9a-f]{8}-)?"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"(?::[0-9a-f]{32})?(?:-[0-9a-f]{8})?",
    re.IGNORECASE,
)


def _percent_encode(character: str) -> str:
    return "".join(f"%{byte:02X}" for byte in character.encode("utf-8", errors="surrogatepass"))


def _escaped_path_component(value: str) -> tuple[str, list[str]]:
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
    return candidate, chunks


def _hashed_path_component(value: str, candidate: str, chunks: list[str]) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()
    suffix = f"~{digest[:_LONG_NAME_HASH_LENGTH]}"
    if len(f"{candidate}{suffix}".encode("utf-8", errors="surrogatepass")) <= (
        _MAX_COMPONENT_BYTES
    ):
        return f"{candidate}{suffix}"

    remaining = _MAX_COMPONENT_BYTES - len(suffix)
    prefix: list[str] = []
    for chunk in chunks:
        chunk_size = len(chunk.encode("utf-8", errors="surrogatepass"))
        if chunk_size > remaining:
            break
        prefix.append(chunk)
        remaining -= chunk_size
    return f"{''.join(prefix).rstrip(' .')}{suffix}"


def portable_path_component(value: str) -> str:
    """Return a collision-resistant path component for supported filesystems.

    The logical value is not changed. Only its filesystem representation is
    escaped, using UTF-8 percent encoding for Windows-reserved characters,
    controls, literal percent signs, and trailing spaces or periods. A digest
    of the original UTF-8 bytes prevents collisions on case-insensitive and
    Unicode-normalizing filesystems.
    """
    candidate, chunks = _escaped_path_component(value)
    return _hashed_path_component(value, candidate, chunks)


def openapi_principal_scope(principal: Literal["dev", "standalone"] | tuple[str, str]) -> str:
    if isinstance(principal, tuple):
        account_id, user_id = principal
        identity = f"openviking:{account_id}:{user_id}"
    else:
        identity = principal
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def studio_session_links(session_ids: set[str], principal_scope: str) -> dict[str, str]:
    """Associate stored OpenAPI histories with the Studio sessions in the same listing."""
    prefix = f"cli__{DEFAULT_OPENAPI_CHANNEL_ID}__{principal_scope}"
    links = {}
    for storage_id in session_ids:
        if not storage_id.startswith(prefix):
            continue
        encoded_key, digest_separator, _ = storage_id.rpartition("~")
        # Older sessions and the compact hook without Working Memory use the logical key directly.
        logical_key = unquote(encoded_key) if digest_separator else storage_id
        chat_key = logical_key.removeprefix(f"{prefix}:")
        match = _STUDIO_OPENAPI_CHAT_ID.fullmatch(chat_key)
        if match is None or match[1] not in session_ids:
            continue
        if digest_separator and portable_path_component(logical_key) != storage_id:
            continue
        links[storage_id] = match[1]
    return links


def is_windows_safe_path_component(value: str) -> bool:
    """Return whether an existing logical identifier can be a Windows path component."""
    if not value or value in {".", ".."}:
        return False
    if len(value.encode("utf-8", errors="surrogatepass")) > _MAX_COMPONENT_BYTES:
        return False
    if value.endswith((".", " ")):
        return False

    for character in value:
        codepoint = ord(character)
        if (
            character in _WINDOWS_INVALID_CHARS
            or codepoint < 32
            or codepoint == 127
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            return False

    stem = value.split(".", 1)[0].rstrip(" .").upper()
    return stem not in _WINDOWS_RESERVED_STEMS


def find_exact_child(parent: Path, name: str) -> Path | None:
    """Find an existing child without case-folded or normalized path lookup."""
    candidate = parent / name
    if candidate.parent != parent:
        return None
    try:
        return next((child for child in parent.iterdir() if child.name == name), None)
    except FileNotFoundError:
        return None


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
    legacy = find_exact_child(parent, legacy_name)
    return legacy if legacy is not None and legacy != canonical else canonical
