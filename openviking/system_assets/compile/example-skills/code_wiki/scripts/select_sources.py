#!/usr/bin/env python3
"""Deterministic README-first source selection for code-wiki compilation.

Standalone, standard-library-only Skill helper. It performs NO network and NO
file-content reads: the agent first exports the repository file tree (relative
paths only, e.g. via the OpenViking ``list`` tool) to a JSON file, and this
script deterministically decides:

1. ``selected_files``: up to ``max_files`` (default 64) cheap overview files to
   preload (READMEs, manifests, entry points, a capped number of docs).
2. ``major_subsystems``: canonical module ids/labels derived from tree shape.
   Every id must be covered by at least one Wiki page frontmatter ``covers``.
3. ``file_tree``: a breadth-balanced bounded structural tree for planning.

Input JSON (UTF-8 file or stdin), either a bare array of paths or an object::

    {"paths": ["src/click/core.py", "README.rst", ...], "max_files": 64}

Output: one JSON object on stdout. Exit code is non-zero on invalid input.

Run ``python3 select_sources.py --selftest`` to verify the materialized copy.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

try:  # POSIX file lock (macOS/Linux); absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

# ---------------------------------------------------------------------------
# Scoring tables (path-name based; no file contents are ever inspected)
# ---------------------------------------------------------------------------

_MANIFEST_NAMES = frozenset(
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
_ENTRYPOINT_NAMES = frozenset(
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
_DOC_INDEX_NAMES = frozenset(
    {
        "contents.md",
        "contents.rst",
        "contents.txt",
        "index.md",
        "index.rst",
        "index.txt",
    }
)
_STRUCTURE_TERMS = (
    "architecture",
    "design",
    "developer",
    "internals",
    "introduction",
    "overview",
)
_IGNORED_GENERATED_NAMES = frozenset(
    {".abstract.md", ".overview.md", ".relations.json", ".source.json"}
)
_LANGUAGES = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".lua": "Lua",
    ".php": "PHP",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scala": "Scala",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}

# Extensions accepted into the bounded structural tree handed to the planner.
_FILE_TREE_EXTENSIONS = frozenset(
    {
        ".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".kt",
        ".kts", ".lua", ".php", ".py", ".rb", ".rs", ".scala", ".swift",
        ".ts", ".tsx", ".html", ".css", ".h", ".hpp", ".m", ".mm",
        ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
    }
)

# Directory segments always excluded from the structural tree.
_FILE_TREE_EXCLUDED_DIRS = frozenset(
    {
        ".git", ".svn", ".hg",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".mypy",
        ".venv", "venv", "env",
        "node_modules",
        "dist", "build", "out", "target", "bin",
        ".idea", ".vscode", ".vs",
        "logs", "log", "tmp", "temp",
        "docs", "_docs", "site-docs",
        "tests", "test", "_tests", "testing",
    }
)

# File names excluded from the structural tree regardless of extension.
_FILE_TREE_EXCLUDED_FILES = frozenset(
    {
        ".ds_store", "thumbs.db", "desktop.ini",
        ".env", ".gitignore", ".gitattributes", ".eslintrc",
        "tsconfig.json", "pyproject.toml", "cargo.lock",
        "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "package-lock.json",
        "go.sum",
    }
)

# Max paths kept in the structural tree (~50-60 KB of structural context).
_FILE_TREE_MAX_FILES = 1200

# Preload budget defaults.
DEFAULT_MAX_FILES = 64
# Cap on preloaded prose documentation; README/manifest/entrypoints are free.
_MAX_DOC_FILES = 8
# Reasons that represent prose documentation rather than code/structure.
_DOC_REASONS = frozenset({"docs_index", "architecture_doc", "docs", "root_doc"})

# --- Major subsystem inventory constants -----------------------------------
_CODE_EXTENSIONS = frozenset(_LANGUAGES)
_MODULE_NOISE_PARTS = frozenset(
    {
        ".git", ".svn", ".hg", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".venv", "venv", "env", "node_modules", "dist", "build", "out",
        "target", "bin", "locale", "locales",
    }
)
_MODULE_SUITE_DIRS = frozenset({"tests", "test", "testing", "_tests", "js_tests"})
# Example/demo trees are shipped prose-like evidence, not library subsystems;
# they must never produce their own canonical topic (fold into cited topics).
_MODULE_EXAMPLE_DIRS = frozenset(
    {"examples", "example", "demos", "demo", "samples", "sample", "recipes"}
)
_FLAT_PACKAGE_MIN_FILES = 5
_FLAT_PACKAGE_DIRECT_SHARE = 0.8
_SPLIT_DIRECT_MAX = 7
_SPLIT_CHILD_MIN = 2
_CHILD_FILES_MIN = 4
_MIN_MODULE_FILES = 3
_MAX_MAJOR_SUBSYSTEMS = 40
_ROOT_PACKAGE_SHARE = 0.6


def _normalize_path(path: str) -> str:
    """Normalize one exported path to a clean repository-relative POSIX path."""
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _derive_major_subsystems(relative_paths: list[str]) -> list[dict[str, Any]]:
    """Map the code tree to a ranked list of major subsystems to document.

    Vendor/build/locale noise is dropped, while a framework's own in-package
    test-support code (e.g. ``django/test``) is kept and the root test suite is
    not. Namespace packages (few direct files, several substantial children)
    are descended (``django/db`` -> ``db/models``, ``db/migrations``); cohesive
    packages and abstract-``base`` + implementations groups stay whole.
    """
    code_paths: list[str] = []
    for relative in relative_paths:
        parts = relative.split("/")
        if any(part.casefold() in _MODULE_NOISE_PARTS for part in parts[:-1]):
            continue
        if PurePosixPath(relative).suffix.casefold() not in _CODE_EXTENSIONS:
            continue
        if any(
            part.casefold() in _MODULE_EXAMPLE_DIRS
            or part.casefold() in _MODULE_SUITE_DIRS
            for part in parts[:-1]
        ):
            continue
        code_paths.append(relative)
    if not code_paths:
        return []

    non_suite = [
        path
        for path in code_paths
        if path.split("/", 1)[0].casefold() not in _MODULE_SUITE_DIRS
    ]
    if not non_suite:
        return []
    root_name, root_n = Counter(
        path.split("/", 1)[0] for path in non_suite
    ).most_common(1)[0]
    code_paths = [
        path
        for path in code_paths
        if not (
            path.split("/", 1)[0].casefold() in _MODULE_SUITE_DIRS
            and path.split("/", 1)[0] != root_name
        )
    ]
    strip_root = root_name if root_n / len(non_suite) >= _ROOT_PACKAGE_SHARE else ""

    direct: Counter[str] = Counter()
    subtree: Counter[str] = Counter()
    children: dict[str, set[str]] = {}
    for path in code_paths:
        dirs = path.split("/")[:-1]
        if not dirs:
            continue
        direct["/".join(dirs)] += 1
        for depth in range(1, len(dirs) + 1):
            subtree["/".join(dirs[:depth])] += 1
        children.setdefault("", set()).add(dirs[0])
        for depth in range(1, len(dirs)):
            parent, child = "/".join(dirs[:depth]), "/".join(dirs[: depth + 1])
            children.setdefault(parent, set()).add(child)

    def is_group(node: str) -> bool:
        """A namespace to descend, versus a cohesive module to stop at."""
        if node == "":
            return True
        if direct[node] > _SPLIT_DIRECT_MAX:
            return False
        node_children = children.get(node, ())
        if any(child.rsplit("/", 1)[-1] == "base" for child in node_children):
            return False
        substantial = sum(subtree[c] >= _CHILD_FILES_MIN for c in node_children)
        return substantial >= _SPLIT_CHILD_MIN

    assigned: Counter[str] = Counter()
    for path in code_paths:
        dirs = path.split("/")[:-1]
        idx = 0
        while idx < len(dirs) - 1 and is_group("/".join(dirs[: idx + 1])):
            idx += 1
        module = "/".join(dirs[: idx + 1])
        if strip_root:
            prefix = strip_root + "/"
            if module == strip_root:
                continue
            if not module.startswith(prefix):
                continue
            module = module[len(prefix) :]
        if module:
            assigned[module] += 1

    modules = [
        {
            "id": re.sub(r"[^A-Za-z0-9]+", "_", module).strip("_"),
            "label": module,
            "file_count": count,
        }
        for module, count in assigned.items()
        if count >= _MIN_MODULE_FILES
    ]
    modules.sort(key=lambda item: (-item["file_count"], item["label"]))
    return modules[:_MAX_MAJOR_SUBSYSTEMS]


def _balanced_tree_paths(
    eligible: list[tuple[str, str]], limit: int
) -> list[str]:
    """Return up to ``limit`` relative paths with top-level breadth.

    Round-robins across top-level segments so a cap cannot erase whole
    later-alphabetic subsystems; within a segment shallowest, first wins.
    """
    buckets: dict[str, list[str]] = {}
    order: list[str] = []
    for relative, top in eligible:
        if top not in buckets:
            buckets[top] = []
            order.append(top)
        buckets[top].append(relative)

    for paths in buckets.values():
        paths.sort(key=lambda p: (len(PurePosixPath(p).parts), p))

    picked: list[str] = []
    indexes = dict.fromkeys(order, 0)
    while len(picked) < limit:
        progressed = False
        for top in order:
            if len(picked) >= limit:
                break
            idx = indexes[top]
            if idx < len(buckets[top]):
                picked.append(buckets[top][idx])
                indexes[top] = idx + 1
                progressed = True
        if not progressed:
            break
    return picked


def _path_excluded_from_tree(path: str) -> bool:
    """Return True if a repository-relative path should be skipped in the tree."""
    parts = path.split("/")
    if any(part.casefold() in _FILE_TREE_EXCLUDED_DIRS for part in parts):
        return True
    if parts and parts[-1].casefold() in _FILE_TREE_EXCLUDED_FILES:
        return True
    return False


def _selection_score(path: str) -> tuple[int, str] | None:
    """Rank low-cost repository evidence without inspecting file contents."""

    pure = PurePosixPath(path)
    name = pure.name.casefold()
    parts = tuple(part.casefold() for part in pure.parts)
    depth = max(0, len(parts) - 1)

    if name in _IGNORED_GENERATED_NAMES:
        return None
    if name.startswith("readme"):
        return 1000 - depth, "readme"
    if name in _MANIFEST_NAMES or (
        name.startswith("requirements") and name.endswith(".txt")
    ):
        return 900 - depth, "manifest"
    if name in _ENTRYPOINT_NAMES:
        return 850 - depth, "entrypoint"

    in_docs = bool(parts and parts[0] in {"doc", "docs", "documentation"})
    if in_docs and name in _DOC_INDEX_NAMES:
        return 820 - depth, "docs_index"
    if in_docs and any(term in "/".join(parts) for term in _STRUCTURE_TERMS):
        return 790 - depth, "architecture_doc"
    if in_docs and depth <= 2 and pure.suffix.casefold() in {".md", ".rst", ".txt"}:
        return 740 - depth, "docs"
    if depth <= 1 and pure.suffix.casefold() in {".md", ".rst", ".txt"}:
        return 700 - depth, "root_doc"
    return None


def select_sources(
    paths: list[str], *, max_files: int = DEFAULT_MAX_FILES
) -> dict[str, Any]:
    """Select overview evidence and derive a structural profile (pure)."""

    if max_files < 1:
        raise ValueError("max_files must be positive")

    files = sorted(dict.fromkeys(p for p in (_normalize_path(x) for x in paths) if p))

    ranked: list[tuple[int, str, str]] = []
    languages: Counter[str] = Counter()
    top_level: Counter[str] = Counter()

    for relative in files:
        pure = PurePosixPath(relative)
        if pure.parts:
            top_level[pure.parts[0]] += 1
        language = _LANGUAGES.get(pure.suffix.casefold())
        if language:
            languages[language] += 1
        score = _selection_score(relative)
        if score is not None:
            ranked.append((score[0], relative, score[1]))

    ranked.sort(key=lambda item: (-item[0], len(PurePosixPath(item[1]).parts), item[1]))

    # Apply the documentation cap while walking the score-sorted list. Code and
    # structural entries are always admitted. Docs beyond the cap are dropped;
    # remaining slots stay empty so implementation detail comes from bounded
    # grep/read probes rather than a documentation flood.
    selected_rows: list[tuple[int, str, str]] = []
    doc_count = 0
    for row in ranked:
        if len(selected_rows) >= max_files:
            break
        if row[2] in _DOC_REASONS:
            if doc_count >= _MAX_DOC_FILES:
                continue
            doc_count += 1
        selected_rows.append(row)

    if not selected_rows and files:
        fallback = sorted(
            ((len(PurePosixPath(uri).parts), uri) for uri in files),
            key=lambda item: (item[0], item[1]),
        )
        selected_rows = [(0, uri, "shallow_fallback") for _, uri in fallback[:max_files]]

    eligible_tree: list[tuple[str, str]] = []
    for relative in files:
        if not relative:
            continue
        if _path_excluded_from_tree(relative):
            continue
        if PurePosixPath(relative).suffix.casefold() not in _FILE_TREE_EXTENSIONS:
            continue
        top = PurePosixPath(relative).parts[0]
        eligible_tree.append((relative, top))

    tree_total = len(eligible_tree)
    file_tree_paths = _balanced_tree_paths(eligible_tree, _FILE_TREE_MAX_FILES)

    major_subsystems = _derive_major_subsystems(
        [relative for relative in files if relative]
    )

    # Flat single-package detection (src/<pkg>/*.py): one directory holds
    # almost all code files directly. Such repos are degenerate for page-topic
    # purposes even when the namespace rules happen to keep one <pkg> subsystem;
    # the AST canonical-topic derivation should split the core module there.
    code_py = [
        relative
        for relative in files
        if PurePosixPath(relative).suffix.casefold() == ".py"
        and not any(
            part.casefold() in _MODULE_NOISE_PARTS
            or part.casefold() in _MODULE_SUITE_DIRS
            or part.casefold() in _MODULE_EXAMPLE_DIRS
            for part in relative.split("/")[:-1]
        )
    ]
    flat_dir = _dominant_package_dir(code_py)
    direct_flat = sum(
        1
        for relative in code_py
        if (relative.rsplit("/", 1)[0] if "/" in relative else "") == flat_dir
    )
    flat_package = bool(
        flat_dir
        and direct_flat >= _FLAT_PACKAGE_MIN_FILES
        and direct_flat >= _FLAT_PACKAGE_DIRECT_SHARE * len(code_py)
    )

    return {
        "strategy": "readme_first",
        "total_files": len(files),
        "selected_file_count": len(selected_rows),
        "doc_file_count": doc_count,
        "doc_quota": _MAX_DOC_FILES,
        "language_distribution": [
            {"language": language, "file_count": count}
            for language, count in languages.most_common()
        ],
        "top_level_entries": [
            {"name": name, "file_count": count}
            for name, count in sorted(
                top_level.items(), key=lambda item: (-item[1], item[0])
            )[:40]
        ],
        "file_tree": {
            "files": file_tree_paths,
            "total": len(file_tree_paths),
            "eligible_total": tree_total,
            "truncated": tree_total > len(file_tree_paths),
        },
        # Breadth contract: each id must be covered by at least one Wiki page
        # frontmatter `covers` list. Labels are repository-relative.
        "major_subsystems": major_subsystems,
        # Degenerate breadth: the tree has code but the namespace rules derive
        # no subsystems (typical for a flat single-package layout such as
        # src/click/*.py). In that case a covers contract would be vacuous;
        # plan the page inventory from selected_files + file_tree instead and
        # do not invent subsystem ids.
        "breadth": {
            "subsystem_count": len(major_subsystems),
            "degenerate": not major_subsystems
            and any(
                PurePosixPath(relative).suffix.casefold() in _CODE_EXTENSIONS
                for relative in files
            ),
            "flat_package": flat_package,
            "flat_package_dir": flat_dir,
        },
        "selected_files": [
            {"path": path, "reason": reason}
            for _, path, reason in selected_rows
        ],
    }


# ---------------------------------------------------------------------------
# Canonical topic inventory (degenerate / flat single-package repositories)
#
# A flat library (src/<pkg>/*.py, zero derived subsystems) made the reduce fan
# out one page per shard with no canonical path contract, producing hundreds of
# near-duplicate pages. This routine derives a SMALL deterministic topic table
# purely from AST + import shape (no model, no content semantics):
#
#   * the "core" module (core.py, else the largest module) is split by its
#     public CLASS INHERITANCE ROOTS: each root is one entity topic and all of
#     its same-module descendants fold into it (Command <- MultiCommand <- Group
#     -> a single `command` topic; Parameter <- Option/Argument -> `parameter`).
#   * every other public module is ONE concept topic; all classes defined there
#     fold into that module (types.Choice/Path/IntRange -> `types`), so minor
#     exception subclasses / TypedDicts / type variants never get their own page.
#   * private modules (_termui_impl.py, _winconsole.py ...) fold into the public
#     module that imports them (import graph), else into a platform-compat topic.
#   * leftover public functions of the core module become one protected
#     `<core>-mechanics` deep chapter (only when there are several).
#
# The table maps each topic to a UNIQUE output path. The reduce author must pick
# paths only from this table; equal paths collide in the framework combiner.
# ---------------------------------------------------------------------------

_TOPIC_SRC_FILE_CAP = 48
_MECHANICS_MIN_FUNCTIONS = 3


def _slug(identifier: str) -> str:
    """CamelCase / snake_case -> kebab-case topic id."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "-", identifier)
    return spaced.lower().replace("_", "-").strip("-")


