# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Shared upload utilities for directory and file uploading to VikingFS."""

import os
import re
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple, Union

from openviking.parse.parsers.constants import (
    ADDITIONAL_TEXT_EXTENSIONS,
    CODE_EXTENSIONS,
    DOCUMENTATION_EXTENSIONS,
    IGNORE_DIRS,
    IGNORE_EXTENSIONS,
    TEXT_ENCODINGS,
    UTF8_VARIANTS,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


# Common text files that have no extension but should be treated as text.
_EXTENSIONLESS_TEXT_NAMES: Set[str] = {
    "LICENSE",
    "LICENCE",
    "MAKEFILE",
    "DOCKERFILE",
    "VAGRANTFILE",
    "GEMFILE",
    "RAKEFILE",
    "PROCFILE",
    "CODEOWNERS",
    "AUTHORS",
    "CONTRIBUTORS",
    "CHANGELOG",
    "CHANGES",
    "NEWS",
    "NOTICE",
    "TODO",
}


def is_text_file(file_path: Union[str, Path]) -> bool:
    """Return True when the file extension is treated as text content."""
    p = Path(file_path)
    extension = p.suffix.lower()
    if extension:
        return (
            extension in CODE_EXTENSIONS
            or extension in DOCUMENTATION_EXTENSIONS
            or extension in ADDITIONAL_TEXT_EXTENSIONS
        )
    # Extensionless files: check against known text file names (case-insensitive).
    return p.name.upper() in _EXTENSIONLESS_TEXT_NAMES


def detect_and_convert_encoding(content: bytes, file_path: Union[str, Path] = "") -> bytes:
    """Detect text encoding and normalize content to UTF-8 when needed."""
    if not is_text_file(file_path):
        return content

    try:
        # Check for potential binary content (null bytes in first 8KB)
        # Binary files often contain null bytes which can cause issues
        sample_size = min(8192, len(content))
        if b'\x00' in content[:sample_size]:
            null_count = content[:sample_size].count(b'\x00')
            # If more than 5% null bytes in sample, likely binary - don't process
            if null_count / sample_size > 0.05:
                logger.debug(f"Detected binary content in {file_path} (null bytes: {null_count}), skipping encoding detection")
                return content

        detected_encoding: Optional[str] = None
        for encoding in TEXT_ENCODINGS:
            try:
                decoded = content.decode(encoding)
                # Additional validation: check for control characters that suggest binary
                control_chars = sum(1 for c in decoded[:1000] if ord(c) < 32 and c not in '\t\n\r')
                if control_chars / min(1000, len(decoded)) > 0.05:  # More than 5% control chars
                    continue
                detected_encoding = encoding
                break
            except UnicodeDecodeError:
                continue

        if detected_encoding is None:
            logger.warning(f"Encoding detection failed for {file_path}: no matching encoding found")
            return content

        if detected_encoding not in UTF8_VARIANTS:
            decoded_content = content.decode(detected_encoding, errors="replace")
            # Remove null bytes from decoded content as they can cause issues downstream
            if '\x00' in decoded_content:
                decoded_content = decoded_content.replace('\x00', '')
                logger.debug(f"Removed null bytes from decoded content in {file_path}")
            content = decoded_content.encode("utf-8")
            logger.debug(f"Converted {file_path} from {detected_encoding} to UTF-8")

        return content
    except Exception as exc:
        logger.warning(f"Encoding detection failed for {file_path}: {exc}")
        return content


def should_skip_file(
    file_path: Path,
    max_file_size: int = 10 * 1024 * 1024,
    ignore_extensions: Optional[Set[str]] = None,
) -> Tuple[bool, str]:
    """Return whether to skip a file and the reason for skipping."""
    effective_ignore_extensions = (
        ignore_extensions if ignore_extensions is not None else IGNORE_EXTENSIONS
    )

    if file_path.name.startswith("."):
        return True, "hidden file"

    if file_path.is_symlink():
        return True, "symbolic link"

    extension = file_path.suffix.lower()
    if extension in effective_ignore_extensions:
        return True, f"ignored extension: {extension}"

    try:
        file_size = file_path.stat().st_size
        if file_size > max_file_size:
            return True, f"file too large: {file_size} bytes"
        if file_size == 0:
            return True, "empty file"
    except OSError as exc:
        return True, f"os error: {exc}"

    return False, ""


def should_skip_directory(
    dir_name: str,
    ignore_dirs: Optional[Set[str]] = None,
) -> bool:
    """Return True when a directory should be skipped during traversal."""
    effective_ignore_dirs = ignore_dirs if ignore_dirs is not None else IGNORE_DIRS
    return dir_name in effective_ignore_dirs or dir_name.startswith(".")


_UNSAFE_PATH_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _sanitize_rel_path(rel_path: str) -> str:
    """Normalize a relative path and reject unsafe components.

    Uses OS-independent checks so that Windows-style drive prefixes and
    backslash separators are rejected even when running on Linux/macOS.
    """
    if not rel_path:
        raise ValueError(f"Unsafe relative path rejected: {rel_path!r}")
    # Reject absolute paths (Unix or Windows style)
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise ValueError(f"Unsafe relative path rejected: {rel_path}")
    # Reject Windows drive letters (C:\..., C:foo)
    if _DRIVE_RE.match(rel_path):
        raise ValueError(f"Unsafe relative path rejected: {rel_path}")
    # Reject parent-directory traversal (../ or ..\)
    if _UNSAFE_PATH_RE.search(rel_path):
        raise ValueError(f"Unsafe relative path rejected: {rel_path}")
    # Normalize to forward slashes
    return rel_path.replace("\\", "/")


async def upload_text_files(
    file_paths: List[Tuple[Path, str]],
    viking_uri_base: str,
    viking_fs: Any,
) -> Tuple[int, List[str]]:
    """Upload text files to VikingFS and return uploaded count with warnings."""
    uploaded_count = 0
    warnings: List[str] = []

    for file_path, rel_path in file_paths:
        try:
            safe_rel = _sanitize_rel_path(rel_path)
            target_uri = f"{viking_uri_base}/{safe_rel}"
            content = file_path.read_bytes()
            content = detect_and_convert_encoding(content, file_path)
            await viking_fs.write_file_bytes(target_uri, content)
            uploaded_count += 1
        except Exception as exc:
            warning = f"Failed to upload {file_path}: {exc}"
            warnings.append(warning)
            logger.warning(warning)

    return uploaded_count, warnings


async def upload_directory(
    local_dir: Path,
    viking_uri_base: str,
    viking_fs: Any,
    ignore_dirs: Optional[Set[str]] = None,
    ignore_extensions: Optional[Set[str]] = None,
    max_file_size: int = 10 * 1024 * 1024,
) -> Tuple[int, List[str]]:
    """Upload an entire directory recursively and return uploaded count with warnings."""
    effective_ignore_dirs = ignore_dirs if ignore_dirs is not None else IGNORE_DIRS
    effective_ignore_extensions = (
        ignore_extensions if ignore_extensions is not None else IGNORE_EXTENSIONS
    )

    uploaded_count = 0
    warnings: List[str] = []

    await viking_fs.mkdir(viking_uri_base, exist_ok=True)

    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [
            dir_name
            for dir_name in dirs
            if not should_skip_directory(dir_name, ignore_dirs=effective_ignore_dirs)
        ]

        for file_name in files:
            file_path = Path(root) / file_name
            should_skip, _ = should_skip_file(
                file_path,
                max_file_size=max_file_size,
                ignore_extensions=effective_ignore_extensions,
            )
            if should_skip:
                continue

            rel_path_str = str(file_path.relative_to(local_dir)).replace(os.sep, "/")
            try:
                safe_rel = _sanitize_rel_path(rel_path_str)
                target_uri = f"{viking_uri_base}/{safe_rel}"
                content = file_path.read_bytes()
                content = detect_and_convert_encoding(content, file_path)
                await viking_fs.write_file_bytes(target_uri, content)
                uploaded_count += 1
            except Exception as exc:
                warning = f"Failed to upload {file_path}: {exc}"
                warnings.append(warning)
                logger.warning(warning)

    return uploaded_count, warnings
