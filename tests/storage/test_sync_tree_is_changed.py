# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for the ``sync_tree`` change predicate.

These use an in-memory filesystem instead of the RAGFS binding so they run
everywhere, including environments without the native extension.
"""

from typing import Any, Dict, Iterable, List, Optional

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs._sync import _SyncMixin
from openviking_cli.session.user_id import UserIdentifier


class _MemoryFS(_SyncMixin):
    """Minimal in-memory stand-in exposing the methods ``sync_tree`` calls."""

    def __init__(self, *, files: Dict[str, bytes], dirs: Iterable[str]) -> None:
        self.files: Dict[str, bytes] = dict(files)
        self.dirs: set[str] = set(dirs)

    @staticmethod
    def _norm(uri: str) -> str:
        return uri.rstrip("/")

    async def exists(self, uri: str, ctx: Optional[RequestContext] = None, **_: Any) -> bool:
        u = self._norm(uri)
        return u in self.files or u in self.dirs

    async def ls(
        self,
        uri: str,
        show_all_hidden: bool = False,
        node_limit: Optional[int] = None,
        ctx: Optional[RequestContext] = None,
        **_: Any,
    ) -> List[Dict[str, Any]]:
        del show_all_hidden, node_limit, ctx
        prefix = self._norm(uri) + "/"
        entries: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for path in sorted(self.files) + sorted(self.dirs):
            if not path.startswith(prefix):
                continue
            name = path[len(prefix) :].split("/", 1)[0]
            if not name or name in seen:
                continue
            seen.add(name)
            entries.append({"name": name, "isDir": prefix + name in self.dirs})
        return entries

    async def stat(
        self,
        uri: str,
        ctx: Optional[RequestContext] = None,
        skip_count: bool = False,
        **_: Any,
    ) -> Dict[str, Any]:
        del ctx, skip_count
        u = self._norm(uri)
        if u in self.files:
            return {"size": len(self.files[u]), "isDir": False}
        if u in self.dirs:
            return {"size": 0, "isDir": True}
        raise FileNotFoundError(u)

    async def read_file(self, uri: str, ctx: Optional[RequestContext] = None, **_: Any) -> bytes:
        del ctx
        u = self._norm(uri)
        if u not in self.files:
            raise FileNotFoundError(u)
        return self.files[u]

    async def mkdir(
        self,
        uri: str,
        exist_ok: bool = False,
        ctx: Optional[RequestContext] = None,
        lease_ref: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> None:
        del exist_ok, ctx, lease_ref
        u = self._norm(uri)
        if u in self.dirs:
            return
        self.dirs.add(u)
        parts = u.split("/")
        for index in range(3, len(parts)):
            self.dirs.add("/".join(parts[:index]))

    async def mv(
        self,
        src: str,
        dst: str,
        ctx: Optional[RequestContext] = None,
        lease_ref: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> None:
        del ctx, lease_ref
        s, d = self._norm(src), self._norm(dst)
        if s in self.files:
            self.files[d] = self.files.pop(s)
            return
        if s not in self.dirs:
            raise FileNotFoundError(s)
        for path in [p for p in self.files if p.startswith(s + "/")]:
            self.files[d + path[len(s) :]] = self.files.pop(path)
        for path in [p for p in self.dirs if p.startswith(s + "/")]:
            self.dirs.discard(path)
            self.dirs.add(d + path[len(s) :])
        self.dirs.discard(s)
        self.dirs.add(d)

    async def rm(
        self,
        uri: str,
        recursive: bool = False,
        ctx: Optional[RequestContext] = None,
        lease_ref: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> None:
        del ctx, lease_ref
        u = self._norm(uri)
        if u in self.files:
            del self.files[u]
            return
        if u not in self.dirs:
            raise FileNotFoundError(u)
        children = [p for p in self.files if p.startswith(u + "/")] + [
            p for p in self.dirs if p.startswith(u + "/")
        ]
        if children and not recursive:
            raise IsADirectoryError(u)
        for path in [p for p in self.files if p.startswith(u + "/")]:
            del self.files[path]
        self.dirs = {p for p in self.dirs if p != u and not p.startswith(u + "/")}

    async def delete_temp(self, uri: str, ctx: Optional[RequestContext] = None, **_: Any) -> None:
        await self.rm(uri, recursive=True, ctx=ctx)


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role(Role.ROOT))


def _changed_pair_fs() -> _MemoryFS:
    return _MemoryFS(
        files={
            "viking://temp/stage/doc.md": b"new content",
            "viking://resources/dest/doc.md": b"old",
        },
        dirs={
            "viking://temp",
            "viking://temp/stage",
            "viking://resources",
            "viking://resources/dest",
        },
    )


async def test_custom_is_changed_predicate_receives_ctx_and_replaces_file() -> None:
    fs = _changed_pair_fs()
    ctx = _ctx()
    calls: List[tuple] = []

    async def predicate(root_file: str, target_file: str, request_ctx) -> bool:
        calls.append((root_file, target_file, request_ctx))
        return True

    diff = await fs.sync_tree(
        "viking://temp/stage",
        "viking://resources/dest",
        ctx=ctx,
        is_changed=predicate,
    )

    assert calls == [("viking://temp/stage/doc.md", "viking://resources/dest/doc.md", ctx)]
    assert diff.updated_files == ["viking://resources/dest/doc.md"]
    assert fs.files["viking://resources/dest/doc.md"] == b"new content"
    assert "viking://temp/stage/doc.md" not in fs.files


async def test_failing_change_predicate_is_treated_as_changed() -> None:
    fs = _changed_pair_fs()

    async def predicate(root_file: str, target_file: str, request_ctx) -> bool:
        raise RuntimeError("comparison backend unavailable")

    diff = await fs.sync_tree(
        "viking://temp/stage",
        "viking://resources/dest",
        ctx=_ctx(),
        is_changed=predicate,
    )

    assert diff.updated_files == ["viking://resources/dest/doc.md"]
    assert fs.files["viking://resources/dest/doc.md"] == b"new content"


async def test_default_change_predicate_skips_identical_and_replaces_different() -> None:
    fs = _MemoryFS(
        files={
            "viking://temp/stage/same.md": b"same",
            "viking://temp/stage/diff.md": b"new",
            "viking://resources/dest/same.md": b"same",
            "viking://resources/dest/diff.md": b"old",
        },
        dirs={
            "viking://temp",
            "viking://temp/stage",
            "viking://resources",
            "viking://resources/dest",
        },
    )

    diff = await fs.sync_tree("viking://temp/stage", "viking://resources/dest", ctx=_ctx())

    assert diff.updated_files == ["viking://resources/dest/diff.md"]
    assert fs.files["viking://resources/dest/same.md"] == b"same"
    assert fs.files["viking://temp/stage/same.md"] == b"same"
    assert fs.files["viking://resources/dest/diff.md"] == b"new"
