#!/usr/bin/env python3
"""Demo: Monorepo per-directory commits with vector storage using pure dulwich.

Each file is stored with its embedding vector as a companion file.
Both content and vector are committed together and rolled back together.

Layout:
    projectA/
    ├── file.txt              # original content
    └── .vectors/
        └── file.txt.vec      # embedding vector (binary float32)
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
from dulwich.objects import Commit, Tree
from dulwich.repo import Repo

VECTOR_DIR = ".vectors"
VECTOR_SUFFIX = ".vec"


# ---------------------------------------------------------------------------
# Vector storage helpers
# ---------------------------------------------------------------------------


def _vector_path(file_path: Path) -> Path:
    """Return the path where the vector for file_path is stored."""
    return file_path.parent / VECTOR_DIR / f"{file_path.name}{VECTOR_SUFFIX}"


def _content_path_from_vector(vector_path: Path) -> Path:
    """Inverse of _vector_path: given a .vec file, return the original file."""
    # .vectors/file.txt.vec -> file.txt
    return vector_path.parent.parent / vector_path.stem


def store_vector(file_path: Path, vector: list[float]) -> None:
    """Store an embedding vector for a file.

    Vector is stored as binary float32 for compactness.
    """
    vec_path = _vector_path(file_path)
    vec_path.parent.mkdir(parents=True, exist_ok=True)
    data = struct.pack(f"<{len(vector)}f", *vector)
    vec_path.write_bytes(data)


def load_vector(file_path: Path) -> list[float] | None:
    """Load the embedding vector for a file, or None if not found."""
    vec_path = _vector_path(file_path)
    if not vec_path.exists():
        return None
    data = vec_path.read_bytes()
    count = len(data) // 4
    return list(struct.unpack(f"<{count}f", data))


def vector_exists(file_path: Path) -> bool:
    """Check if a vector file exists for the given file."""
    return _vector_path(file_path).exists()


# ---------------------------------------------------------------------------
# Repo operations
# ---------------------------------------------------------------------------


def init_repo(root: str) -> Repo:
    """Initialize a monorepo at root."""
    root_path = Path(root).resolve()
    if (root_path / ".git").exists():
        raise ValueError(f".git already exists in {root_path}")
    repo = Repo.init(str(root_path), mkdir=False)
    print(f"✓ Initialized repo at {root_path}")
    return repo


def commit_project(repo: Repo, project_dir: str, message: str) -> str:
    """Commit only changes under project_dir (includes .vectors directory)."""
    porcelain.add(repo, project_dir)
    commit_id = porcelain.commit(
        repo,
        message=message.encode("utf-8"),
        author=b"User <user@example.com>",
        committer=b"User <user@example.com>",
    )
    commit_hex = commit_id.decode("ascii")
    print(f"✓ [{project_dir}] {message} -> {commit_hex[:8]}")
    return commit_hex


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


def rollback_project(repo: Repo, project_dir: str, to_commit: str) -> None:
    """Roll back a project (content + vectors) to a previous commit."""
    root = Path(repo.path)
    project_prefix = project_dir.rstrip("/")
    target_commit = repo[to_commit.encode("ascii")]
    target_tree = repo[target_commit.tree]

    # Collect all files (content + vectors) in the target commit
    target_files = _walk_tree(repo, target_tree)
    target_project_files = {
        path: (mode, sha)
        for path, (mode, sha) in target_files.items()
        if path == project_prefix or path.startswith(f"{project_prefix}/")
    }

    # Collect currently tracked files
    head = repo[repo.head()]
    head_tree = repo[head.tree]
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
            if file_path.exists():
                file_path.unlink()

    # Restore files from target (both content and .vec files)
    for path, (mode, sha) in target_project_files.items():
        file_path = root / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        blob = repo[sha]
        file_path.write_bytes(blob.data)
        os.chmod(file_path, mode & 0o777)

    # Clean up empty .vectors directories
    for dirpath, dirnames, filenames in os.walk(root / project_prefix, topdown=False):
        if os.path.basename(dirpath) == VECTOR_DIR and not filenames:
            os.rmdir(dirpath)

    # Stage and commit the rollback
    porcelain.add(repo, project_dir)
    commit_id = porcelain.commit(
        repo,
        message=f"Rollback {project_dir} to {to_commit[:8]}".encode("utf-8"),
        author=b"User <user@example.com>",
        committer=b"User <user@example.com>",
    )
    print(f"↩ [{project_dir}] Rolled back to {to_commit[:8]} -> {commit_id.decode()[:8]}")


def get_file_at_commit(repo: Repo, file_path: str, commit_sha: str) -> bytes:
    """Get the raw content of a file at a specific commit."""
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
    return blob.data


def get_vector_at_commit(repo: Repo, file_path: str, commit_sha: str) -> list[float] | None:
    """Get the embedding vector for a file at a specific commit."""
    # Derive vector path: projectA/file.txt -> projectA/.vectors/file.txt.vec
    parts = file_path.split("/")
    vec_parts = parts[:-1] + [VECTOR_DIR, f"{parts[-1]}{VECTOR_SUFFIX}"]
    vec_path = "/".join(vec_parts)

    try:
        data = get_file_at_commit(repo, vec_path, commit_sha)
    except KeyError:
        return None

    count = len(data) // 4
    return list(struct.unpack(f"<{count}f", data))


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

        # Create project directories with files
        for name in ["projectA", "projectB"]:
            proj = root / name
            proj.mkdir()

        # projectA: initial content + vector
        file_a = root / "projectA" / "file.txt"
        file_a.write_text("projectA v1\n")
        store_vector(file_a, [0.1, 0.2, 0.3, 0.4])

        # projectB: initial content + vector
        file_b = root / "projectB" / "data.txt"
        file_b.write_text("projectB v1\n")
        store_vector(file_b, [1.0, 2.0, 3.0])

        print(f"=== Test monorepo at {root} ===\n")

        repo = init_repo(str(root))

        # Commit each project independently
        commit_a1 = commit_project(repo, "projectA", "projectA: initial with vector")
        commit_b1 = commit_project(repo, "projectB", "projectB: initial with vector")

        # Update projectA: new content + new vector
        file_a.write_text("projectA v2\n")
        store_vector(file_a, [0.5, 0.6, 0.7, 0.8])
        commit_a2 = commit_project(repo, "projectA", "projectA: update content + vector")

        # Show history
        log_project(repo, "projectA")
        log_project(repo, "projectB")

        # Read historical versions (content + vector)
        print(f"\n=== Historical content + vector for projectA/file.txt ===")
        content_a1 = get_file_at_commit(repo, "projectA/file.txt", commit_a1).decode().strip()
        vector_a1 = get_vector_at_commit(repo, "projectA/file.txt", commit_a1)
        print(f"@ commit_a1 ({commit_a1[:8]}): content={repr(content_a1)}, vector={vector_a1}")

        content_a2 = get_file_at_commit(repo, "projectA/file.txt", commit_a2).decode().strip()
        vector_a2 = get_vector_at_commit(repo, "projectA/file.txt", commit_a2)
        print(f"@ commit_a2 ({commit_a2[:8]}): content={repr(content_a2)}, vector={vector_a2}")

        # Roll back projectA (should restore both content AND vector)
        print(f"\n=== Rolling back projectA to {commit_a1[:8]} ===")
        rollback_project(repo, "projectA", commit_a1)

        # Verify
        print(f"\n=== Verification after rollback ===")
        current_content = file_a.read_text().strip()
        current_vector = load_vector(file_a)
        print(f"projectA/file.txt: {repr(current_content)}")
        print(f"projectA/file.txt vector: {current_vector}")
        print(f"projectB/data.txt unchanged: {file_b.read_text().strip() == 'projectB v1'}")
        print(f"projectB vector unchanged: {load_vector(file_b) == [1.0, 2.0, 3.0]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
