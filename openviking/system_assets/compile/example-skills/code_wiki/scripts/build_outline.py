#!/usr/bin/env python3
"""Deterministic, DeepWiki-style pre-Map outline for code-wiki compilation.

Standalone, standard-library-only. It performs NO network access and reads NO file
contents: structure planning uses only the repository file tree that the framework
derives from seeded evidence (relative paths, URIs and per-file line ranges), exactly
the "directory tree + README" inputs DeepWiki uses for its chapter plan.

It deterministically decides a fixed, path-locked page plan:

1. ``index.md`` (overview): every README and root manifest / entry-point file. When the
   tree has none, it anchors on a single small root file (``__init__.py`` preferred) so
   the landing page stays small instead of duplicating the whole root bucket.
2. ``entity/<package>.md``: one page per top-level package directory.
3. ``entity/root.md``: top-level files that live outside any package directory.

Size control (second-level split): the atomic unit is a seeded line range (the framework
already chunks files into ~250-330 line records). When the ranges in one package/root
bucket exceed ``_MAX_PAGE_LINES``, they are greedily packed into multiple pages
(``entity/root.md``, ``entity/root-2.md`` ...). Ranges are disjoint, so each seeded
record is read by exactly one page and no oversized page (or duplicated source) results.

Every seeded file is assigned to exactly one bucket page (the overview anchor / README
may also appear there, by design), so coverage is total and the framework needs no legacy
fallback.

Input: the manifest JSON file path given as argv[1] (or stdin when no argument)::

    {"files": [{"path": "src/click/core.py", "uri": "...",
                "ranges": [[1, 400], [401, 800]]}, ...]}

Output: one JSON object on stdout::

    {"pages": [{"id": "...", "title": "...", "path": "entity/foo.md",
                "sources": [{"uri": "...", "start_line": 1, "end_line": 400}]}]}

Exit code is non-zero on invalid input. Run ``python3 build_outline.py --selftest``.
"""

from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from typing import Any

