#!/usr/bin/env python3
"""Extract local git commit facets without GitHub/GitLab provider access."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


def run_git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:
        raise SystemExit("git is required for local commit facets")
    return subprocess.run(["git", "-C", str(repo_path), *args], text=True, capture_output=True)


def git_stdout(repo_path: Path, args: list[str], message: str) -> str:
    result = run_git(repo_path, args)
    if result.returncode != 0:
        raise SystemExit(f"{message}:\n{result.stderr.strip()}")
    return result.stdout


def resolve_snapshot_sha(repo_path: Path, snapshot_ref: str) -> str:
    return git_stdout(
        repo_path,
        ["rev-parse", "--verify", f"{snapshot_ref}^{{commit}}"],
        f"git could not resolve snapshot ref {snapshot_ref!r} in {repo_path}",
    ).strip()


def normalize_remote_path(path: str) -> str:
    repo_path = path.strip().lstrip("/").rstrip("/")
    if repo_path.endswith(".git"):
        repo_path = repo_path[:-4]
    return repo_path


def repo_name_from_remote(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        match = re.match(r"^(?:[^@/\s]+@)?[^:/\s]+:(?P<path>.+)$", url)
        return normalize_remote_path(match.group("path")) if match else ""
    parsed = urlparse(url)
    return normalize_remote_path(parsed.path)


def repo_name(repo_path: Path) -> str:
    remote = run_git(repo_path, ["remote", "get-url", "origin"])
    parsed = repo_name_from_remote(remote.stdout.strip()) if remote.returncode == 0 else ""
    return parsed or repo_path.name


def unique(values: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    out: list[Any] = []
    for value in values:
        if value in (None, "") or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def path_module(path: str) -> str:
    parts = [part for part in path.split("/") if part and part not in {".", ".."}]
    if not parts:
        return ""
    if parts[0] == ".github":
        return ".github/workflows"
    if "." in parts[0] and len(parts) == 1:
        return ""
    return parts[0]


def path_modules(files: list[str]) -> list[str]:
    return unique(module for module in (path_module(path) for path in files) if module)


def key_files(files: list[str], limit: int = 8) -> list[str]:
    def score(path: str) -> tuple[int, int, str]:
        lower = path.lower()
        value = 0
        if "readme" in lower or lower.endswith((".md", ".rst")):
            value += 2
        if any(token in lower for token in ["/src/", "/core/", "/server", "/api", "/train", "/rollout", "/loss", "/model", "/actor", "/data"]):
            value += 4
        if lower.endswith((".py", ".ts", ".tsx", ".rs", ".go", ".sh", ".yaml", ".yml", ".toml", ".json")):
            value += 1
        return (-value, len(path), path)

    return sorted(files, key=score)[:limit]


def bounded_summary(parts: Iterable[str], max_chars: int) -> str:
    text = "\n\n".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def extract_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for match in re.finditer(r"\b[$A-Z_a-z][$\w]*\b", text):
        value = match.group(0)
        looks_symbolic = (
            re.search(r"[a-z][A-Z]", value) is not None
            or re.match(r"^[A-Z0-9_]{2,}$", value) is not None
            or "_" in value
            or "$" in value
        )
        if looks_symbolic:
            symbols.append(value)
    return unique(symbols)


def parse_numstat(text: str) -> tuple[list[str], int, int]:
    files: list[str] = []
    additions = 0
    deletions = 0
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add, delete, path = parts[0], parts[1], parts[2]
        if add.isdigit():
            additions += int(add)
        if delete.isdigit():
            deletions += int(delete)
        if path:
            files.append(path)
    return unique(files), additions, deletions


def commit_facet(repo: str, repo_path: Path, sha: str, summary_chars: int) -> dict[str, Any]:
    fields = git_stdout(
        repo_path,
        ["show", "-s", "--date=iso-strict", "--format=%H%x1f%h%x1f%P%x1f%an%x1f%ad%x1f%s", sha],
        f"git could not read commit metadata for {sha}",
    ).rstrip("\n").split("\x1f")
    full_sha, short_sha, parents, author, authored_at, title = (fields + [""] * 6)[:6]
    body = git_stdout(repo_path, ["show", "-s", "--format=%B", sha], f"git could not read commit body for {sha}")
    files, additions, deletions = parse_numstat(
        git_stdout(repo_path, ["show", "--format=", "--numstat", "--no-renames", sha], f"git could not read commit diff for {sha}")
    )
    modules = path_modules(files)
    keys = key_files(files)
    summary = bounded_summary([body], summary_chars) or title
    evidence = unique([
        f"commit {short_sha}: {title}" if short_sha and title else "",
        f"commit {short_sha} changed {', '.join(keys)}" if short_sha and keys else "",
    ])
    return {
        "facetId": f"commit.{short_sha or full_sha[:12]}",
        "sourceType": "commit",
        "provider": "local_git",
        "repo": repo,
        "sha": full_sha,
        "short_sha": short_sha,
        "parents": [parent for parent in parents.split() if parent],
        "is_merge": len([parent for parent in parents.split() if parent]) > 1,
        "title": title,
        "summary": summary,
        "author": author,
        "authoredAt": authored_at,
        "updatedAt": authored_at,
        "files": files,
        "path_modules": modules,
        "key_files": keys,
        "changed_files": len(files),
        "additions": additions,
        "deletions": deletions,
        "symbols": extract_symbols("\n".join([title, summary, *files])),
        "evidence": evidence,
    }


def fetch_facets(repo_path: Path, snapshot_ref: str, limit: int, summary_chars: int) -> list[dict[str, Any]]:
    snapshot_sha = resolve_snapshot_sha(repo_path, snapshot_ref)
    shas = git_stdout(
        repo_path,
        ["rev-list", "--max-count", str(limit), snapshot_sha],
        f"git could not list commits from {snapshot_ref!r} in {repo_path}",
    ).splitlines()
    name = repo_name(repo_path)
    return [commit_facet(name, repo_path, sha.strip(), summary_chars) for sha in shas if sha.strip()]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract local git commit SourceFacet JSON without provider login.")
    parser.add_argument("--repo-path", default=".", help="Local git repository path")
    parser.add_argument("--snapshot-ref", default="HEAD", help="Local git ref whose history should be collected")
    parser.add_argument("--limit", type=int, default=30, help="Maximum commits retained from the snapshot history")
    parser.add_argument("--summary-chars", type=int, default=4000)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--out", help="Write JSON to this file instead of stdout")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 500:
        parser.error("--limit must be from 1 to 500")
    if args.summary_chars < 100:
        parser.error("--summary-chars must be at least 100")
    args.repo_path = Path(args.repo_path).expanduser().resolve()
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    facets = fetch_facets(args.repo_path, args.snapshot_ref, args.limit, args.summary_chars)
    text = json.dumps(facets, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
