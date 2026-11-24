# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import os
import tempfile
import uuid
import zipfile
from pathlib import Path

from ._utils import _path_is_relative_to


def zip_directory(dir_path: str) -> str:
    path = Path(dir_path)
    if not path.is_dir():
        raise ValueError(f"Path {path} is not a directory")

    root = path.resolve()
    zip_path = Path(tempfile.gettempdir()) / f"temp_upload_{uuid.uuid4().hex}.zip"

    def raise_scan_error(error: OSError) -> None:
        raise error

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for directory, _, filenames in os.walk(path, onerror=raise_scan_error):
                for filename in filenames:
                    file_path = Path(directory) / filename
                    if file_path.is_symlink():
                        continue
                    if file_path.is_file():
                        if not _path_is_relative_to(file_path.resolve(), root):
                            continue
                        arcname = str(file_path.relative_to(path)).replace("\\", "/")
                        zipf.write(file_path, arcname=arcname)
    except BaseException:
        zip_path.unlink(missing_ok=True)
        raise

    return str(zip_path)
