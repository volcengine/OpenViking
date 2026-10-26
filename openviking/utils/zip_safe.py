# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Safe ZIP extraction with Zip Slip protection."""

import os
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

_UTF8_FLAG = 0x800
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:$")


def _contains_cjk(text: str) -> bool:
    return any(
        "\u3400" <= ch <= "\u4dbf"
        or "\u4e00" <= ch <= "\u9fff"
        or "\u3000" <= ch <= "\u303f"
        or "\uff00" <= ch <= "\uffef"
        for ch in text
    )


def _contains_common_mojibake(text: str) -> bool:
    return any(
        # CP437 decodes UTF-8 lead byte 0xE6 as U+00B5.  That byte covers
        # a large part of the basic CJK block whose mojibake otherwise has
        # no Greek, mathematical, or box-drawing characters.
        ch == "\u00b5"
        or "\u0370" <= ch <= "\u03ff"
        or "\u2200" <= ch <= "\u22ff"
        or "\u2500" <= ch <= "\u257f"
        for ch in text
    )


def normalize_zip_filenames(zipf: zipfile.ZipFile) -> None:
    """Repair UTF-8 member names when archives forgot to set the UTF-8 flag."""
    for member in zipf.infolist():
        if member.flag_bits & _UTF8_FLAG:
            continue

        try:
            raw_name = member.filename.encode("cp437")
            repaired_name = raw_name.decode("utf-8")
        except UnicodeError:
            continue

        if repaired_name == member.filename:
            continue
        if _contains_cjk(member.filename):
            continue
        if not _contains_cjk(repaired_name):
            continue
        if not _contains_common_mojibake(member.filename):
            continue

        # Keep orig_filename unchanged. ZipFile.open() compares the local-header
        # name decoded as CP437 with this value; changing it would make repaired
        # entries unreadable even though their output path is now correct.
        member.filename = repaired_name


def _safe_zip_member_path(filename: str) -> Path:
    normalized = filename.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute():
        raise ValueError(f"Zip Slip attempt detected: {filename}")

    safe_parts: list[str] = []
    for part in posix_path.parts:
        if part in {"", "."}:
            continue
        if part == ".." or _WINDOWS_DRIVE_RE.match(part):
            raise ValueError(f"Zip Slip attempt detected: {filename}")
        safe_parts.append(part)

    if not safe_parts:
        raise ValueError(f"Zip Slip attempt detected: {filename}")
    return Path(*safe_parts)


def safe_extract_zip(zipf: zipfile.ZipFile, dest_dir: Path) -> None:
    """Extract ZIP archive with Zip Slip protection.

    Validates every member path stays within dest_dir before extraction.
    Rejects absolute paths, Windows drive prefixes, and parent-directory
    traversal (..). Windows-style backslash separators are normalized to
    forward slash semantics before extraction.
    """
    dest_dir = Path(dest_dir).resolve()
    normalize_zip_filenames(zipf)
    for member in zipf.infolist():
        safe_rel_path = _safe_zip_member_path(member.filename)
        member_path = (dest_dir / safe_rel_path).resolve()
        # Ensure the resolved path is inside dest_dir
        if not str(member_path).startswith(str(dest_dir) + os.sep):
            raise ValueError(f"Zip Slip attempt detected: {member.filename}")
        if member.is_dir() or member.filename.endswith(("/", "\\")):
            member_path.mkdir(parents=True, exist_ok=True)
            continue

        member_path.parent.mkdir(parents=True, exist_ok=True)
        with zipf.open(member) as source, member_path.open("wb") as target:
            shutil.copyfileobj(source, target)
