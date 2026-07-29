# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any


class InMemoryAGFS:
    """Tiny synchronous AGFS fake with enough semantics for PathLockEngine."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/"}
        self._lock = threading.RLock()
        for path, content in (files or {}).items():
            self.write(path, content.encode("utf-8") if isinstance(content, str) else content)

    def ls(self, path: str = "/", ctx=None) -> list[dict[str, Any]]:
        del ctx
        path = _norm_dir(path)
        with self._lock:
            if path not in self.dirs:
                raise FileNotFoundError(path)
            prefix = "/" if path == "/" else f"{path}/"
            names: dict[str, bool] = {}
            for directory in self.dirs:
                if directory == path or not directory.startswith(prefix):
                    continue
                rest = directory.removeprefix(prefix)
                if rest and "/" not in rest:
                    names[rest] = True
            for file_path in self.files:
                if not file_path.startswith(prefix):
                    continue
                rest = file_path.removeprefix(prefix)
                if rest and "/" not in rest:
                    names.setdefault(rest, False)
            return [
                {"name": name, "path": f"{prefix.rstrip('/')}/{name}", "isDir": is_dir}
                for name, is_dir in sorted(names.items())
            ]

    def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False, ctx=None):
        del ctx
        path = _norm_path(path)
        with self._lock:
            if path not in self.files:
                raise FileNotFoundError(path)
            data = self.files[path]
            if size != -1:
                data = data[offset : offset + size]
            elif offset:
                data = data[offset:]
            if stream:
                return iter([data])
            return data

    def cat(self, path: str, offset: int = 0, size: int = -1, stream: bool = False, ctx=None):
        return self.read(path, offset=offset, size=size, stream=stream, ctx=ctx)

    def write(self, path: str, data, max_retries: int = 3, ctx=None) -> str:
        del max_retries, ctx
        path = _norm_path(path)
        with self._lock:
            self.ensure_parent_dirs(path)
            if hasattr(data, "read"):
                raw = data.read()
            elif not isinstance(data, (bytes, bytearray)) and hasattr(data, "__iter__"):
                raw = b"".join(data)
            else:
                raw = data
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            self.files[path] = bytes(raw)
            return path

    def mkdir(self, path: str, mode: str = "755", ctx=None) -> dict[str, Any]:
        del mode, ctx
        path = _norm_dir(path)
        with self._lock:
            parts = [part for part in path.strip("/").split("/") if part]
            current = ""
            self.dirs.add("/")
            for part in parts:
                current = f"{current}/{part}" if current else f"/{part}"
                self.dirs.add(current)
            return {"path": path, "isDir": True}

    def ensure_parent_dirs(self, path: str, mode: str = "755", ctx=None) -> dict[str, Any]:
        del mode, ctx
        parent = _parent(_norm_path(path))
        if parent:
            return self.mkdir(parent)
        return {"path": "/", "isDir": True}

    def rm(
        self,
        path: str,
        recursive: bool = False,
        force: bool = True,
        ctx=None,
    ) -> dict[str, Any]:
        del ctx
        path = _norm_path(path)
        with self._lock:
            if path in self.files:
                del self.files[path]
                return {"path": path, "removed": True}
            dir_path = _norm_dir(path)
            if dir_path in self.dirs:
                has_children = any(
                    item.startswith(f"{dir_path.rstrip('/')}/")
                    for item in (*self.dirs, *self.files)
                    if item != dir_path
                )
                if has_children and not recursive:
                    raise IsADirectoryError(dir_path)
                for file_path in list(self.files):
                    if file_path.startswith(f"{dir_path.rstrip('/')}/"):
                        del self.files[file_path]
                for child_dir in sorted(self.dirs, key=len, reverse=True):
                    if child_dir == dir_path or child_dir.startswith(f"{dir_path.rstrip('/')}/"):
                        self.dirs.discard(child_dir)
                self.dirs.add("/")
                return {"path": dir_path, "removed": True}
            if force:
                return {"path": path, "removed": False}
            raise FileNotFoundError(path)

    def stat(self, path: str, ctx=None) -> dict[str, Any]:
        del ctx
        path = _norm_path(path)
        with self._lock:
            if path in self.files:
                return {"path": path, "name": path.rstrip("/").rsplit("/", 1)[-1], "isDir": False}
            dir_path = _norm_dir(path)
            if dir_path in self.dirs:
                return {
                    "path": dir_path,
                    "name": dir_path.rstrip("/").rsplit("/", 1)[-1] if dir_path != "/" else "/",
                    "isDir": True,
                }
            raise FileNotFoundError(path)

    def mv(self, old_path: str, new_path: str, ctx=None) -> dict[str, Any]:
        del ctx
        old_path = _norm_path(old_path)
        new_path = _norm_path(new_path)
        with self._lock:
            if old_path not in self.files:
                raise FileNotFoundError(old_path)
            self.ensure_parent_dirs(new_path)
            self.files[new_path] = self.files.pop(old_path)
            return {"old_path": old_path, "new_path": new_path}

    def grep(self, **kwargs: Any) -> dict[str, Any]:
        return {"matches": [], "kwargs": kwargs}

    def tree_directory(
        self,
        path: str,
        show_hidden: bool = False,
        node_limit: int | None = None,
        level_limit: int | None = None,
        ctx=None,
    ) -> list[dict[str, Any]]:
        del show_hidden, node_limit, level_limit, ctx
        return self.ls(path)


class InMemoryVikingFS:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.writes = []
        self.agfs = InMemoryAGFS()
        for uri in files:
            self.agfs.ensure_parent_dirs(self._uri_to_path(uri))

    def _uri_to_path(self, uri: str, ctx=None) -> str:
        account_id = getattr(ctx, "account_id", None)
        if account_id is None:
            user = getattr(ctx, "user", None)
            account_id = getattr(user, "account_id", None)
        account_id = account_id or "default"
        return f"/local/{account_id}/{uri.removeprefix('viking://').strip('/')}"

    async def ls(self, uri: str, output: str = "original", ctx=None):
        assert output == "original"
        self.agfs.mkdir(self._uri_to_path(uri, ctx=ctx))
        prefix = uri.rstrip("/") + "/"
        return [
            {
                "name": path.removeprefix(prefix),
                "uri": path,
                "isDir": False,
            }
            for path in sorted(self.files)
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        ]

    async def read_file(self, uri: str, ctx=None):
        return self.files[uri]

    async def write_file(self, uri: str, content: str, ctx=None):
        self.files[uri] = content
        self.writes.append((uri, content, ctx))
        self.agfs.write(self._uri_to_path(uri, ctx=ctx), content.encode("utf-8"))


def fake_request_context(account_id: str = "default", user_id: str = "u"):
    return SimpleNamespace(
        user=SimpleNamespace(account_id=account_id, user_id=user_id),
        account_id=account_id,
    )


def _norm_path(path: str) -> str:
    if not path:
        return "/"
    return "/" + path.strip("/") if not path.startswith("/") else path.rstrip("/") or "/"


def _norm_dir(path: str) -> str:
    return _norm_path(path)


def _parent(path: str) -> str | None:
    path = _norm_path(path)
    if path == "/" or "/" not in path.strip("/"):
        return "/" if path != "/" else None
    parent = path.rsplit("/", 1)[0]
    return parent or "/"
