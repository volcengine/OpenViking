from __future__ import annotations

import tempfile
import uuid
import zipfile
from pathlib import Path


def zip_directory(dir_path: str) -> str:
    path = Path(dir_path)
    if not path.is_dir():
        raise ValueError(f"Path {path} is not a directory")

    root = path.resolve()
    zip_path = Path(tempfile.gettempdir()) / f"temp_upload_{uuid.uuid4().hex}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in path.rglob("*"):
            if file_path.is_symlink():
                continue
            if file_path.is_file():
                if not file_path.resolve().is_relative_to(root):
                    continue
                arcname = str(file_path.relative_to(path)).replace("\\", "/")
                zipf.write(file_path, arcname=arcname)

    return str(zip_path)
