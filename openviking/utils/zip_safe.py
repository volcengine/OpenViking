# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Safe ZIP extraction with Zip Slip protection."""

import os
import zipfile
from pathlib import Path


def safe_extract_zip(zipf: zipfile.ZipFile, dest_dir: Path) -> None:
    """Extract ZIP archive with Zip Slip protection.

    Validates every member path stays within dest_dir before extraction.
    Rejects absolute paths and parent-directory traversal (..).
    """
    dest_dir = Path(dest_dir).resolve()
    for member in zipf.infolist():
        member_path = (dest_dir / member.filename).resolve()
        # Ensure the resolved path is inside dest_dir
        if not str(member_path).startswith(str(dest_dir) + os.sep):
            raise ValueError(f"Zip Slip attempt detected: {member.filename}")
        zipf.extract(member, dest_dir)
