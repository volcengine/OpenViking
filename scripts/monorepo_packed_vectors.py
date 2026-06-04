#!/usr/bin/env python3
"""Demo: Monorepo with vectors packed into git blobs (Scheme A).

Each file's content + embedding vector are packed into a single git blob:
    [uint32 content_len][content bytes][uint32 vector_count][vector float32...]

Working directory contains only the original files. Vectors are read from
an external source at commit time, and restored at rollback time.
"""

import os
import pickle
import stat
import struct
import sys
import tempfile
from pathlib import Path

_DULWICH_REPO_ROOT = Path(__file__).resolve().parents[2] / "dulwich"
if _DULWICH_REPO_ROOT.exists():
    sys.path.insert(0, str(_DULWICH_REPO_ROOT))

from dulwich import porcelain
from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo


# ---------------------------------------------------------------------------
# Packing format
# ---------------------------------------------------------------------------

PACK_MAGIC = b"VEC1"  # 4-byte magic for versioning


def pack_blob(content: bytes, vector: list[float] | None) -> bytes:
    """Pack content + vector into a single blob.

    Format: [4B magic][4B content_len][content][4B vec_count][vec float32...]
    If vector is None, vec_count = 0 and no vector data follows.
    """
    vec_count = len(vector) if vector else 0
    header = PACK_MAGIC + struct.pack("<II", len(content), vec_count)
    if vector:
        vec_data = struct.pack(f"<{vec_count}f", *vector)
        return header + content + vec_data
    return header + content


def unpack_blob(data: bytes) -> tuple[bytes, list[float] | None]:
    """Unpack a blob into (content, vector)."""
    if not data.startswith(PACK_MAGIC):
        # Not a packed blob — treat as plain content
        return data, None

    offset = 4
    content_len, vec_count = struct.unpack_from("<II", data, offset)
    offset += 8
    content = data[offset : offset + content_len]
    offset += content_len

    if vec_count == 0:
        return content, None

    vector = list(struct.unpack_from(f"<{vec_count}f", data, offset))
    return content, vector


# ---------------------------------------------------------------------------
# Vector source (mock for demo — in real use, read from your external system)
# ---------------------------------------------------------------------------


class VectorSource:
    """Simulates an external system that provides vectors for files."""

    def __init__(self):
        self._vectors: dict[str, list[float]] = {}

    def set_vector(self, file_path: str | Path, vector: list[float]) -> None:
        self._vectors[str(file_path)] = vector

    def get_vector(self, file_path: str | Path) -> list[float] | None:
        return self._vectors.get(str(file_path))

    def restore_vector(self, file_path: str | Path, vector: list[float]) -> None:
        """Called during rollback to restore the vector to the external system."""
        self._vectors[str(file_path)] = vector
        print(f"  ↳ restored vector for {Path(file_path).name}: {vector}")


# ---------------------------------------------------------------------------
# Custom commit that packs content + vector
# ---------------------------------------------------------------------------


def _write_packed_blob(
    repo: Repo, file_path: Path, content: bytes, vector: list[float] | None
) -> bytes:
    """Write a packed blob to the git object store and return its SHA."""
    packed = pack_blob(content, vector)
    blob = Blob.from_string(packed)
    repo.object_store.add_object(blob)
    return blob.id


def _build_tree_from_dir(
    repo: Repo,
    root: Path,
    dir_path: Path,
    vector_source: VectorSource,
) -> bytes:
    """Recursively build a Tree for dir_path, packing files with their vectors."""
    tree = Tree()
    for entry in sorted(dir_path.iterdir()):
        if entry.name == ".git":
            continue
        rel_path = entry.relative_to(root)
        if entry.is_file():
            content = entry.read_bytes()
            vector = vector_source.get_vector(rel_path)
            mode = 0o100755 if os.access(entry, os.X_OK) else 0o100644
            sha = _write_packed_blob(repo, entry, content, vector)
            tree.add(entry.name.encode("utf-8"), mode, sha)
        elif entry.is_dir():
            subtree_sha = _build_tree_from_dir(repo, root, entry, vector_source)
            tree.add(entry.name.encode("utf-8"), stat.S_IFDIR, subtree_sha)
    repo.object_store.add_object(tree)
    return tree.id


