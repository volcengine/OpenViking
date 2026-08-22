"""Regression: sync_tree must be additive by default (issue #4217).

add-resource into an EXISTING directory used to mirror the temp source tree
over the target, deleting every target-only sibling (the temp tree holds only
the newly-added resource, so N adds to one directory left 1 survivor).

The fix gates the two destructive branches behind ``delete_missing`` (default
``False`` = additive merge): target-only files and directories are retained
unless a caller explicitly asks for a full mirror (``delete_missing=True``).
"""

from __future__ import annotations

from typing import Any, Dict

from openviking.storage.viking_fs._sync import _SyncMixin


class _MemNode:
    def __init__(self, kind: str, content: str = "") -> None:
        self.kind = kind  # "file" | "dir"
        self.content = content
        self.children: Dict[str, str] = {}  # name -> uri


class _MemoryVikingFS(_SyncMixin):
    """In-memory VikingFS fake backing the real ``_SyncMixin.sync_tree`` logic."""

    def __init__(self) -> None:
        self.tree: Dict[str, _MemNode] = {}
        self._ensure_dir("viking:")

    # -- seeding helpers ---------------------------------------------------

    def _ensure_dir(self, uri: str) -> None:
        if uri in self.tree:
            return
        parent_uri, name = (uri.rsplit("/", 1) + [""])[:2] if "/" in uri else ("", uri)
        if parent_uri and name:
            self._ensure_dir(parent_uri)
        node = _MemNode("dir")
        self.tree[uri] = node
        if parent_uri in self.tree and name:
            self.tree[parent_uri].children[name] = uri

    def add_file(self, uri: str, content: str = "") -> None:
        parent_uri, name = uri.rsplit("/", 1)
        self._ensure_dir(parent_uri)
        self.tree[uri] = _MemNode("file", content)
        self.tree[parent_uri].children[name] = uri

    # -- VikingFS interface used by sync_tree -----------------------------

    async def exists(self, uri: str, ctx=None) -> bool:
        return uri in self.tree

    async def ls(self, uri: str, show_all_hidden=False, node_limit=None, ctx=None):
        node = self.tree.get(uri)
        if node is None or node.kind != "dir":
            return []
        entries = []
        for name, child_uri in node.children.items():
            child = self.tree.get(child_uri)
            if child is None:
                continue
            entries.append({"name": name, "isDir": child.kind == "dir"})
        return entries

    async def stat(self, uri: str, ctx=None) -> Dict[str, Any]:
        return {"size": len(self.tree.get(uri, _MemNode("file")).content)}

    async def read_file(self, uri: str, ctx=None) -> str:
        node = self.tree.get(uri)
        return node.content if node else ""

    async def mv(self, src: str, dst: str, ctx=None, lease_ref=None) -> None:
        node = self.tree.pop(src, None)
        if node is None:
            return
        old_parent, old_name = src.rsplit("/", 1)
        if old_parent in self.tree and old_name in self.tree[old_parent].children:
            del self.tree[old_parent].children[old_name]
        self.tree[dst] = node
        new_parent, new_name = dst.rsplit("/", 1)
        if new_parent in self.tree and new_name:
            self.tree[new_parent].children[new_name] = dst

    async def rm(self, uri: str, recursive=False, ctx=None, lease_ref=None) -> None:
        node = self.tree.pop(uri, None)
        if node is None:
            return
        for child_uri in list(node.children.values()):
            await self.rm(child_uri, recursive=True)
        parent, name = uri.rsplit("/", 1)
        if parent in self.tree and name in self.tree[parent].children:
            del self.tree[parent].children[name]

    async def mkdir(self, uri: str, exist_ok=False, ctx=None, lease_ref=None) -> None:
        self._ensure_dir(uri)

    async def delete_temp(self, uri: str, ctx=None, lease_ref=None) -> None:
        await self.rm(uri, recursive=True)


def _seed() -> _MemoryVikingFS:
    """Target holds a pre-existing file + subdir; source holds only one new file."""
    fs = _MemoryVikingFS()
    fs._ensure_dir("viking://resources/test")
    fs.add_file("viking://resources/test/a.md", "existing file")
    fs._ensure_dir("viking://resources/test/sub")
    fs.add_file("viking://resources/test/sub/x.md", "nested file")
    fs._ensure_dir("viking://temp/new")
    fs.add_file("viking://temp/new/b.md", "new file")
    return fs


async def test_sync_tree_additive_keeps_target_only_entries():
    """Default (delete_missing=False): sync adds the new file, deletes nothing."""
    fs = _seed()

    diff = await fs.sync_tree("viking://temp/new", "viking://resources/test")

    # Prior target entries survive.
    assert await fs.exists("viking://resources/test/a.md")
    assert await fs.exists("viking://resources/test/sub/x.md")
    # The new file lands.
    assert await fs.exists("viking://resources/test/b.md")
    assert await fs.read_file("viking://resources/test/b.md") == "new file"
    # Diff reports the add only — nothing deleted.
    assert diff.added_files == ["viking://resources/test/b.md"]
    assert diff.deleted_files == []
    assert diff.deleted_dirs == []


async def test_sync_tree_delete_missing_mirrors_source():
    """delete_missing=True restores the old mirror behaviour explicitly."""
    fs = _seed()

    diff = await fs.sync_tree(
        "viking://temp/new",
        "viking://resources/test",
        delete_missing=True,
    )

    # Target-only entries removed, new file present.
    assert not await fs.exists("viking://resources/test/a.md")
    assert not await fs.exists("viking://resources/test/sub")
    assert await fs.exists("viking://resources/test/b.md")
    assert "viking://resources/test/a.md" in diff.deleted_files
    assert "viking://resources/test/sub" in diff.deleted_dirs