def _dominant_package_dir(py_paths: list[str]) -> str:
    """Directory whose DIRECT .py children dominate the code tree (flat pkg)."""
    direct: Counter[str] = Counter()
    for rel in py_paths:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        direct[parent] += 1
    if not direct:
        return ""
    # A package is "flat" when the busiest single directory holds a majority.
    parent, count = direct.most_common(1)[0]
    return parent if count * 2 >= len(py_paths) else ""


def _parse_module_ast(source: str) -> dict[str, Any] | None:
    """Extract public classes (+ bases), functions and private-module imports."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    classes: list[dict[str, Any]] = []
    functions: list[str] = []
    private_imports: Counter[str] = Counter()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            bases = [
                b.id
                for b in node.bases
                if isinstance(b, ast.Name)
            ]
            methods = sum(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                for n in node.body
            )
            classes.append(
                {"name": node.name, "bases": bases, "methods": methods}
            )
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        ):
            functions.append(node.name)
    # Walk the whole module (including `if os.name == "nt":` / TYPE_CHECKING
    # blocks) so platform/private imports are still seen.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            stem = node.module.rsplit(".", 1)[-1]
            if stem.startswith("_"):
                private_imports[stem] += len(node.names) or 1
    return {
        "classes": classes,
        "functions": functions,
        "private_imports": private_imports,
        "lines": source.count("\n") + 1,
    }


def _entity_id_for_root(root: str) -> str:
    """BaseCommand -> command (strip an abstract Base prefix, keep kebab)."""
    if root.startswith("Base") and len(root) > 4:
        root = root[4:]
    return _slug(root)


def derive_canonical_topics(
    paths: list[str],
    read_text: Any | None = None,
    *,
    degenerate: bool,
    subsystem_topics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Derive the canonical topic -> unique path table (pure given read_text).

    ``read_text(rel_path) -> str | None`` supplies Python sources; when it is
    None the table degrades to module-level concept topics (no class split).
    For non-degenerate repositories the derived major subsystems are used as-is.
    """
    if not degenerate:
        topics = [
            {
                "id": s["id"],
                "layer": "entity",
                "path": f"entity/{_slug(s['id'])}.md",
                "symbols": [],
                "modules": [s["label"]],
            }
            for s in (subsystem_topics or [])
        ]
        return _topic_payload(topics, folded=False)

    py_files = sorted(
        p
        for p in (_normalize_path(x) for x in paths)
        if p.endswith(".py")
        and not any(
            part.casefold() in _MODULE_SUITE_DIRS
            or part.casefold() in _MODULE_EXAMPLE_DIRS
            for part in p.split("/")[:-1]
        )
    )
    pkg_dir = _dominant_package_dir(py_files)
    flat = [p for p in py_files if (p.rsplit("/", 1)[0] if "/" in p else "") == pkg_dir]
    if not flat:  # not actually a flat package; nothing safe to invent.
        return _topic_payload([], folded=False)

    public = [
        p
        for p in flat
        if not PurePosixPath(p).name.startswith("_")
        and PurePosixPath(p).name != "__init__.py"
    ]
    private = [
        p
        for p in flat
        if PurePosixPath(p).name.startswith("_")
        and PurePosixPath(p).name != "__init__.py"
    ]

    parsed: dict[str, dict[str, Any]] = {}
    for rel in (public + private)[:_TOPIC_SRC_FILE_CAP]:
        if read_text is None:
            continue
        source = read_text(rel)
        if source:
            info = _parse_module_ast(source)
            if info:
                parsed[PurePosixPath(rel).stem] = info

    # Choose the core module: an explicit core.py, else the largest by surface.
    def surface(stem: str) -> int:
        info = parsed.get(stem)
        if info:
            return sum(c["methods"] for c in info["classes"]) * 4 + len(
                info["functions"]
            ) + info["lines"] // 50
        return 0

    core_stem = next(
        (PurePosixPath(p).stem for p in public if PurePosixPath(p).stem == "core"),
        None,
    )
    if core_stem is None and parsed:
        core_stem = max(parsed, key=lambda s: surface(s))
    core_rel = next(
        (p for p in public if PurePosixPath(p).stem == core_stem), None
    )

    topics: list[dict[str, Any]] = []

    # Core module: one entity topic per inheritance ROOT, descendants fold in.
    if core_stem and core_stem in parsed:
        info = parsed[core_stem]
        class_names = {c["name"] for c in info["classes"]}
        children: dict[str, list[str]] = {}
        for cls in info["classes"]:
            internal_parent = next(
                (b for b in cls["bases"] if b in class_names), None
            )
            if internal_parent:
                children.setdefault(internal_parent, []).append(cls["name"])

        def family_of(root_name: str) -> list[str]:
            out: list[str] = []
            stack = list(children.get(root_name, ()))
            seen = set()
            while stack:
                name = stack.pop()
                if name in seen:
                    continue
                seen.add(name)
                out.append(name)
                stack.extend(children.get(name, ()))
            return out

        roots = [
            c["name"]
            for c in info["classes"]
            if not any(b in class_names for b in c["bases"])
        ]
        no_method_roots: list[str] = []
        for root in roots:
            members = sorted({root, *family_of(root)})
            root_info = next(c for c in info["classes"] if c["name"] == root)
            if root_info["methods"] == 0:
                # Marker/enum/sentinel root with no behavior: not its own entity.
                no_method_roots.append(root)
                continue
            topics.append(
                {
                    "id": _entity_id_for_root(root),
                    "layer": "entity",
                    "path": f"entity/{_entity_id_for_root(root)}.md",
                    "symbols": members,
                    "modules": [PurePosixPath(core_rel).name] if core_rel else [],
                }
            )
        mechanics_symbols = sorted(info["functions"]) + sorted(no_method_roots)
        if len(set(mechanics_symbols)) >= _MECHANICS_MIN_FUNCTIONS:
            topics.append(
                {
                    "id": f"{_slug(core_stem)}-mechanics",
                    "layer": "concept",
                    "path": f"concept/{_slug(core_stem)}-mechanics.md",
                    "symbols": sorted(set(mechanics_symbols)),
                    "modules": [PurePosixPath(core_rel).name] if core_rel else [],
                    "protected_deep_chapter": True,
                }
            )

    # Attach private modules to the public module that imports them most,
    # following private->private import chains (_winconsole is only imported by
    # _termui_impl, which itself belongs to termui).
    private_stems = [PurePosixPath(p).stem for p in private]
    public_stems = [PurePosixPath(p).stem for p in public]

    def importers_of(pstem: str) -> Counter[str]:
        votes: Counter[str] = Counter()
        for stem, minfo in parsed.items():
            n = minfo["private_imports"].get(pstem, 0)
            if n:
                votes[stem] += n
        return votes

    def owner_of(pstem: str, _visiting: frozenset[str] = frozenset()) -> str | None:
        votes = importers_of(pstem)
        public_votes = Counter({k: v for k, v in votes.items() if k in public_stems})
        if public_votes:
            total = sum(public_votes.values())
            owner, n = sorted(
                public_votes.items(), key=lambda kv: (-kv[1], kv[0])
            )[0]
            if n * 2 >= total:
                return owner
        for importer in votes:
            if importer in private_stems and importer not in _visiting:
                chained = owner_of(importer, _visiting | {pstem})
                if chained:
                    return chained
        return None

    assigned_private: dict[str, str] = {}
    platform_private: list[str] = []
    for pstem in private_stems:
        owner = owner_of(pstem)
        if owner:
            assigned_private[pstem] = owner
            continue
        # Fallback: longest common public-stem prefix, else a compat topic.
        target = pstem.lstrip("_")

        def _lcp_len(stem: str) -> int:
            n = 0
            for a, b in zip(stem, target):
                if a != b:
                    break
                n += 1
            return n

        prefix_owner = max(public_stems, key=_lcp_len, default="")
        if prefix_owner and _lcp_len(prefix_owner) >= 3:
            assigned_private[pstem] = prefix_owner
        else:
            platform_private.append(pstem)

    # One concept topic per non-core public module (all its classes fold in).
    owner_private: dict[str, list[str]] = {}
    for pstem, owner in assigned_private.items():
        owner_private.setdefault(owner, []).append(pstem)
    for rel in public:
        stem = PurePosixPath(rel).name
        stem_noext = PurePosixPath(rel).stem
        if stem_noext == core_stem:
            continue
        symbols = sorted(
            {
                *(c["name"] for c in parsed.get(stem_noext, {}).get("classes", [])),
                *parsed.get(stem_noext, {}).get("functions", []),
            }
        )
        modules = [stem] + [
            f"{p}.py" for p in sorted(owner_private.get(stem_noext, []))
        ]
        topics.append(
            {
                "id": _slug(stem_noext),
                "layer": "concept",
                "path": f"concept/{_slug(stem_noext)}.md",
                "symbols": symbols,
                "modules": modules,
            }
        )
    if platform_private:
        topics.append(
            {
                "id": "platform-compat",
                "layer": "concept",
                "path": "concept/platform-compat.md",
                "symbols": [],
                "modules": [f"{p}.py" for p in sorted(platform_private)],
            }
        )

    # Stable ordering: entities first (by path), then concepts.
    topics.sort(key=lambda t: (t["layer"] != "entity", t["path"]))
    return _topic_payload(topics, folded=bool(parsed))


