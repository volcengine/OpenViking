# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Aider RepoMap-style skeleton extraction using vendored tags queries."""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_QUERY_DIR = Path(__file__).with_name("queries") / "tree-sitter-language-pack"
_TREE_CONTEXT_OMISSION = "⋮"
_TREE_CONTEXT_CODE_PREFIX = "│"

_LANG_ALIASES = {
    "common_lisp": "commonlisp",
    "emacs_lisp": "elisp",
    "objective_caml": "ocaml",
}


def _query_language_name(lang: str) -> str:
    return _LANG_ALIASES.get(lang, lang)


@lru_cache(maxsize=None)
def _load_tag_query(lang: str) -> Optional[str]:
    """Load a maintained tree-sitter tags query for a grep-ast language."""

    query_file = f"{_query_language_name(lang)}-tags.scm"
    query_path = _QUERY_DIR / query_file
    try:
        query_scm = query_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("No maintained tags query for language '%s'", lang)
        return None
    except OSError as exc:
        logger.warning(
            "Failed to load tags query '%s' for language '%s': %s",
            query_file,
            lang,
            exc,
        )
        return None
    return query_scm.strip() or None


def has_tag_query(file_name: str) -> bool:
    """Return whether a maintained tags query exists for this file."""

    try:
        from grep_ast import filename_to_lang

        lang = filename_to_lang(Path(file_name).name or "source.txt")
    except Exception as exc:
        logger.debug("Unable to detect tags-query language for '%s': %s", file_name, exc)
        return False
    return bool(lang and _load_tag_query(lang))


def _extract_with_grep_ast(
    file_name: str,
    rel_name: str,
    content: str,
    verbose: bool,
) -> Optional[str]:
    try:
        from grep_ast import TreeContext
    except Exception as exc:
        logger.warning("grep-ast RepoMap extractor unavailable: %s", exc)
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
        rendered = _clean_repromap_rendered(context.format()).strip()
        if not rendered:
            return None

        mode = "verbose" if verbose else "compact"
        return f"# {file_name} [aider-repomap-lite, {mode}]\n\n{rendered}"
    except Exception as exc:
        logger.warning("grep-ast RepoMap extraction failed for '%s': %s", file_name, exc)
        return None


def _clean_repromap_rendered(rendered: str) -> str:
    """Remove TreeContext visual gutter before skeleton usefulness checks."""

    lines: list[str] = []
    for line in rendered.splitlines():
        if line.strip() == _TREE_CONTEXT_OMISSION:
            continue
        if line.startswith(_TREE_CONTEXT_CODE_PREFIX):
            line = line[len(_TREE_CONTEXT_CODE_PREFIX) :]
        lines.append(line.rstrip())
    return "\n".join(lines)


def _query_captures(rel_name: str, content: str):
    from grep_ast import filename_to_lang
    from grep_ast.tsl import get_language, get_parser

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
