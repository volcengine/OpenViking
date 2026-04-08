# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lua AST extractor using tree-sitter-lua."""

from typing import Dict, List, Optional

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _comment_text(node, content_bytes: bytes) -> str:
    """Extract readable text from a comment node (-- or --[[ ... ]])."""
    raw = _node_text(node, content_bytes).strip()
    if raw.startswith("--[["):
        inner = raw[4:]
        if inner.endswith("]]"):
            inner = inner[:-2]
        lines = [line.strip() for line in inner.split("\n")]
        return "\n".join(line for line in lines if line).strip()
    if raw.startswith("--"):
        return raw[2:].strip()
    return raw


def _preceding_doc(siblings: list, idx: int, content_bytes: bytes) -> str:
    """Collect consecutive comment nodes immediately before siblings[idx]."""
    lines: List[str] = []
    i = idx - 1
    while i >= 0 and siblings[i].type == "comment":
        lines.insert(0, _comment_text(siblings[i], content_bytes))
        i -= 1
    return "\n".join(lines).strip()


def _extract_params(params_node, content_bytes: bytes) -> str:
    if params_node is None:
        return ""
    raw = _node_text(params_node, content_bytes).strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    return raw.strip()


def _find_require_in_call(call_node, content_bytes: bytes) -> Optional[str]:
    """Return module name if call_node is require('mod') / require 'mod', else None."""
    children = list(call_node.children)
    if not children:
        return None
    if children[0].type != "identifier":
        return None
    if _node_text(children[0], content_bytes) != "require":
        return None
    for child in children[1:]:
        if child.type == "arguments":
            for arg in child.children:
                if arg.type == "string":
                    return _node_text(arg, content_bytes).strip().strip("'\"")
        elif child.type == "string":
            return _node_text(child, content_bytes).strip().strip("'\"")
    return None


def _collect_requires(node, content_bytes: bytes) -> List[str]:
    """Find require() calls in node, not recursing into function bodies."""
    if node.type == "function_call":
        mod = _find_require_in_call(node, content_bytes)
        if mod:
            return [mod]
    results: List[str] = []
    for child in node.children:
        if child.type in ("block", "function_declaration"):
            continue
        results.extend(_collect_requires(child, content_bytes))
    return results


class LuaExtractor(LanguageExtractor):
    def __init__(self):
        import tree_sitter_lua as tslua
        from tree_sitter import Language, Parser

        self._language = Language(tslua.language())
        self._parser = Parser(self._language)

    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        content_bytes = content.encode("utf-8")
        tree = self._parser.parse(content_bytes)
        root = tree.root_node

        imports: List[str] = []
        _seen_imports: set = set()
        _classes: Dict[str, ClassSkeleton] = {}
        functions: List[FunctionSig] = []

        siblings = list(root.children)
        for idx, child in enumerate(siblings):
            # --- require imports (from variable_declaration or bare function_call) ---
            if child.type in ("variable_declaration", "function_call"):
                for mod in _collect_requires(child, content_bytes):
                    if mod not in _seen_imports:
                        imports.append(mod)
                        _seen_imports.add(mod)

            # --- function declarations (including local function ...) ---
            if child.type == "function_declaration":
                doc = _preceding_doc(siblings, idx, content_bytes)
                name_node = None
                params_node = None
                for c in child.children:
                    if c.type in (
                        "identifier",
                        "dot_index_expression",
                        "method_index_expression",
                    ):
                        name_node = c
                    elif c.type == "parameters":
                        params_node = c

                if name_node is None:
                    continue

                params = _extract_params(params_node, content_bytes)

                if name_node.type == "identifier":
                    name = _node_text(name_node, content_bytes)
                    functions.append(
                        FunctionSig(name=name, params=params, return_type="", docstring=doc)
                    )
                elif name_node.type in ("dot_index_expression", "method_index_expression"):
                    # M.foo or M:foo — map to class method
                    id_children = [c for c in name_node.children if c.type == "identifier"]
                    if len(id_children) >= 2:
                        table_name = _node_text(id_children[0], content_bytes)
                        method_name = _node_text(id_children[1], content_bytes)
                        fn = FunctionSig(
                            name=method_name, params=params, return_type="", docstring=doc
                        )
                        if table_name not in _classes:
                            _classes[table_name] = ClassSkeleton(
                                name=table_name, bases=[], docstring="", methods=[]
                            )
                        _classes[table_name].methods.append(fn)

        return CodeSkeleton(
            file_name=file_name,
            language="Lua",
            module_doc="",
            imports=imports,
            classes=list(_classes.values()),
            functions=functions,
        )