def commit_project(
    repo: Repo,
    project_dir: str,
    message: str,
    vector_source: VectorSource,
) -> str:
    """Commit a project directory, packing each file with its vector.

    Preserves the project directory as a subdirectory in the repo root tree.
    """
    root = Path(repo.path)
    proj_path = root / project_dir

    # Build tree for the project directory
    proj_tree_sha = _build_tree_from_dir(repo, root, proj_path, vector_source)

    # Get current root tree (if any commits exist)
    try:
        parent = repo[repo.head()]
        root_tree = repo[parent.tree]
    except KeyError:
        parent = None
        root_tree = Tree()

    # Create a new root tree, adding/updating the project directory entry
    new_root_tree = Tree()
    # Copy existing entries except the project we're updating
    for name, mode, sha in root_tree.iteritems():
        if name.decode("utf-8") != project_dir:
            new_root_tree.add(name, mode, sha)
    # Add the project tree
    new_root_tree.add(project_dir.encode("utf-8"), stat.S_IFDIR, proj_tree_sha)

    repo.object_store.add_object(new_root_tree)

    # Create commit
    commit = Commit()
    commit.tree = new_root_tree.id
    commit.author = b"User <user@example.com>"
    commit.committer = b"User <user@example.com>"
    import time

    now = int(time.time())
    commit.author_time = now
    commit.commit_time = now
    commit.author_timezone = 0
    commit.commit_timezone = 0
    commit.parents = [parent.id] if parent else []
    commit.message = message.encode("utf-8")

    repo.object_store.add_object(commit)
    repo.refs[b"HEAD"] = commit.id

    commit_hex = commit.id.decode("ascii")
    print(f"✓ [{project_dir}] {message} -> {commit_hex[:8]}")
    return commit_hex


# ---------------------------------------------------------------------------
# Tree walking
# ---------------------------------------------------------------------------


def _walk_tree(repo: Repo, tree: Tree, prefix: str = "") -> dict[str, tuple[int, bytes]]:
    """Recursively walk a tree, returning {path: (mode, sha)} for all blobs."""
    entries = {}
    for name, mode, sha in tree.iteritems():
        name_str = name.decode("utf-8")
        full_path = f"{prefix}/{name_str}" if prefix else name_str
        if stat.S_ISDIR(mode):
            subtree = repo[sha]
            entries.update(_walk_tree(repo, subtree, full_path))
        else:
            entries[full_path] = (mode, sha)
    return entries


# ---------------------------------------------------------------------------
# Rollback: restore content + vector from packed blobs
# ---------------------------------------------------------------------------


def rollback_project(
    repo: Repo,
    project_dir: str,
    to_commit: str,
    vector_source: VectorSource,
) -> None:
    """Roll back a project to a previous commit, restoring both content and vectors."""
    root = Path(repo.path)
    project_prefix = project_dir.rstrip("/")
    target_commit = repo[to_commit.encode("ascii")]
    target_tree = repo[target_commit.tree]

    # Collect files in target commit
    target_files = _walk_tree(repo, target_tree)
    target_project_files = {
        path: (mode, sha)
        for path, (mode, sha) in target_files.items()
        if path == project_prefix or path.startswith(f"{project_prefix}/")
    }

    # Collect currently tracked files (from HEAD tree)
    head_commit = repo[repo.head()]
    head_tree = repo[head_commit.tree]
    current_files = _walk_tree(repo, head_tree)
    current_project_files = {
        path: (mode, sha)
        for path, (mode, sha) in current_files.items()
        if path == project_prefix or path.startswith(f"{project_prefix}/")
    }

    # Delete files that no longer exist in target
    for path in current_project_files:
        if path not in target_project_files:
            file_path = root / path
            if file_path.is_file():
                file_path.unlink()

    # Restore files from target (unpack content + vector)
    for path, (mode, sha) in target_project_files.items():
        file_path = root / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        blob = repo[sha]
        content, vector = unpack_blob(blob.data)
        file_path.write_bytes(content)
        os.chmod(file_path, mode & 0o777)
        if vector is not None:
            vector_source.restore_vector(path, vector)

    # Stage and commit the rollback using standard add/commit
    porcelain.add(repo, project_dir)
    commit_id = porcelain.commit(
        repo,
        message=f"Rollback {project_dir} to {to_commit[:8]}".encode("utf-8"),
        author=b"User <user@example.com>",
        committer=b"User <user@example.com>",
    )
    print(f"↩ [{project_dir}] Rolled back to {to_commit[:8]} -> {commit_id.decode()[:8]}")


