#!/usr/bin/env python3
"""Demo: Custom object store for dulwich, using a dict-based FS API.

Shows how to plug in your own storage backend instead of the default
.git/objects/ disk layout.
"""

import sys
import zlib
from pathlib import Path

_DULWICH_REPO_ROOT = Path(__file__).resolve().parents[2] / "dulwich"
if _DULWICH_REPO_ROOT.exists():
    sys.path.insert(0, str(_DULWICH_REPO_ROOT))

from dulwich.objects import Blob, Tree, Commit
from dulwich.object_store import BaseObjectStore, MemoryObjectStore
from dulwich.refs import RefsContainer


# ---------------------------------------------------------------------------
# Mock FS API (replace this with your actual FS API)
# ---------------------------------------------------------------------------


class DictFSApi:
    """A simple in-memory FS API for demo. Replace with your real implementation."""

    def __init__(self, base_path: str = ""):
        self.base_path = base_path.rstrip("/")
        self._storage: dict[str, bytes] = {}

    def _full_path(self, path: str) -> str:
        return f"{self.base_path}/{path.lstrip('/')}" if self.base_path else path

    def write(self, path: str, data: bytes) -> None:
        full = self._full_path(path)
        print(f"  [FS] WRITE {full} ({len(data)} bytes)")
        self._storage[full] = data

    def read(self, path: str) -> bytes:
        full = self._full_path(path)
        if full not in self._storage:
            raise KeyError(f"Not found: {full}")
        data = self._storage[full]
        print(f"  [FS] READ  {full} ({len(data)} bytes)")
        return data

    def exists(self, path: str) -> bool:
        return self._full_path(path) in self._storage

    def list(self, prefix: str) -> list[str]:
        prefix = self._full_path(prefix)
        return [k[len(prefix) :].lstrip("/") for k in self._storage if k.startswith(prefix)]

    def delete(self, path: str) -> None:
        full = self._full_path(path)
        if full in self._storage:
            del self._storage[full]


# ---------------------------------------------------------------------------
# Custom Object Store
# ---------------------------------------------------------------------------


class CustomObjectStore(BaseObjectStore):
    """Object store backed by a custom FS API.

    Objects are stored as: objects/<hex-sha>
    Content is zlib-compressed (same as git loose objects).
    """

    def __init__(self, fs_api: DictFSApi):
        super().__init__()
        self.fs_api = fs_api

    def _sha_to_path(self, sha1: bytes | str) -> str:
        if isinstance(sha1, bytes):
            sha1 = sha1.hex()
        return f"objects/{sha1}"

    def __contains__(self, sha1) -> bool:
        return self.fs_api.exists(self._sha_to_path(sha1))

    def add_object(self, obj) -> None:
        """Store a dulwich object (Blob/Tree/Commit/Tag)."""
        sha_hex = obj.id.hex()
        # as_legacy_object() returns zlib-compressed raw object data
        compressed_data = obj.as_legacy_object()
        self.fs_api.write(self._sha_to_path(sha_hex), compressed_data)

    def get_raw(self, name) -> tuple[int, bytes]:
        """Read an object. Returns (type_num, content_bytes)."""
        compressed = self.fs_api.read(self._sha_to_path(name))
        # as_legacy_object already returns zlib-compressed data
        raw_data = zlib.decompress(compressed)
        # Parse git object format: "type size\0content"
        nul_idx = raw_data.index(b"\x00")
        header = raw_data[:nul_idx]
        content = raw_data[nul_idx + 1 :]
        type_name, _ = header.split(b" ", 1)
        type_num = {
            b"blob": 3,
            b"tree": 2,
            b"commit": 1,
            b"tag": 4,
        }[type_name]
        return type_num, content

    def contains_loose(self, sha) -> bool:
        return self.fs_api.exists(self._sha_to_path(sha))

    # Optional: implement these for better performance
    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Custom Refs Container
# ---------------------------------------------------------------------------


class CustomRefs(RefsContainer):
    """Refs (branches, tags, HEAD) stored in the custom FS API."""

    def __init__(self, fs_api: DictFSApi):
        super().__init__()
        self.fs_api = fs_api

    def _ref_path(self, ref: bytes | str) -> str:
        if isinstance(ref, bytes):
            ref = ref.decode("ascii")
        return ref

    def __getitem__(self, ref):
        data = self.fs_api.read(self._ref_path(ref))
        return data.strip()

    def __setitem__(self, ref, sha):
        if isinstance(ref, str):
            ref = ref.encode("ascii")
        if isinstance(sha, str):
            sha = sha.encode("ascii")
        self.fs_api.write(self._ref_path(ref), sha + b"\n")

    def __contains__(self, ref):
        return self.fs_api.exists(self._ref_path(ref))

    def keys(self):
        return [k.encode("ascii") for k in self.fs_api.list("refs/")]

    def get_peeled(self, ref):
        return self[ref]


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== Custom Object Store Demo ===\n")

    # Initialize FS API (replace with your actual implementation)
    fs_api = DictFSApi(base_path="ov-git/projectA")

    # Create custom object store and refs
    object_store = CustomObjectStore(fs_api)
    refs = CustomRefs(fs_api)

    # Set HEAD
    refs[b"HEAD"] = b"ref: refs/heads/main"

    # Create a blob
    print("\n1. Creating blob...")
    blob = Blob.from_string(b"Hello, custom storage!")
    object_store.add_object(blob)

    # Create a tree
    print("\n2. Creating tree...")
    tree = Tree()
    tree.add(b"hello.txt", 0o100644, blob.id)
    object_store.add_object(tree)

    # Create a commit
    print("\n3. Creating commit...")
    commit = Commit()
    commit.tree = tree.id
    commit.author = b"Test <test@example.com>"
    commit.committer = b"Test <test@example.com>"
    commit.author_time = 1717209600
    commit.commit_time = 1717209600
    commit.author_timezone = 0
    commit.commit_timezone = 0
    commit.parents = []
    commit.message = b"Initial commit with custom storage"
    object_store.add_object(commit)

    # Update branch ref
    refs[b"refs/heads/main"] = commit.id

    # Verify we can read everything back
    print("\n4. Reading back objects...")
    read_commit = object_store[commit.id]
    print(f"   Commit message: {read_commit.message.decode()}")

    read_tree = object_store[read_commit.tree]
    tree_items = [
        (name.decode("utf-8"), mode, sha.hex()[:8]) for name, mode, sha in read_tree.iteritems()
    ]
    print(f"   Tree entries: {tree_items}")

    mode, sha = read_tree[b"hello.txt"]
    read_blob = object_store[sha]
    print(f"   Blob content: {read_blob.data.decode()}")

    # List stored objects
    print("\n5. All objects in storage:")
    for key in sorted(fs_api._storage.keys()):
        print(f"   {key}")

    print("\n✓ Demo completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
