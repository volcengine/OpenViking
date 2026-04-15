# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Safe ZIP extraction with Zip Slip protection."""

import os
import zipfile
from pathlib import Path

_UTF8_FLAG = 0x800


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
        "\u0370" <= ch <= "\u03ff" or "\u2200" <= ch <= "\u22ff" or "\u2500" <= ch <= "\u257f"
        for ch in text
    )


def normalize_zip_filenames(zipf: zipfile.ZipFile) -> None:
    """Repair UTF-8 member names when archives forgot to set the UTF-8 flag."""
    repaired_any = False
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

        member.filename = repaired_name
        member.orig_filename = repaired_name
        repaired_any = True

    if repaired_any:
        zipf.metadata_encoding = "utf-8"


def safe_extract_zip(zipf: zipfile.ZipFile, dest_dir: Path) -> None:
    """Extract ZIP archive with Zip Slip protection.

    Validates every member path stays within dest_dir before extraction.
    Rejects absolute paths and parent-directory traversal (..).
    """
    dest_dir = Path(dest_dir).resolve()
    normalize_zip_filenames(zipf)
    for member in zipf.infolist():
        member_path = (dest_dir / member.filename).resolve()
        # Ensure the resolved path is inside dest_dir
        if not str(member_path).startswith(str(dest_dir) + os.sep):
            raise ValueError(f"Zip Slip attempt detected: {member.filename}")
        zipf.extract(member, dest_dir)