# ---------------------------------------------------------------------------
# Reading historical versions
# ---------------------------------------------------------------------------


def get_file_at_commit(
    repo: Repo, file_path: str, commit_sha: str
) -> tuple[bytes, list[float] | None]:
    """Get (content, vector) for a file at a specific commit."""
    commit = repo[commit_sha.encode("ascii")]
    tree = repo[commit.tree]

    parts = file_path.split("/")
    current_tree = tree
    for part in parts[:-1]:
        mode, sha = current_tree[part.encode("utf-8")]
        current_tree = repo[sha]

    filename = parts[-1].encode("utf-8")
    mode, blob_sha = current_tree[filename]
    blob = repo[blob_sha]
    return unpack_blob(blob.data)


# ---------------------------------------------------------------------------
# Other helpers
# ---------------------------------------------------------------------------


def init_repo(root: str) -> Repo:
    """Initialize a monorepo at root."""
    root_path = Path(root).resolve()
    if (root_path / ".git").exists():
        raise ValueError(f".git already exists in {root_path}")
    repo = Repo.init(str(root_path), mkdir=False)
    print(f"✓ Initialized repo at {root_path}")
    return repo


def log_project(repo: Repo, project_dir: str) -> None:
    """Show commit history for a single project."""
    project_bytes = project_dir.encode("utf-8")
    walker = repo.get_walker(paths=[project_bytes])

    print(f"\n=== History: {project_dir} ===")
    count = 0
    for entry in walker:
        commit = entry.commit
        short_sha = commit.id.decode("ascii")[:8]
        message = commit.message.decode("utf-8", errors="replace").split("\n")[0]
        print(f"{short_sha} {message}")
        count += 1
    if count == 0:
        print("(no commits)")


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "monorepo"
        root.mkdir()

        # Create project directories
        for name in ["projectA", "projectB"]:
            (root / name).mkdir()

        # External vector source
        vector_source = VectorSource()

        # projectA: initial
        file_a = root / "projectA" / "file.txt"
        file_a.write_text("projectA v1\n")
        vector_source.set_vector("projectA/file.txt", [0.1, 0.2, 0.3, 0.4])

        # projectB: initial
        file_b = root / "projectB" / "data.txt"
        file_b.write_text("projectB v1\n")
        vector_source.set_vector("projectB/data.txt", [1.0, 2.0, 3.0])

        print(f"=== Test monorepo at {root} ===\n")
        repo = init_repo(str(root))

        # Commit each project (blobs contain content + vector)
        commit_a1 = commit_project(repo, "projectA", "projectA: initial", vector_source)
        commit_b1 = commit_project(repo, "projectB", "projectB: initial", vector_source)

        # Update projectA
        file_a.write_text("projectA v2\n")
        vector_source.set_vector("projectA/file.txt", [0.5, 0.6, 0.7, 0.8])
        commit_a2 = commit_project(repo, "projectA", "projectA: update", vector_source)

        # Show history
        log_project(repo, "projectA")
        log_project(repo, "projectB")

        # Read historical versions
        print(f"\n=== Historical content + vector ===")
        content_a1, vec_a1 = get_file_at_commit(repo, "projectA/file.txt", commit_a1)
        print(
            f"@ commit_a1 ({commit_a1[:8]}): content={repr(content_a1.decode().strip())}, vector={vec_a1}"
        )
        content_a2, vec_a2 = get_file_at_commit(repo, "projectA/file.txt", commit_a2)
        print(
            f"@ commit_a2 ({commit_a2[:8]}): content={repr(content_a2.decode().strip())}, vector={vec_a2}"
        )

        # Roll back projectA (restores content AND vector to external source)
        print(f"\n=== Rolling back projectA to {commit_a1[:8]} ===")
        rollback_project(repo, "projectA", commit_a1, vector_source)

        # Verify
        print(f"\n=== Verification ===")
        print(f"projectA/file.txt: {repr(file_a.read_text().strip())}")
        print(f"projectA vector in source: {vector_source.get_vector('projectA/file.txt')}")
        print(f"projectB unchanged: {file_b.read_text().strip() == 'projectB v1'}")
        print(
            f"projectB vector unchanged: {vector_source.get_vector('projectB/data.txt') == [1.0, 2.0, 3.0]}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