def _topic_payload(topics: list[dict[str, Any]], *, folded: bool) -> dict[str, Any]:
    core = len(topics)
    return {
        "canonical_topics": topics,
        "topic_inventory": {
            "core_topics": core,
            "with_index": core + 1 if core else 0,
            # upper bound leaves room for the index plus a few method how-tos;
            # the lower bound forbids diluting/merging distinct topics away.
            "recommended_total_range": [core, core + 4] if core else [],
            "class_inheritance_folded": folded,
        },
        "topic_policy": [
            "Every emitted page path MUST be one of canonical_topics[].path "
            "(one page per topic); the index is the only additional path.",
            "tests/, examples/, docs/ build or contribution walkthroughs do NOT "
            "get standalone pages; fold their evidence into the cited topic.",
            "Protected deep chapters keep full ordered mechanism steps and "
            "multiple anchors; do not dilute them into short landing summaries.",
        ],
    }


# ---------------------------------------------------------------------------
# CLI / JSON IO
# ---------------------------------------------------------------------------


def _first_json_token(text: str) -> str:
    """Drop any leading non-JSON banner (e.g. the ``ov`` wrapper's ``cmd: ...`` line)."""
    for i, ch in enumerate(text):
        if ch in "{[":
            return text[i:]
    raise ValueError("no JSON object or array found in input")


