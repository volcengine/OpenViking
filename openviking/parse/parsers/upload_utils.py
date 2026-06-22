# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared upload utilities for directory and file uploading to VikingFS."""

import asyncio
import os
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple, Union

from openviking.parse.gitignore import GitignoreMatcher
from openviking.parse.parsers.constants import (
    ADDITIONAL_TEXT_EXTENSIONS,
    CODE_EXTENSIONS,
    DOCUMENTATION_EXTENSIONS,
    IGNORE_DIRS,
    IGNORE_EXTENSIONS,
    TEXT_ENCODINGS,
    UTF8_VARIANTS,
)
from openviking.utils.path_safety import safe_join_viking_uri, sanitize_relative_viking_path
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
    "BUILD",
}


_CP1252_PUNCTUATION_CODEPOINTS = {
    0x20AC,  # euro sign
    0x2018,
    0x2019,
    0x201C,
    0x201D,
    0x2013,
    0x2014,
}
_CP1252_MOJIBAKE_CODEPOINTS = {
    0x0192,
    0x201A,
    0x201E,
    0x2020,
    0x2021,
    0x02C6,
    0x2030,
    0x0160,
    0x2039,
    0x0152,
    0x017D,
    0x2022,
    0x02DC,
    0x2122,
    0x0161,
    0x203A,
    0x0153,
    0x017E,
    0x0178,
}
_LATIN_FALLBACK_ENCODINGS = {"cp1252", "iso-8859-1", "latin-1"}


def _is_control_heavy(decoded: str) -> bool:
    sample = decoded[:1000]
    if not sample:
        return False

    control_chars = sum(1 for char in sample if ord(char) < 32 and char not in "\t\n\r")
    return control_chars / len(sample) > 0.05


def _encoding_score(decoded: str, encoding: str) -> int:
    """Lower score means the decoded text looks more plausible for this codec."""
    sample = decoded[:1000]
    sample_length = max(1, len(sample))

    c1_controls = sum(1 for char in sample if 0x80 <= ord(char) <= 0x9F)
    cjk = sum(1 for char in sample if "\u4e00" <= char <= "\u9fff")
    hiragana = sum(1 for char in sample if "\u3040" <= char <= "\u309f")
    katakana = sum(1 for char in sample if "\u30a0" <= char <= "\u30ff")
    halfwidth_katakana = sum(1 for char in sample if "\uff66" <= char <= "\uff9f")
    kana = hiragana + katakana + halfwidth_katakana
    hangul = sum(1 for char in sample if "\uac00" <= char <= "\ud7af")
    extended_latin = sum(1 for char in sample if "\u00a0" <= char <= "\u024f")
    cp1252_punctuation = sum(1 for char in sample if ord(char) in _CP1252_PUNCTUATION_CODEPOINTS)
    cp1252_mojibake = sum(1 for char in sample if ord(char) in _CP1252_MOJIBAKE_CODEPOINTS)

    score = c1_controls * 30

    if encoding in _LATIN_FALLBACK_ENCODINGS:
        score += (cjk + kana + hangul) * 40
        if extended_latin / sample_length > 0.20:
            score += extended_latin * 8
        score += cp1252_mojibake * 15

    if encoding in {"iso-8859-1", "latin-1"}:
        score += cp1252_punctuation * 30
    elif encoding == "cp1252":
        score -= cp1252_punctuation * 3

    if encoding == "shift_jis":
        score -= (hiragana + katakana) * 10
        score += halfwidth_katakana * 8 + hangul * 30
    elif encoding == "euc-kr":
        score += 25
        score -= hangul * 10
        score += kana * 30 + cjk * 35
    elif encoding == "big5":
        score += kana * 30 + hangul * 30
    elif encoding.startswith("gb"):
        score += kana * 30 + hangul * 30

    return score


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
    if not content:
        return content

    try:
        # Check for potential binary content (null bytes in first 8KB)
        # Binary files often contain null bytes which can cause issues
        sample_size = min(8192, len(content))
        if b"\x00" in content[:sample_size]:
            null_count = content[:sample_size].count(b"\x00")
            # If more than 5% null bytes in sample, likely binary - don't process
            if null_count / sample_size > 0.05:
                logger.debug(
                    f"Detected binary content in {file_path} (null bytes: {null_count}), skipping encoding detection"
                )
                return content

        if content.startswith(b"\xef\xbb\xbf"):
            decoded_content = content.decode("utf-8-sig")
            return decoded_content.encode("utf-8")

        try:
            content.decode("utf-8")
            return content
        except UnicodeDecodeError:
            pass

        candidates: List[Tuple[int, int, str, str]] = []
        for encoding_index, encoding in enumerate(TEXT_ENCODINGS):
            if encoding in UTF8_VARIANTS:
                continue
            try:
                decoded = content.decode(encoding)
                if _is_control_heavy(decoded):
                    continue
                candidates.append(
                    (_encoding_score(decoded, encoding), encoding_index, encoding, decoded)
                )
            except UnicodeDecodeError:
                continue

        if not candidates:
            logger.warning(f"Encoding detection failed for {file_path}: no matching encoding found")
            return content

        _, _, detected_encoding, decoded_content = min(candidates)
        # Remove null bytes from decoded content as they can cause issues downstream
        if "\x00" in decoded_content:
            decoded_content = decoded_content.replace("\x00", "")
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


