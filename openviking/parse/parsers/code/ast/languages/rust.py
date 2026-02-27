# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Rust AST extractor using tree-sitter-rust."""

from typing import List

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _preceding_doc(siblings: list, idx: int, content_bytes: bytes) -> str:
    """Collect consecutive /// doc comment lines before siblings[idx]."""
    lines = []
    i = idx - 1
    while i >= 0 and siblings[i].type == "line_comment":
        node = siblings[i]
        # Only /// doc comments have a doc_comment child
        doc_child = next((c for c in node.children if c.type == "doc_comment"), None)
        if doc_child is None:
            break
        lines.insert(0, _node_text(doc_child, content_bytes).strip())
        i -= 1
    return "\n".join(lines).strip()


def _extract_function(node, content_bytes: bytes, docstring: str = "") -> FunctionSig:
    name = ""
    params = ""
    return_type = ""

    for child in node.children:
        if child.type == "identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "parameters":
            raw = _node_text(child, content_bytes).strip()
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1]
            params = raw.strip()
        elif child.type == "type_identifier":
            return_type = _node_text(child, content_bytes)
        elif child.type == "scoped_type_identifier":
            return_type = _node_text(child, content_bytes)
        elif child.type == "generic_type":
            return_type = _node_text(child, content_bytes)

    return FunctionSig(name=name, params=params, return_type=return_type, docstring=docstring)


def _extract_struct_or_trait(node, content_bytes: bytes, docstring: str = "") -> ClassSkeleton:
    name = ""
    bases: List[str] = []

    for child in node.children:
        if child.type in ("type_identifier", "identifier") and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "trait_bounds":
            for sub in child.children:
                if sub.type == "type_identifier":
                    bases.append(_node_text(sub, content_bytes))

    return ClassSkeleton(name=name, bases=bases, docstring=docstring, methods=[])


def _extract_impl(node, content_bytes: bytes) -> ClassSkeleton:
    """impl Foo { ... } → treat as class with methods."""
    name = ""
    methods: List[FunctionSig] = []

    for child in node.children:
        if child.type == "type_identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "declaration_list":
            siblings = list(child.children)
            for idx, sub in enumerate(siblings):
                if sub.type == "function_item":
                    doc = _preceding_doc(siblings, idx, content_bytes)
                    methods.append(_extract_function(sub, content_bytes, docstring=doc))

    return ClassSkeleton(name=f"impl {name}", bases=[], docstring="", methods=methods)


class RustExtractor(LanguageExtractor):
    def __init__(self):
        import tree_sitter_rust as tsrust
        from tree_sitter import Language, Parser

        self._language = Language(tsrust.language())
        self._parser = Parser(self._language)

    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        content_bytes = content.encode("utf-8")
        tree = self._parser.parse(content_bytes)
        root = tree.root_node

        imports: List[str] = []
        classes: List[ClassSkeleton] = []
        functions: List[FunctionSig] = []

        siblings = list(root.children)
        for idx, child in enumerate(siblings):
            if child.type == "use_declaration":
                imports.append(_node_text(child, content_bytes).strip().rstrip(";").replace("use ", ""))
            elif child.type in ("struct_item", "trait_item", "enum_item"):
                doc = _preceding_doc(siblings, idx, content_bytes)
                classes.append(_extract_struct_or_trait(child, content_bytes, docstring=doc))
            elif child.type == "impl_item":
                classes.append(_extract_impl(child, content_bytes))
            elif child.type == "function_item":
                doc = _preceding_doc(siblings, idx, content_bytes)
                functions.append(_extract_function(child, content_bytes, docstring=doc))

        return CodeSkeleton(
            file_name=file_name,
            language="Rust",
            module_doc="",
            imports=imports,
            classes=classes,
            functions=functions,
        )