def _entry_rel_path(entry: dict[str, Any]) -> str | None:
    """Extract a repository-relative path from one ``ov ls -o json`` result row."""
    p = entry.get("rel_path")
    if not isinstance(p, str) or not p:
        uri = entry.get("uri")
        if isinstance(uri, str) and "://" in uri:
            # viking://resources/<repo>/src/x.py -> src/x.py : drop scheme+host+repo.
            rest = uri.split("://", 1)[1].split("/", 2)
            p = rest[2] if len(rest) == 3 else ""
        else:
            p = ""
    p = p.strip().lstrip("/")
    # Drop a leading repo-name segment if present (best effort; harmless if wrong).
    return p or None


def _from_ov_result(rows: list[Any]) -> list[str]:
    files: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("isDir") is True or str(row.get("type", "")).lower() == "dir":
            continue
        rel = _entry_rel_path(row)
        if rel:
            files.append(rel)
    return files


def _load_input(text: str) -> tuple[list[str], int | None]:
    data = json.loads(_first_json_token(text))
    if isinstance(data, list):
        if not all(isinstance(p, str) for p in data):
            raise ValueError("input array must contain only path strings")
        return data, None
    if isinstance(data, dict):
        # Raw `ov ls -r -o json` output: {"ok": true, "result": [ {rel_path,isDir}, ... ]}.
        result = data.get("result")
        if isinstance(result, list) and (not result or isinstance(result[0], dict)):
            paths = _from_ov_result(result)
            if paths:
                return paths, None
        paths = data.get("paths")
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError(
                "input must be a string array, {\"paths\": [...]}, or raw "
                "`ov ls -r -o json` output"
            )
        max_files = data.get("max_files")
        if max_files is not None and (not isinstance(max_files, int) or max_files < 1):
            raise ValueError("'max_files' must be a positive integer")
        return paths, max_files
    raise ValueError("input must be a JSON array or object")