def _sanitize_rel_path(rel_path: str) -> str:
    """Compatibility wrapper for existing upload utility callers/tests."""
    return sanitize_relative_viking_path(rel_path)


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
            target_uri = safe_join_viking_uri(viking_uri_base, rel_path)
            content = file_path.read_bytes()
            content = detect_and_convert_encoding(content, file_path)
            await viking_fs.write_file_bytes(target_uri, content)
            uploaded_count += 1
        except Exception as exc:
            warning = f"Failed to upload {file_path}: {exc}"
            warnings.append(warning)
            logger.warning(warning)

    return uploaded_count, warnings


_UPLOAD_CONCURRENCY = 8


async def upload_directory(
    local_dir: Path,
    viking_uri_base: str,
    viking_fs: Any,
    ignore_dirs: Optional[Set[str]] = None,
    ignore_extensions: Optional[Set[str]] = None,
    max_file_size: int = 10 * 1024 * 1024,
) -> Tuple[int, List[str]]:
    """Upload an entire directory recursively and return uploaded count with warnings.

    Optimized: collects all files in one pass, pre-creates directories upfront,
    then uploads all files concurrently (up to _UPLOAD_CONCURRENCY at a time).
    """
    effective_ignore_dirs = ignore_dirs if ignore_dirs is not None else IGNORE_DIRS
    effective_ignore_extensions = (
        ignore_extensions if ignore_extensions is not None else IGNORE_EXTENSIONS
    )
    gitignore_matcher = GitignoreMatcher(local_dir)

    warnings: List[str] = []

    # --- Phase 1: Collect files and unique parent directory URIs in one pass ---
    files_to_upload: List[Tuple[Path, str]] = []  # (local_path, target_uri)
    parent_uris: Set[str] = {viking_uri_base}

    for root, dirs, files in os.walk(local_dir):
        dir_path = Path(root)
        dir_spec = gitignore_matcher.spec_for_dir(dir_path)

        # Prune subdirectories in-place so os.walk won't descend into them
        kept = []
        for d in dirs:
            sub_dir_path = dir_path / d
            should_skip = should_skip_directory(d, ignore_dirs=effective_ignore_dirs)
            if should_skip:
                continue

            if gitignore_matcher.is_ignored_dir(sub_dir_path, dir_spec):
                continue

            kept.append(d)

        dirs[:] = kept

        for file_name in files:
            file_path = dir_path / file_name
            should_skip, _ = should_skip_file(
                file_path,
                max_file_size=max_file_size,
                ignore_extensions=effective_ignore_extensions,
            )
            if should_skip:
                continue

            if gitignore_matcher.is_ignored_file(file_path, dir_spec):
                continue

            rel_path_str = str(file_path.relative_to(local_dir)).replace(os.sep, "/")
            try:
                target_uri = safe_join_viking_uri(viking_uri_base, rel_path_str)
            except ValueError as exc:
                warning = f"Skipping {file_path}: {exc}"
                warnings.append(warning)
                logger.warning(warning)
                continue
            files_to_upload.append((file_path, target_uri))
            parent_uris.add(target_uri.rsplit("/", 1)[0])

    # --- Phase 2: Pre-create all directories ---
    # Memoized mkdir: each unique VikingFS path is created at most once.
    # This is equivalent to _ensure_parent_dirs but avoids redundant HTTP calls
    # by tracking already-processed paths across all directories.
    _created: Set[str] = set()

    for dir_uri in sorted(parent_uris):
        if dir_uri in _created:
            continue
        try:
            await viking_fs.mkdir(dir_uri, exist_ok=True)
            _created.add(dir_uri)
        except Exception as e:
            if "already" in str(e).lower():
                _created.add(dir_uri)
            else:
                logger.warning(f"Failed to create directory {dir_uri}: {e}")

    # --- Phase 3: Upload files concurrently ---
    sem = asyncio.Semaphore(_UPLOAD_CONCURRENCY)
    errors: List[Optional[str]] = [None] * len(files_to_upload)

    async def _upload_one(idx: int, file_path: Path, target_uri: str) -> None:
        async with sem:

            def _read_and_encode() -> bytes:
                content = file_path.read_bytes()
                return detect_and_convert_encoding(content, file_path)

            try:
                encoded = await asyncio.to_thread(_read_and_encode)
                await viking_fs.write_file_bytes(target_uri, encoded)
            except Exception as exc:
                errors[idx] = f"Failed to upload {file_path}: {exc}"

    await asyncio.gather(*[_upload_one(i, fp, uri) for i, (fp, uri) in enumerate(files_to_upload)])

    for err in errors:
        if err:
            warnings.append(err)
            logger.warning(err)

    uploaded_count = sum(1 for e in errors if e is None)
    return uploaded_count, warnings
