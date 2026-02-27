# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Java AST extractor using tree-sitter-java."""

from typing import List

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _parse_block_comment(raw: str) -> str:
    """Strip /** ... */ markers and leading * from each line."""
    raw = raw.strip()
    if raw.startswith("/**"):
        raw = raw[3:]
    elif raw.startswith("/*"):
        raw = raw[2:]
    if raw.endswith("*/"):
        raw = raw[:-2]
    lines = [l.strip().lstrip("*").strip() for l in raw.split("\n")]
    return "\n".join(l for l in lines if l).strip()


def _preceding_doc(siblings: list, idx: int, content_bytes: bytes) -> str:
    """Return Javadoc block comment immediately before siblings[idx], or ''."""
    if idx == 0:
        return ""
    prev = siblings[idx - 1]
    if prev.type == "block_comment":
        return _parse_block_comment(_node_text(prev, content_bytes))
    return ""


def _extract_method(node, content_bytes: bytes, docstring: str = "") -> FunctionSig:
    name = ""
    params = ""
    return_type = ""

    for child in node.children:
        if child.type == "identifier" and not name:
            if return_type:
                name = _node_text(child, content_bytes)
        elif child.type in ("type_identifier", "void_type", "integral_type",
                            "floating_point_type", "boolean_type", "array_type",
                            "generic_type"):
            if not return_type:
                return_type = _node_text(child, content_bytes)
        elif child.type == "formal_parameters":
            raw = _node_text(child, content_bytes).strip()
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1]
            params = raw.strip()

    return FunctionSig(name=name, params=params, return_type=return_type, docstring=docstring)


def _extract_class(node, content_bytes: bytes, docstring: str = "") -> ClassSkeleton:
    name = ""
    bases: List[str] = []
    body_node = None

    for child in node.children:
        if child.type in ("type_identifier", "identifier") and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "superclass":
            for sub in child.children:
                if sub.type == "type_identifier":
                    bases.append(_node_text(sub, content_bytes))
        elif child.type == "super_interfaces":
            for sub in child.children:
                if sub.type == "type_list":
                    for s2 in sub.children:
                        if s2.type == "type_identifier":
                            bases.append(_node_text(s2, content_bytes))
        elif child.type == "class_body":
            body_node = child

    methods: List[FunctionSig] = []
    if body_node:
        siblings = list(body_node.children)
        for idx, child in enumerate(siblings):
            if child.type in ("method_declaration", "constructor_declaration"):
                doc = _preceding_doc(siblings, idx, content_bytes)
                methods.append(_extract_method(child, content_bytes, docstring=doc))

    return ClassSkeleton(name=name, bases=bases, docstring=docstring, methods=methods)


class JavaExtractor(LanguageExtractor):
    def __init__(self):
        import tree_sitter_java as tsjava
        from tree_sitter import Language, Parser

        self._language = Language(tsjava.language())
        self._parser = Parser(self._language)

    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        content_bytes = content.encode("utf-8")
        tree = self._parser.parse(content_bytes)
        root = tree.root_node

        imports: List[str] = []
        classes: List[ClassSkeleton] = []

        siblings = list(root.children)
        for idx, child in enumerate(siblings):
            if child.type == "import_declaration":
                for sub in child.children:
                    if sub.type == "scoped_identifier":
                        imports.append(_node_text(sub, content_bytes))
                    elif sub.type == "identifier":
                        imports.append(_node_text(sub, content_bytes))
            elif child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                doc = _preceding_doc(siblings, idx, content_bytes)
                classes.append(_extract_class(child, content_bytes, docstring=doc))

        return CodeSkeleton(
            file_name=file_name,
            language="Java",
            module_doc="",
            imports=imports,
            classes=classes,
            functions=[],
        )
