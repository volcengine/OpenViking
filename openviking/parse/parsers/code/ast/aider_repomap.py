# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Aider RepoMap-style skeleton extraction using vendored tags queries."""

import logging
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
    """Load a maintained tree-sitter tags query for a grep-ast language."""

    query_path = _QUERY_DIR / f"{_query_language_name(lang)}-tags.scm"
    try:
        query_scm = query_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("No maintained tags query for language '%s'", lang)
        return None
    except OSError as exc:
        logger.warning("Failed to load tags query '%s': %s", query_path, exc)
        return None
    return query_scm.strip() or None


def _normalise_repromap_name(file_name: str) -> str:
    """Recover the original suffix from Viking's ``foo.py/foo.md`` layout."""

    path = Path(file_name)
    if path.suffix.lower() == ".md" and path.parent.name:
        parent_suffix = Path(path.parent.name).suffix
        if parent_suffix:
            return path.parent.name
    return path.name or "source.txt"


def has_tag_query(file_name: str) -> bool:
    """Return whether a maintained tags query exists for this file."""

    try:
        from grep_ast import filename_to_lang

        lang = filename_to_lang(_normalise_repromap_name(file_name))
    except Exception as exc:
        logger.debug("Unable to detect tags-query language for '%s': %s", file_name, exc)
        return False
    return bool(lang and _load_tag_query(lang))


def extract_repromap_skeleton(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> Optional[str]:
    """Return a RepoMap-style skeleton for one source file."""

    if not content:
        return None
    rel_name = _normalise_repromap_name(file_name)
    return _extract_with_grep_ast(file_name, rel_name, content, verbose)


def extract_query_skeleton(
    file_name: str,
    content: str,
    verbose: bool = False,
) -> Optional[str]:
    """Return captured definition symbols without source context rendering."""

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
        lines.extend(f"- L{line_no}: {kind} {name}" for line_no, kind, name in symbols)
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("RepoMap query extraction failed for '%s': %s", file_name, exc)
        return None


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
        lang, captures = _query_captures(rel_name, content)
        if lang == "c":
            rendered = _render_c_signature_skeleton(captures, content)
            if rendered:
                mode = "verbose" if verbose else "compact"
                return f"# {file_name} [aider-repomap-lite, {mode}]\n\n{rendered}"

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


def _render_c_signature_skeleton(captures, content: str) -> Optional[str]:
    """Render C definitions as declarations instead of function-body context."""

    source = content.encode("utf-8")
    signatures: set[tuple[int, str]] = set()

    def add_signature(tag: str, node) -> None:
        prefix = "name.definition."
        if not tag.startswith(prefix):
            return
        kind = tag[len(prefix) :]
        line_no = node.start_point[0] + 1
        name = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()
        signature = _c_definition_signature(node, source)
        if not signature and name:
            signature = f"{kind} {name}"
        if signature:
            signatures.add((line_no, signature))

    if isinstance(captures, dict):
        for tag, nodes in captures.items():
            for node in nodes:
                add_signature(str(tag), node)
    else:
        for node, tag in captures:
            add_signature(str(tag), node)

    if not signatures:
        return None
    return "\n".join(signature for _, signature in sorted(signatures))


def _c_definition_signature(node, source: bytes) -> str:
    ancestor = node
    while ancestor is not None:
        node_type = getattr(ancestor, "type", "")
        if node_type == "function_definition":
            return _c_function_signature(ancestor, source)
        if node_type in {
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
            "type_definition",
        }:
            return _c_type_signature(ancestor, node, source)
        ancestor = getattr(ancestor, "parent", None)
    return ""


def _c_function_signature(node, source: bytes) -> str:
    body = _first_child_of_type(node, "compound_statement")
    end_byte = body.start_byte if body is not None else node.end_byte
    signature = source[node.start_byte : end_byte].decode("utf-8", errors="replace")
    return _normalise_c_signature(signature)


def _c_type_signature(node, name_node, source: bytes) -> str:
    name = source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace").strip()
    if not name:
        return ""
    if node.type == "struct_specifier":
        return f"struct {name}"
    if node.type == "union_specifier":
        return f"union {name}"
    if node.type == "enum_specifier":
        return f"enum {name}"
    if node.type == "type_definition":
        return f"typedef {name}"
    return name


def _first_child_of_type(node, node_type: str):
    for child in getattr(node, "children", ()):
        if getattr(child, "type", "") == node_type:
            return child
    return None


def _normalise_c_signature(signature: str) -> str:
    signature = signature.strip()
    if signature.endswith("{"):
        signature = signature[:-1].rstrip()
    return " ".join(signature.split())


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


def _name_definition_symbols(captures, content: str) -> list[tuple[int, str, str]]:
    source = content.encode("utf-8")
    symbols: set[tuple[int, str, str]] = set()

    def add_symbol(tag: str, node) -> None:
        prefix = "name.definition."
        if not tag.startswith(prefix):
            return
        kind = tag[len(prefix) :]
        name = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()
        if name:
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
