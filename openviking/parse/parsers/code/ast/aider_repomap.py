# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Aider RepoMap backed text skeleton extraction.

This module intentionally returns only formatted text for semantic indexing.
The structured CodeSkeleton extractor remains the source of truth for code tools
that need symbol hierarchy and source spans.
"""

import logging
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_QUERY_DIR = Path(__file__).with_name("queries") / "tree-sitter-language-pack"

_LANG_ALIASES = {
    "c_sharp": "csharp",
    "common_lisp": "commonlisp",
    "emacs_lisp": "elisp",
    "js": "javascript",
    "objective_caml": "ocaml",
    "shell": "bash",
    "sh": "bash",
    "ts": "typescript",
    "tsx": "typescript",
}


def _query_language_name(lang: str) -> str:
    return _LANG_ALIASES.get(lang, lang)


@lru_cache(maxsize=None)
def _load_tag_query(lang: str) -> Optional[str]:
    """Load vendored Aider tree-sitter tag queries for a grep-ast language."""

    query_lang = _query_language_name(lang)
    query_path = _QUERY_DIR / f"{query_lang}-tags.scm"
    try:
        query_scm = query_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("No Aider RepoMap tag query for language '%s'", lang)
        return None
    except OSError as exc:
        logger.warning("Failed to load Aider RepoMap tag query '%s': %s", query_path, exc)
        return None

    return query_scm.strip() or None


class _RepoMapIO:
    """Minimal IO shim required by aider.repomap.RepoMap."""

    def read_text(self, fname: str) -> str:
        try:
            return Path(fname).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def tool_warning(self, message: str) -> None:
        logger.warning("Aider RepoMap: %s", message)

    def tool_error(self, message: str) -> None:
        logger.error("Aider RepoMap: %s", message)

    def tool_output(self, message: str) -> None:
        logger.debug("Aider RepoMap: %s", message)


def _normalise_repromap_name(file_name: str) -> str:
    """Recover the original code suffix for Viking resource body files.

    Uploaded code can be stored as ``foo.py/foo.md``. The OV AST extractor
    treats that as Python by looking at the parent directory suffix; do the same
    here so Aider does not parse it as Markdown.
    """

    path = Path(file_name)
    if path.suffix.lower() == ".md" and path.parent.name:
        parent_suffix = Path(path.parent.name).suffix
        if parent_suffix:
            return path.parent.name
    return path.name or "source.txt"


def extract_repromap_skeleton(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> Optional[str]:
    """Return an Aider RepoMap style skeleton for one source file.

    ``verbose`` is accepted for API parity with the existing extractor. Aider's
    repo map is line-context based, so both compact and verbose modes share the
    same extraction path for this experimental provider.
    """

    if not content:
        return None

    rel_name = _normalise_repromap_name(file_name)

    try:
        return _extract_with_aider_package(file_name, rel_name, content, verbose)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("External Aider RepoMap extraction failed for '%s': %s", file_name, exc)

    return _extract_with_grep_ast(file_name, rel_name, content, verbose)


def extract_query_skeleton(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> Optional[str]:
    """Return a pure tags-query skeleton without RepoMap context rendering.

    This provider uses the same vendored ``*-tags.scm`` queries as RepoMap, but
    stores only the captured definition symbols.  It is intentionally more
    compact than ``extract_repromap_skeleton`` and avoids pulling surrounding
    source lines into the embedding summary.
    """

    if not content:
        return None

    rel_name = _normalise_repromap_name(file_name)
    try:
        lang, captures = _query_captures(rel_name, content)
        symbols = _name_definition_symbols(captures, content)
        if not symbols:
            return None

        mode = "verbose" if verbose else "compact"
        lines = [f"# {file_name} [repomap-query, {mode}]", "", f"language: {lang}", ""]
        for line_no, kind, name in symbols:
            lines.append(f"- L{line_no}: {kind} {name}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("RepoMap query extraction failed for '%s': %s", file_name, exc)
        return None


def _extract_with_aider_package(
    file_name: str,
    rel_name: str,
    content: str,
    verbose: bool,
) -> Optional[str]:
    from aider.repomap import RepoMap

    with tempfile.TemporaryDirectory(prefix="ov-aider-repomap-") as tmpdir:
        root = Path(tmpdir)
        abs_path = root / rel_name
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8", errors="replace")

        repo_map = RepoMap(root=tmpdir, io=_RepoMapIO(), map_tokens=0)
        tags = repo_map.get_tags(str(abs_path), str(abs_path.relative_to(root)))
        def_lines = sorted({tag.line for tag in tags if getattr(tag, "kind", None) == "def"})
        if not def_lines:
            return None

        tree = repo_map.render_tree(str(abs_path), rel_name, def_lines).strip()
        if not tree:
            return None

        mode = "verbose" if verbose else "compact"
        return f"# {file_name} [aider-repomap, {mode}]\n\n{tree}"


def _extract_with_grep_ast(
    file_name: str,
    rel_name: str,
    content: str,
    verbose: bool,
) -> Optional[str]:
    try:
        from grep_ast import TreeContext
    except Exception as exc:
        logger.warning("grep-ast RepoMap extractor unavailable, falling back to LLM: %s", exc)
        return None

    try:
        _, captures = _query_captures(rel_name, content)
        def_lines = _definition_lines(captures)
        if not def_lines:
            return None

        context = TreeContext(
            rel_name,
            content if content.endswith("\n") else content + "\n",
            color=False,
            line_number=False,
            child_context=False,
            last_line=False,
            margin=0,
            mark_lois=False,
            loi_pad=0,
            show_top_of_file_parent_scope=False,
        )
        context.add_lines_of_interest(def_lines)
        context.add_context()
        rendered = context.format().strip()
        if not rendered:
            return None

        mode = "verbose" if verbose else "compact"
        return f"# {file_name} [aider-repomap-lite, {mode}]\n\n{rendered}"
    except Exception as exc:
        logger.warning("grep-ast RepoMap extraction failed for '%s': %s", file_name, exc)
        return None


def _query_captures(rel_name: str, content: str):
    try:
        from grep_ast import filename_to_lang
        from grep_ast.tsl import get_language, get_parser
    except Exception as exc:
        logger.warning("grep-ast query extractor unavailable, falling back to LLM: %s", exc)
        raise

    lang = filename_to_lang(rel_name)
    if not lang:
        raise ValueError(f"unsupported file language: {rel_name}")
    query_scm = _load_tag_query(lang)
    if not query_scm:
        raise ValueError(f"missing tags query for language: {lang}")

    query_lang = _query_language_name(lang)
    parser = get_parser(query_lang)
    language = get_language(query_lang)
    tree = parser.parse(content.encode("utf-8"))
    query = language.query(query_scm)
    if hasattr(query, "captures"):
        captures = query.captures(tree.root_node)
    else:
        from tree_sitter import QueryCursor

        captures = QueryCursor(query).captures(tree.root_node)
    return lang, captures


def _name_definition_symbols(captures, content: str) -> list[tuple[int, str, str]]:
    source = content.encode("utf-8")
    symbols: set[tuple[int, str, str]] = set()

    def add_symbol(tag: str, node) -> None:
        prefix = "name.definition."
        if not tag.startswith(prefix):
            return
        kind = tag[len(prefix) :]
        name = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()
        if not name:
            return
        symbols.add((node.start_point[0] + 1, kind, " ".join(name.split())))

    if isinstance(captures, dict):
        for tag, nodes in captures.items():
            for node in nodes:
                add_symbol(str(tag), node)
    else:
        for node, tag in captures:
            add_symbol(str(tag), node)

    return sorted(symbols)


def _definition_lines(captures) -> list[int]:
    lines: set[int] = set()
    if isinstance(captures, dict):
        for tag, nodes in captures.items():
            if str(tag).startswith("name.definition."):
                lines.update(node.start_point[0] for node in nodes)
        return sorted(lines)

    for node, tag in captures:
        if str(tag).startswith("name.definition."):
            lines.add(node.start_point[0])
    return sorted(lines)
