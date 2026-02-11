# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for parse tests."""

import os

import pytest

import openviking.storage.viking_fs as vfs_mod


class FakeAGFS:
    """Minimal AGFS mock that uses local filesystem for temp dirs."""

    def __init__(self, base_dir: str):
        self._base = base_dir

    def _resolve(self, path: str) -> str:
        # path is typically like /temp/abcd1234
        return os.path.join(self._base, path.lstrip("/"))

    def mkdir(self, path: str, **kwargs):
        real = self._resolve(path)
        os.makedirs(real, exist_ok=True)

    def write(self, path: str, data: bytes, **kwargs):
        real = self._resolve(path)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))

    def read(self, path: str, offset: int = 0, size: int = -1):
        real = self._resolve(path)
        with open(real, "rb") as f:
            if offset:
                f.seek(offset)
            return f.read(size if size > 0 else None)

    def stat(self, path: str):
        real = self._resolve(path)
        if not os.path.exists(real):
            raise FileNotFoundError(path)
        st = os.stat(real)
        return {"size": st.st_size, "is_dir": os.path.isdir(real)}

    def ls(self, path: str):
        real = self._resolve(path)
        if not os.path.isdir(real):
            return []
        entries = []
        for name in os.listdir(real):
            full = os.path.join(real, name)
            entries.append({"name": name, "is_dir": os.path.isdir(full)})
        return entries

    def rm(self, path: str, recursive: bool = False):
        import shutil

        real = self._resolve(path)
        if os.path.isdir(real) and recursive:
            shutil.rmtree(real)
        elif os.path.exists(real):
            os.remove(real)

    def mv(self, old_path: str, new_path: str):
        real_old = self._resolve(old_path)
        real_new = self._resolve(new_path)
        os.makedirs(os.path.dirname(real_new), exist_ok=True)
        os.rename(real_old, real_new)

    def grep(self, path: str, pattern: str, recursive: bool = True, case_insensitive: bool = False):
        return []


@pytest.fixture(autouse=True)
def init_viking_fs_for_tests(tmp_path):
    """Initialize a VikingFS with fake AGFS backend for parser unit tests."""
    from openviking.storage.viking_fs import VikingFS

    fs = VikingFS.__new__(VikingFS)
    fs.agfs = FakeAGFS(str(tmp_path))
    fs._query_embedder = None
    fs._rerank_config = None
    fs._vector_store = None

    # Patch the singleton
    old = vfs_mod._instance
    vfs_mod._instance = fs

    yield fs

    vfs_mod._instance = old