# ---------------------------------------------------------------------------
# Remote tree fetch (--uri): query the OpenViking server directly, so the
# owner child needs neither the `ov` binary nor a precomputed tree file.
# Standard library only; the per-task CLI config supplies url + credentials.
# ---------------------------------------------------------------------------


def _files_from_uris(uris: list[Any], root: str) -> list[str]:
    """Strip the ``root`` prefix from absolute URIs and drop directory entries."""
    root = root.rstrip("/")
    rel = [
        u[len(root) + 1:]
        for u in uris
        if isinstance(u, str) and u != root and u.startswith(root + "/")
    ]
    dirs = {p for p in rel if any(other.startswith(p + "/") for other in rel)}
    return sorted(p for p in rel if p not in dirs)


def _load_cli_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _ls_page(
    base: str,
    headers: dict[str, str],
    uri: str,
    *,
    recursive: bool,
    offset: int,
    page_limit: int,
    simple: bool,
) -> tuple[list[Any], bool]:
    query = urllib.parse.urlencode(
        {
            "uri": uri,
            "recursive": "true" if recursive else "false",
            "simple": "true" if simple else "false",
            "limit": page_limit,
            "offset": offset,
        }
    )
    request = urllib.request.Request(f"{base}/fs/ls?{query}", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") and payload["status"] != "ok":
        raise RuntimeError(f"server error: {payload.get('error')}")
    result = payload.get("result")
    if not isinstance(result, list):
        raise RuntimeError("unexpected fs/ls response: result is not a list")
    return result, bool(payload.get("has_more"))


def _list_subtree(
    base: str,
    headers: dict[str, str],
    uri: str,
    *,
    page_limit: int,
    max_pages: int,
    seen: set[str],
    sink: list[str],
) -> None:
    """Recursively list one directory via offset paging, deduping into sink.

    Per-subtree recursive listings page correctly; the repository ROOT recursive
    listing is silently truncated by the server around ~2k entries, so callers
    enumerate the root non-recursively and recurse per top-level child.
    """
    offset = 0
    for _ in range(max_pages):
        result, has_more = _ls_page(
            base, headers, uri, recursive=True, offset=offset,
            page_limit=page_limit, simple=True,
        )
        for entry in result:
            if isinstance(entry, str) and entry not in seen:
                seen.add(entry)
                sink.append(entry)
        if not has_more or not result:
            return
        offset += len(result)


def _fetch_tree_uris(
    server_url: str,
    root_uri: str,
    cfg: dict[str, Any],
    *,
    page_limit: int = 2000,
    max_pages: int = 50,
) -> list[Any]:
    """Enumerate ``root_uri`` completely.

    A single recursive listing on the root is server-capped (~2k entries,
    returned as if complete). So: list the root NON-recursively (typed, to know
    isDir), emit root files directly, and page each top-level subtree.
    """
    base = server_url.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    headers = {"Accept": "application/json"}
    if cfg.get("account"):
        headers["X-OpenViking-Account"] = str(cfg["account"])
    if cfg.get("user"):
        headers["X-OpenViking-User"] = str(cfg["user"])
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    seen: set[str] = set()
    collected: list[str] = []
    child_dirs: list[str] = []
    offset = 0
    for _ in range(max_pages):  # root non-recursive, typed (isDir available)
        result, has_more = _ls_page(
            base, headers, root_uri, recursive=False, offset=offset,
            page_limit=page_limit, simple=False,
        )
        for entry in result:
            if not isinstance(entry, dict):
                continue
            uri = entry.get("uri")
            if not isinstance(uri, str) or uri in seen:
                continue
            seen.add(uri)
            if entry.get("isDir"):
                child_dirs.append(uri)
            else:
                collected.append(uri)
        if not has_more or not result:
            break
        offset += len(result)

    for dir_uri in sorted(child_dirs):
        _list_subtree(
            base, headers, dir_uri, page_limit=page_limit,
            max_pages=max_pages, seen=seen, sink=collected,
        )
    return collected


def _fetch_text(
    server_url: str, file_uri: str, cfg: dict[str, Any]
) -> str | None:
    """Read one raw file via GET /api/v1/content/read (model-free HTTP)."""
    base = server_url.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    headers = {"Accept": "application/json"}
    if cfg.get("account"):
        headers["X-OpenViking-Account"] = str(cfg["account"])
    if cfg.get("user"):
        headers["X-OpenViking-User"] = str(cfg["user"])
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    query = urllib.parse.urlencode({"uri": file_uri, "raw": "true"})
    request = urllib.request.Request(
        f"{base}/content/read?{query}", headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if isinstance(payload, dict) and "result" in payload:
        result = payload["result"]
        return result if isinstance(result, str) else body
    return body


# ---------------------------------------------------------------------------
# Shared on-disk cache for the fetched tree.
#
# Parallel page-authoring children have isolated scratch dirs, but exec is not
# an OS sandbox: every child can read/write a common absolute path. Caching the
# tree there makes all children share ONE physical listing instead of each
# hammering fs/ls. A file lock + double-check collapses the first concurrent
# wave to a single fetch; atomic replace prevents partial reads.
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_DIR = os.path.expanduser(
    "~/.openviking/data/bot/compile_source_cache"
)
_CACHE_VERSION = 2
_DEFAULT_TTL_SECONDS = 3600


def _cache_paths(
    server_url: str, root_uri: str, cache_dir: str
) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{server_url.rstrip('/')}|{root_uri}".encode("utf-8")
    ).hexdigest()
    return (
        os.path.join(cache_dir, f"{digest}.json"),
        os.path.join(cache_dir, f"{digest}.lock"),
    )


def _read_cache(cache_file: str, ttl_seconds: int) -> list[Any] | None:
    try:
        with open(cache_file, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if payload.get("version") != _CACHE_VERSION:
        return None
    uris = payload.get("uris")
    if not isinstance(uris, list) or not uris:
        return None
    if ttl_seconds >= 0:
        age = time.time() - float(payload.get("fetched_at", 0))
        if age > ttl_seconds:
            return None
    return uris


def _write_cache_atomic(cache_file: str, server_url: str, root_uri: str, uris: list[Any]) -> None:
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    payload = {
        "version": _CACHE_VERSION,
        "fetched_at": time.time(),
        "server": server_url.rstrip("/"),
        "uri": root_uri,
        "uris": uris,
    }
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(cache_file), prefix=".tree-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp_path, cache_file)  # atomic on POSIX/Windows
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _source_cache_paths(
    server_url: str, file_uri: str, cache_dir: str
) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"src|{server_url.rstrip('/')}|{file_uri}".encode("utf-8")
    ).hexdigest()
    return (
        os.path.join(cache_dir, f"src-{digest}.json"),
        os.path.join(cache_dir, f"src-{digest}.lock"),
    )


def _read_source_cache(cache_file: str, ttl_seconds: int) -> str | None:
    try:
        with open(cache_file, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if payload.get("version") != _CACHE_VERSION:
        return None
    text = payload.get("text")
    if not isinstance(text, str):
        return None
    if ttl_seconds >= 0 and time.time() - float(payload.get("fetched_at", 0)) > ttl_seconds:
        return None
    return text


def _get_source_cached(
    server_url: str,
    file_uri: str,
    cfg: dict[str, Any],
    *,
    cache_dir: str,
    ttl_seconds: int,
    use_cache: bool,
) -> str | None:
    """Read one source file for --print-topics, sharing one fetch across jobs."""
    if not use_cache:
        return _fetch_text(server_url, file_uri, cfg)
    cache_file, lock_file = _source_cache_paths(server_url, file_uri, cache_dir)
    cached = _read_source_cache(cache_file, ttl_seconds)
    if cached is not None:
        return cached
    os.makedirs(cache_dir, exist_ok=True)
    with open(lock_file, "a+b") as lock_handle:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        cached = _read_source_cache(cache_file, ttl_seconds)
        if cached is not None:
            return cached
        text = _fetch_text(server_url, file_uri, cfg)
        if text:
            fd, tmp_path = tempfile.mkstemp(
                dir=cache_dir, prefix=".src-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"version": _CACHE_VERSION,
                         "fetched_at": time.time(), "text": text},
                        handle,
                        ensure_ascii=False,
                    )
                os.replace(tmp_path, cache_file)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        return text


def _get_tree_cached(
    server_url: str,
    root_uri: str,
    cfg: dict[str, Any],
    *,
    cache_dir: str,
    ttl_seconds: int,
    use_cache: bool,
) -> list[Any]:
    """Return the URI listing, fetching once and sharing via a locked cache."""
    if not use_cache:
        return _fetch_tree_uris(server_url, root_uri, cfg)

    cache_file, lock_file = _cache_paths(server_url, root_uri, cache_dir)
    # Fast path: a fresh cache already exists, no lock needed.
    cached = _read_cache(cache_file, ttl_seconds)
    if cached is not None:
        sys.stderr.write(f"tree cache HIT: {root_uri}\n")
        return cached

    os.makedirs(cache_dir, exist_ok=True)
    with open(lock_file, "a+b") as lock_handle:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        # Double-check: another child in the first wave may have finished.
        cached = _read_cache(cache_file, ttl_seconds)
        if cached is not None:
            sys.stderr.write(f"tree cache HIT (after lock): {root_uri}\n")
            return cached
        sys.stderr.write(f"tree cache MISS, fetching: {root_uri}\n")
        uris = _fetch_tree_uris(server_url, root_uri, cfg)
        _write_cache_atomic(cache_file, server_url, root_uri, uris)
        return uris


def _selftest() -> int:
    # Synthetic package: dominant src/ root with two substantial children, so
    # the namespace is descended into core/ and api/.
    synthetic = (
        ["README.md", "pyproject.toml", "LICENSE"]
        + [f"src/core/{n}.py" for n in ("a", "b", "c", "d")]
        + [f"src/api/{n}.py" for n in ("a", "b", "c", "d")]
        + [f"docs/q{i:02d}.md" for i in range(20)]
        + [f"tests/test_lib_{i}.py" for i in range(10)]
    )
    profile = select_sources(synthetic, max_files=64)
    selected = profile["selected_files"]

    assert selected[0]["path"] == "README.md", "README must rank first"
    assert any(f["path"] == "pyproject.toml" for f in selected), "manifest selected"
    assert sum(f["reason"] in _DOC_REASONS for f in selected) <= _MAX_DOC_FILES, (
        "documentation cap enforced"
    )
    assert {s["id"] for s in profile["major_subsystems"]} == {"core", "api"}, (
        profile["major_subsystems"]
    )
    assert not profile["breadth"]["degenerate"]
    tree_files = profile["file_tree"]["files"]
    assert all(not p.startswith("tests/") for p in tree_files), "suite excluded"
    assert "pyproject.toml" not in tree_files, "lockfile/manifest excluded from tree"
    assert not profile["file_tree"]["truncated"]

    # Click-like shape: one flat package under src/ yields no namespace children,
    # hence no derived subsystems; breadth is flagged degenerate, never invented.
    click = ["README.rst", "pyproject.toml"] + [
        f"src/click/{n}.py"
        for n in ("__init__", "core", "parser", "types", "termui", "decorators", "context")
    ]
    click_profile = select_sources(click, max_files=64)
    assert click_profile["major_subsystems"] == [], click_profile["major_subsystems"]
    assert click_profile["breadth"]["subsystem_count"] == 0
    assert click_profile["breadth"]["degenerate"] is True
    assert click_profile["breadth"]["flat_package"] is True
    assert click_profile["breadth"]["flat_package_dir"] == "src/click"

    # Regression: example/demo .py files must never masquerade as subsystems.
    polluted = click + [f"examples/demo{i}.py" for i in range(15)]
    polluted_profile = select_sources(polluted, max_files=64)
    assert polluted_profile["major_subsystems"] == [], polluted_profile[
        "major_subsystems"
    ]
    assert polluted_profile["breadth"]["flat_package"] is True

    # Determinism: same input -> byte-identical output.
    again = select_sources(synthetic, max_files=64)
    assert json.dumps(again, sort_keys=True) == json.dumps(
        profile, sort_keys=True
    ), "selection must be deterministic"

    # Invalid budget.
    try:
        select_sources(["README.md"], max_files=0)
    except ValueError:
        pass
    else:  # pragma: no cover - assertion path
        raise AssertionError("max_files=0 must raise")

    # Remote URI listing -> repo-relative file paths (directories dropped).
    root = "viking://resources/pallets/click"
    uris = [
        root,
        root + "/docs",
        root + "/docs/advanced.md",
        root + "/src/click",
        root + "/src/click/core.py",
        root + "/README.md",
        "viking://resources/other/x.py",  # outside root: ignored
    ]
    assert _files_from_uris(uris, root) == [
        "README.md",
        "docs/advanced.md",
        "src/click/core.py",
    ], "URI listing must strip prefix and drop directories"

    # Shared tree cache: atomic write, read-back, TTL expiry, key isolation.
    import tempfile as _tmp

    with _tmp.TemporaryDirectory() as cache_dir:
        srv, other = "http://127.0.0.1:1933", "http://example:9999"
        cf, _lf = _cache_paths(srv, root, cache_dir)
        _write_cache_atomic(cf, srv, root, ["a.py", "b.py"])
        assert _read_cache(cf, ttl_seconds=3600) == ["a.py", "b.py"], "cache read-back"
        assert _read_cache(cf, ttl_seconds=0) is None, "expired TTL must miss"
        cf_other, _ = _cache_paths(other, root, cache_dir)
        assert cf_other != cf, "different servers must not share a cache key"

    # Canonical topic table for a flat package: inheritance roots fold
    # descendants; non-core modules fold all their classes; private modules
    # attach via the import graph. Pure AST, no network.
    flat_paths = [
        f"src/pkg/{n}.py"
        for n in (
            "core", "types", "termui", "_termui_impl", "_winconsole",
            "exceptions", "_compat",
        )
    ]
    sources = {
        "src/pkg/core.py": (
            "class BaseCommand:\n"
            "    def run(self):\n        return 1\n"
            "class Command(BaseCommand):\n    def main(self):\n        pass\n"
            "class MultiCommand(Command):\n    def add(self):\n        pass\n"
            "class Group(MultiCommand):\n    def cmd(self):\n        pass\n"
            "class Context:\n    def obj(self):\n        pass\n"
            "class Parameter:\n    def p(self):\n        pass\n"
            "class Option(Parameter):\n    def o(self):\n        pass\n"
            "class Argument(Parameter):\n    def a(self):\n        pass\n"
            "class Marker:\n    pass\n"
            "def one():\n    pass\ndef two():\n    pass\ndef three():\n    pass\n"
        ),
        "src/pkg/types.py": (
            "class ParamType:\n    def c(self):\n        pass\n"
            "class Choice(ParamType):\n    def x(self):\n        pass\n"
            "class Path(ParamType):\n    def y(self):\n        pass\n"
        ),
        "src/pkg/termui.py": (
            "from ._termui_impl import ProgressBar\n"
            "def echo():\n    return ProgressBar\n"
        ),
        "src/pkg/_termui_impl.py": (
            "from ._winconsole import X\n"
            "class ProgressBar:\n    def bar(self):\n        return X\n"
        ),
        "src/pkg/_winconsole.py": "X = 1\n",
        "src/pkg/exceptions.py": (
            "class ClickException(Exception):\n    def e(self):\n        pass\n"
            "class UsageError(ClickException):\n    def u(self):\n        pass\n"
        ),
        "src/pkg/_compat.py": "X = 1\n",
    }
    flat_profile = select_sources(
        ["README.md"] + flat_paths + ["tests/test_x.py"], max_files=64
    )
    assert flat_profile["breadth"]["flat_package"] is True
    assert flat_profile["breadth"]["flat_package_dir"] == "src/pkg"
    topic_doc = derive_canonical_topics(
        flat_paths,
        lambda rel: sources.get(rel),
        degenerate=True,
    )
    table = {t["path"]: t for t in topic_doc["canonical_topics"]}
    # one command entity folds the whole inheritance chain
    assert "entity/command.md" in table, table.keys()
    assert set(table["entity/command.md"]["symbols"]) >= {
        "BaseCommand", "Command", "MultiCommand", "Group"
    }
    assert set(table["entity/parameter.md"]["symbols"]) == {
        "Parameter", "Argument", "Option"
    }
    assert "entity/context.md" in table
    # non-core module: all type variants fold into ONE concept page
    types_topic = table["concept/types.md"]
    assert set(types_topic["symbols"]) == {"Choice", "Path", "ParamType"}
    # exception subclasses do not get their own pages
    assert "concept/exceptions.md" in table
    assert "entity/usage-error.md" not in table
    # private implementation folds into its importer (also transitively);
    # orphan private -> compat
    assert "_termui_impl.py" in table["concept/termui.md"]["modules"]
    assert "_winconsole.py" in table["concept/termui.md"]["modules"]
    assert table["concept/platform-compat.md"]["modules"] == ["_compat.py"]
    # core leftovers become one protected mechanics deep chapter; zero-method
    # marker roots fold there rather than getting their own entity page
    mechanics = table["concept/core-mechanics.md"]
    assert mechanics.get("protected_deep_chapter") is True
    assert "Marker" in mechanics["symbols"]
    assert "entity/marker.md" not in table
    # unique paths only; range sane
    paths_out = [t["path"] for t in topic_doc["canonical_topics"]]
    assert len(paths_out) == len(set(paths_out))
    inv = topic_doc["topic_inventory"]
    assert inv["core_topics"] == len(paths_out)
    assert inv["class_inheritance_folded"] is True

    # Non-degenerate repositories keep their subsystem-derived topics.
    sub_doc = derive_canonical_topics(
        synthetic, None, degenerate=False,
        subsystem_topics=select_sources(synthetic)["major_subsystems"],
    )
    assert {t["id"] for t in sub_doc["canonical_topics"]} == {"core", "api"}

    print("selftest ok: docs cap, subsystem derivation, tree filter, determinism")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic README-first source selection (pure, offline)."
    )
    parser.add_argument(
        "--input",
        default="-",
        help="JSON input file (repo-relative paths); '-' for stdin (default).",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Preload budget; overrides input max_files (default 64).",
    )
    parser.add_argument(
        "--uri",
        default=None,
        help=(
            "Input repository Viking URI (e.g. viking://resources/pallets/click). "
            "When set, the file tree is fetched from the OpenViking server directly; "
            "no --input file or `ov` binary is required."
        ),
    )
    parser.add_argument(
        "--server",
        default=None,
        help="Server base URL; defaults to the CLI config 'url' or $OPENVIKING_URL.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("OPENVIKING_CLI_CONFIG_FILE"),
        help="Per-task CLI config JSON (defaults to $OPENVIKING_CLI_CONFIG_FILE).",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("OV_COMPILE_CACHE_DIR", _DEFAULT_CACHE_DIR),
        help=(
            "Shared directory for the fetched-tree cache (default "
            "~/.openviking/data/bot/compile_source_cache or $OV_COMPILE_CACHE_DIR)."
        ),
    )
    parser.add_argument(
        "--cache-ttl",
        type=int,
        default=_DEFAULT_TTL_SECONDS,
        help="Cache freshness in seconds (default 3600; -1 = never expire).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the shared tree cache and fetch from the server directly.",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON output.")
    parser.add_argument(
        "--print-topics",
        action="store_true",
        help=(
            "Also emit a deterministic canonical_topics table (topic -> unique "
            "page path) for page fan-out control. Needs source text, supplied by "
            "--uri (server content API) or --src-dir (local checkout)."
        ),
    )
    parser.add_argument(
        "--src-dir",
        default=None,
        help=(
            "Local repository checkout root used with --print-topics; source "
            "files are read as <src-dir>/<repo-relative-path> (offline, no server)."
        ),
    )
    parser.add_argument(
        "--selftest", action="store_true", help="Run built-in checks and exit."
    )
    args = parser.parse_args(argv)

    if args.selftest:
        try:
            return _selftest()
        except AssertionError as exc:
            print(f"selftest failed: {exc}", file=sys.stderr)
            return 1

    input_max: int | None = None
    cfg: dict[str, Any] = {}
    server_url: str | None = None
    if args.uri:
        try:
            cfg = _load_cli_config(args.config)
            server_url = (
                args.server
                or cfg.get("url")
                or os.environ.get("OPENVIKING_URL")
                or "http://127.0.0.1:1933"
            )
            uris = _get_tree_cached(
                str(server_url),
                args.uri,
                cfg,
                cache_dir=args.cache_dir,
                ttl_seconds=args.cache_ttl,
                use_cache=not args.no_cache,
            )
            paths = _files_from_uris(uris, args.uri)
            if not paths:
                raise RuntimeError(
                    f"no files listed under {args.uri} (wrong URI or empty repository)"
                )
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            print(f"tree fetch failed: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            raw = (
                sys.stdin.read()
                if args.input == "-"
                else open(args.input, encoding="utf-8").read()
            )
            paths, input_max = _load_input(raw)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"invalid input: {exc}", file=sys.stderr)
            return 2

    max_files = args.max_files if args.max_files is not None else (
        input_max or DEFAULT_MAX_FILES
    )
    try:
        profile = select_sources(paths, max_files=max_files)
    except ValueError as exc:
        print(f"selection failed: {exc}", file=sys.stderr)
        return 2

    if args.print_topics:
        read_text = None
        if args.src_dir:
            def read_text(rel: str) -> str | None:  # type: ignore[misc]
                try:
                    with open(
                        os.path.join(args.src_dir, rel), encoding="utf-8"
                    ) as handle:
                        return handle.read()
                except OSError:
                    return None
        elif args.uri and server_url:
            root_uri = args.uri.rstrip("/")

            def read_text(rel: str) -> str | None:  # type: ignore[misc]
                try:
                    return _get_source_cached(
                        str(server_url),
                        f"{root_uri}/{rel}",
                        cfg,
                        cache_dir=args.cache_dir,
                        ttl_seconds=args.cache_ttl,
                        use_cache=not args.no_cache,
                    )
                except (OSError, ValueError, urllib.error.URLError):
                    return None
        is_flat = bool(
            profile["breadth"]["degenerate"]
            or profile["breadth"].get("flat_package")
        )
        # Canonical topics only replace the page contract for flat/degenerate
        # packages; multi-subsystem repositories keep their proven
        # covers/subsystem-map flow, so emit nothing extra for them.
        if is_flat:
            try:
                profile.update(
                    derive_canonical_topics(
                        paths,
                        read_text,
                        degenerate=True,
                    )
                )
            except (OSError, ValueError) as exc:
                print(f"topic derivation failed: {exc}", file=sys.stderr)
                return 2

    json.dump(profile, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
