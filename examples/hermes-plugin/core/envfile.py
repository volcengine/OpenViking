"""Profile ``.env`` writes that keep secrets on one line and 0600."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from .log import get_logger

logger = get_logger()


def _secure_secret_file(path: Path, *, create: bool = False) -> None:
    """chmod 0600 a secret-bearing file; with ``create`` also pre-create it BEFORE writing
    (write-then-chmod leaves a window where the fresh file is world-readable under the umask)."""
    try:
        if create and not path.exists():
            os.close(os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600))
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as e:
        logger.debug("Could not %s secret file %s: %s", "pre-create" if create else "restrict permissions on", path, e)


def _env_line_safe(value: Any) -> str:
    """Strip CR/LF/NUL so a value can only occupy its single ``KEY=VALUE`` line — an
    embedded line break would be re-parsed as a separate variable (secret injection)."""
    text = value if isinstance(value, str) else str(value)
    return "".join(text.replace("\x00", "").splitlines())


def _write_env_vars(env_path: Path, env_writes: dict, remove_keys: tuple[str, ...] = ()) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    remove_set = set(remove_keys) - set(env_writes)
    # utf-8-sig + surrogateescape: a Windows editor may leave a BOM (breaks the
    # first key match) or save cp1252; round-trip undecodable bytes unchanged so
    # updating one credential cannot corrupt an unrelated value.
    # newline="": universal-newline translation on read would turn every CRLF
    # into LF, so the file's real ending could never be detected below.
    if env_path.exists():
        with env_path.open("r", encoding="utf-8-sig", errors="surrogateescape", newline="") as fh:
            existing = fh.read()
    else:
        existing = ""
    # Only physical line endings separate records; other separators belong to values.
    existing_lines = re.split(r"\r\n|\r|\n", existing)
    if existing_lines[-1] == "":
        existing_lines.pop()
    # Adopt the file's own line ending instead of the platform default: writing
    # one variable must not rewrite every untouched line from LF to CRLF.
    eol = "\r\n" if "\r\n" in existing else "\n"  # Mixed endings use CRLF if present.
    updated_keys = set()
    new_lines = []
    for line in existing_lines:
        key_match = line.split("=", 1)[0].strip() if "=" in line else ""
        if key_match in remove_set:
            continue
        if key_match in env_writes:
            updated_keys.add(key_match)
        new_lines.append(f"{key_match}={_env_line_safe(env_writes[key_match])}" if key_match in env_writes else line)
    new_lines += [f"{key}={_env_line_safe(val)}" for key, val in env_writes.items() if key not in updated_keys]
    _secure_secret_file(env_path, create=True)
    # newline="": ``eol`` above is the only thing allowed to decide the line
    # ending, so text mode cannot translate it on the way out.
    with env_path.open("w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(eol.join(new_lines) + (eol if new_lines else ""))
    _secure_secret_file(env_path)
