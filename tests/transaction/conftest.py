# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Fixtures for transaction lock tests."""

import uuid

import pytest


class MemoryAgfs:
    def __init__(self):
        self.dirs = {"/"}
        self.files: dict[str, bytes] = {}

    def _parent(self, path: str) -> str:
        path = path.rstrip("/") or "/"
        parent = path.rsplit("/", 1)[0]
        return parent or "/"

    def stat(self, path: str):
        path = path.rstrip("/") or "/"
        if path in self.dirs:
            return {"name": path.rsplit("/", 1)[-1], "isDir": True}
        if path in self.files:
            return {"name": path.rsplit("/", 1)[-1], "isDir": False}
        raise FileNotFoundError(path)

    def mkdir(self, path: str):
        path = path.rstrip("/") or "/"
        parent = self._parent(path)
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.dirs.add(path)
        return {"message": "created"}

    def read(self, path: str):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def cat(self, path: str):
        return self.read(path)

    def write(self, path: str, data: bytes):
        parent = self._parent(path)
        if parent not in self.dirs:
            raise FileNotFoundError(parent)
        self.files[path] = data
        return path

    def rm(self, path: str, recursive: bool = False):
        path = path.rstrip("/") or "/"
        self.files.pop(path, None)
        if path in self.dirs:
            children = [
                item
                for item in [*self.dirs, *self.files]
                if item != path and item.startswith(path.rstrip("/") + "/")
            ]
            if children and not recursive:
                raise RuntimeError("directory not empty")
            for child in children:
                self.files.pop(child, None)
                self.dirs.discard(child)
            self.dirs.discard(path)
        return {"message": "deleted"}

    def ls(self, path: str):
        path = path.rstrip("/") or "/"
        prefix = path.rstrip("/") + "/"
        names: dict[str, bool] = {}
        for item in self.dirs:
            if item == path or not item.startswith(prefix):
                continue
            rest = item[len(prefix) :]
            if "/" not in rest:
                names[rest] = True
        for item in self.files:
            if not item.startswith(prefix):
                continue
            rest = item[len(prefix) :]
            if "/" not in rest:
                names[rest] = False
        return [{"name": name, "isDir": is_dir} for name, is_dir in names.items()]


@pytest.fixture
def agfs_client():
    agfs = MemoryAgfs()
    agfs.mkdir("/local")
    agfs.mkdir("/local/default")
    return agfs


@pytest.fixture
def test_dir(agfs_client):
    path = f"/local/default/transaction-{uuid.uuid4().hex}"
    agfs_client.mkdir(path)
    return path