_README_RE = re.compile(r"^readme(\.[a-z0-9]+)?$", re.IGNORECASE)
_MANIFEST_BASENAMES = frozenset(
    {
        "cargo.toml",
        "cmakelists.txt",
        "composer.json",
        "gemfile",
        "go.mod",
        "makefile",
        "package.json",
        "pom.xml",
        "pyproject.toml",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
    }
)
_ENTRYPOINT_BASENAMES = frozenset(
    {
        "__main__.py",
        "app.py",
        "asgi.py",
        "cli.py",
        "main.go",
        "main.py",
        "manage.py",
        "server.py",
        "wsgi.py",
    }
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Soft cap on seeded source lines packed into one page. A single seeded range is always
# smaller than this, so every produced page stays at roughly _MAX_PAGE_LINES (+ one
# range at most), which keeps the downstream reduce prompt bounded.
_MAX_PAGE_LINES = 1800

# Universal wrapper directories that add no module identity; skipped when a real
# package directory follows them (src/click/core.py -> package "click").
_WRAPPER_DIRS = frozenset({"src", "lib", "libs", "source", "sources"})


def _slug(value: str) -> str:
    """Flatten a package name to a stable, path-safe page slug."""
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "misc"


def _title(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _is_readme(path: str) -> bool:
    return bool(_README_RE.match(path.rsplit("/", 1)[-1]))


def _is_root_manifest(path: str) -> bool:
    if "/" in path:
        return False
    base = path.rsplit("/", 1)[-1].lower()
    return base in _MANIFEST_BASENAMES or base in _ENTRYPOINT_BASENAMES


def _package(path: str) -> str | None:
    """Return the first real module directory, or None for a top-level file."""
    segments = path.split("/")
    if len(segments) == 1:
        return None
    first = segments[0]
    if first.lower() in _WRAPPER_DIRS and len(segments) > 2:
        return segments[1]
    return first


def _node_spans(node: dict) -> list[dict]:
    """All seeded spans of one file as source dicts, ordered by start line."""
    return [
        {"uri": node["uri"], "start_line": int(start), "end_line": int(end)}
        for start, end in sorted(node.get("ranges", []), key=lambda span: int(span[0]))
    ]


def _file_lines(node: dict) -> int:
    return sum(int(end) - int(start) + 1 for start, end in node.get("ranges", []))


def _pack(nodes: list[dict]) -> list[list[dict]]:
    """Greedily pack disjoint seeded spans into bins of at most _MAX_PAGE_LINES.

    Spans keep their file order. A span that would overflow the current bin starts a new
    page, so a single large file (many ~250-line chunks) is spread across several pages
    while small sibling files share a page. Each span appears in exactly one bin.
    """
    bins: list[list[dict]] = []
    current: list[dict] = []
    current_lines = 0
    for node in nodes:
        for span in _node_spans(node):
            size = span["end_line"] - span["start_line"] + 1
            if current and current_lines + size > _MAX_PAGE_LINES:
                bins.append(current)
                current, current_lines = [], 0
            current.append(span)
            current_lines += size
    if current:
        bins.append(current)
    return bins


def _make_page(page_id: str, title: str, path: str, sources: list[dict]) -> dict:
    return {"id": page_id, "title": title, "path": path, "sources": sources}


def _anchor_nodes(files: list[dict], root_files: list[dict]) -> list[dict]:
    """Pick a small, whole-file anchor for the landing page when no README exists.

    Prefer the package ``__init__.py``, then the smallest root file; fall back to the
    smallest file overall. The file is assigned whole so it is excluded from bucket pages
    and never duplicated (mirroring how a README anchors the overview).
    """
    candidates = root_files or files
    init = [node for node in candidates if node["path"].rsplit("/", 1)[-1] == "__init__.py"]
    pool = init or candidates
    return [min(pool, key=_file_lines)]


def build_outline(manifest: dict[str, Any]) -> dict[str, Any]:
    """Plan overview + size-bounded per-package pages from the file tree alone."""
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest.files must be a non-empty list")

    # Normalize and bucket by top-level package; root-level files use the "" bucket.
    overview: list[dict] = []
    packages: "OrderedDict[str, list[dict]]" = OrderedDict()
    root_files: list[dict] = []

    seen_paths: set[str] = set()
    for node in files:
        path = node.get("path")
        uri = node.get("uri")
        ranges = node.get("ranges")
        if not isinstance(path, str) or not path or not isinstance(uri, str):
            raise ValueError("each file needs a non-empty path and uri")
        if not isinstance(ranges, list) or not ranges:
            raise ValueError(f"file {path} needs at least one line range")
        for span in ranges:
            if (
                not isinstance(span, (list, tuple))
                or len(span) != 2
                or int(span[0]) > int(span[1])
            ):
                raise ValueError(f"file {path} has an invalid range: {span!r}")
        if path in seen_paths:
            raise ValueError(f"duplicate file path in manifest: {path}")
        seen_paths.add(path)

        if _is_readme(path) or _is_root_manifest(path):
            overview.append(node)

        package = _package(path)
        if package is not None:
            packages.setdefault(package, []).append(node)
        else:
            root_files.append(node)

    # Landing page: explicit README/manifest files when present, otherwise one small anchor.
    index_nodes = overview or _anchor_nodes(files, root_files)
    index_paths = {node["path"] for node in index_nodes}

    pages: list[dict] = []
    used_paths: set[str] = set()

    def add(page: dict) -> None:
        if not page["sources"]:
            return
        if page["path"] in used_paths:
            raise ValueError(f"outline page path collision: {page['path']}")
        used_paths.add(page["path"])
        pages.append(page)

    add(_make_page("overview", "Overview", "index.md", [s for n in index_nodes for s in _node_spans(n)]))

    def emit_bucket(page_prefix: str, slug: str, nodes: list[dict]) -> None:
        bins = _pack(nodes)
        for index, sources in enumerate(bins):
            if index == 0:
                page_id, path = f"{page_prefix}-{slug}", f"entity/{slug}.md"
            else:
                page_id, path = f"{page_prefix}-{slug}-{index + 1}", f"entity/{slug}-{index + 1}.md"
            title = _title(slug) if index == 0 else f"{_title(slug)} (Part {index + 1})"
            add(_make_page(page_id, title, path, sources))

    for package, members in packages.items():
        emit_bucket("entity", _slug(package), members)

    # Top-level files not already used whole by the landing/overview page.
    leftover = [node for node in root_files if node["path"] not in index_paths]
    if leftover:
        emit_bucket("entity", "root", leftover)

    if not pages:  # Defensive: coverage must never silently collapse to zero pages.
        raise ValueError("outline produced no pages")

    return {"pages": pages}


def _selftest() -> None:
    manifest = {
        "files": [
            {"path": "README.md", "uri": "u://r/README.md", "ranges": [[1, 100]]},
            {"path": "pyproject.toml", "uri": "u://r/pyproject.toml", "ranges": [[1, 40]]},
            {"path": "main.py", "uri": "u://r/main.py", "ranges": [[1, 20]]},
            {"path": "helpers.py", "uri": "u://r/helpers.py", "ranges": [[1, 15]]},
            {"path": "src/click/core.py", "uri": "u://r/src/click/core.py",
             "ranges": [[1, 90], [91, 180]]},
            {"path": "src/click/utils.py", "uri": "u://r/src/click/utils.py",
             "ranges": [[1, 60]]},
            {"path": "tests/test_core.py", "uri": "u://r/tests/test_core.py",
             "ranges": [[1, 30]]},
        ]
    }
    outline = build_outline(manifest)
    paths = [p["path"] for p in outline["pages"]]
    assert paths == ["index.md", "entity/click.md", "entity/tests.md", "entity/root.md"], paths
    by_path = {p["path"]: p for p in outline["pages"]}
    # Overview keeps README + root manifest/entry-point files.
    overview_uris = {s["uri"] for s in by_path["index.md"]["sources"]}
    assert {
        "u://r/README.md",
        "u://r/pyproject.toml",
        "u://r/main.py",
    } <= overview_uris
    # The src/ wrapper is skipped: module page is "click" and carries every seeded span.
    src_sources = by_path["entity/click.md"]["sources"]
    assert {(s["uri"], s["start_line"], s["end_line"]) for s in src_sources} == {
        ("u://r/src/click/core.py", 1, 90),
        ("u://r/src/click/core.py", 91, 180),
        ("u://r/src/click/utils.py", 1, 60),
    }
    # Ordinary top-level files land in the root page.
    assert {s["uri"] for s in by_path["entity/root.md"]["sources"]} == {"u://r/helpers.py"}
    # Every input file is covered by at least one page.
    covered = {s["uri"] for p in outline["pages"] for s in p["sources"]}
    assert covered == {f["uri"] for f in manifest["files"]}

    # Second-level split: a large root file (12 x 200-line chunks) is packed across
    # bounded pages, each chunk assigned to exactly one page (no overlap, no loss).
    big_ranges = [[200 * i + 1, 200 * (i + 1)] for i in range(12)]
    split_manifest = {
        "files": [
            {"path": "__init__.py", "uri": "u://r/__init__.py", "ranges": [[1, 10]]},
            {"path": "big.py", "uri": "u://r/big.py", "ranges": big_ranges},
        ]
    }
    split = build_outline(split_manifest)
    split_paths = [p["path"] for p in split["pages"]]
    assert split_paths == ["index.md", "entity/root.md", "entity/root-2.md"], split_paths
    split_by = {p["path"]: p for p in split["pages"]}
    # No README: the small __init__ anchors the landing page whole and is NOT duplicated
    # into the root bucket.
    assert [s["uri"] for s in split_by["index.md"]["sources"]] == ["u://r/__init__.py"]
    root_spans = [
        (s["uri"], s["start_line"], s["end_line"])
        for path in ("entity/root.md", "entity/root-2.md")
        for s in split_by[path]["sources"]
    ]
    # big.py chunks partition exactly: 9 + 3, no duplication, nothing lost.
    assert len(root_spans) == 12
    assert len(set(root_spans)) == 12
    assert {(u, a, b) for u, a, b in root_spans} == {
        ("u://r/big.py", a, b) for a, b in big_ranges
    }
    for path in ("entity/root.md", "entity/root-2.md"):
        lines = sum(s["end_line"] - s["start_line"] + 1 for s in split_by[path]["sources"])
        assert lines <= _MAX_PAGE_LINES, (path, lines)

    print(json.dumps(outline, indent=2))
    print("SELFTEST_OK", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--selftest":
        _selftest()
        return 0
    if len(argv) > 3:
        print("usage: build_outline.py [manifest.json] [outline_out.json]", file=sys.stderr)
        return 2
    out_path = argv[2] if len(argv) == 3 else None
    raw = open(argv[1], encoding="utf-8").read() if len(argv) >= 2 else sys.stdin.read()
    try:
        manifest = json.loads(raw)
        outline = build_outline(manifest)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"outline error: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(outline, ensure_ascii=False)
    if out_path:
        # File contract: keep stdout tiny (the Direct backend truncates stdout at 10k
        # characters). The framework reads the outline JSON from this file, no size cap.
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
        print(f"outline written: {out_path}")
    else:
        sys.stdout.write(payload)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
