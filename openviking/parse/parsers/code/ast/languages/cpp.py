# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""C/C++ AST extractor using tree-sitter-cpp."""

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
    """Return Doxygen block comment immediately before siblings[idx], or ''."""
    if idx == 0:
        return ""
    prev = siblings[idx - 1]
    if prev.type == "comment":
        return _parse_block_comment(_node_text(prev, content_bytes))
    return ""


def _extract_function_declarator(node, content_bytes: bytes):
    name = ""
    params = ""
    for child in node.children:
        if child.type in ("identifier", "field_identifier") and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "qualified_identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "function_declarator":
            n, p = _extract_function_declarator(child, content_bytes)
            if n:
                name = n
            if p:
                params = p
        elif child.type == "parameter_list":
            raw = _node_text(child, content_bytes).strip()
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1]
            params = raw.strip()
    return name, params


def _extract_function(node, content_bytes: bytes, docstring: str = "") -> FunctionSig:
    name = ""
    params = ""
    return_type = ""

    for child in node.children:
        if child.type == "function_declarator":
            name, params = _extract_function_declarator(child, content_bytes)
        elif child.type in ("type_specifier", "primitive_type", "type_identifier",
                            "qualified_identifier", "auto"):
            if not return_type:
                return_type = _node_text(child, content_bytes)
        elif child.type == "pointer_declarator":
            for sub in child.children:
                if sub.type == "function_declarator":
                    name, params = _extract_function_declarator(sub, content_bytes)

    return FunctionSig(name=name, params=params, return_type=return_type, docstring=docstring)


def _extract_class(node, content_bytes: bytes, docstring: str = "") -> ClassSkeleton:
    name = ""
    bases: List[str] = []
    body_node = None

    for child in node.children:
        if child.type == "type_identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "base_class_clause":
            for sub in child.children:
                if sub.type == "type_identifier":
                    bases.append(_node_text(sub, content_bytes))
        elif child.type == "field_declaration_list":
            body_node = child

    methods: List[FunctionSig] = []
    if body_node:
        siblings = list(body_node.children)
        for idx, child in enumerate(siblings):
            if child.type == "function_definition":
                doc = _preceding_doc(siblings, idx, content_bytes)
                methods.append(_extract_function(child, content_bytes, docstring=doc))
            elif child.type in ("declaration", "field_declaration"):
                ret_type = ""
                fn_name = ""
                fn_params = ""
                for sub in child.children:
                    if sub.type in ("type_specifier", "primitive_type", "type_identifier",
                                    "qualified_identifier") and not ret_type:
                        ret_type = _node_text(sub, content_bytes)
                    elif sub.type == "function_declarator":
                        fn_name, fn_params = _extract_function_declarator(sub, content_bytes)
                        break
                if fn_name:
                    doc = _preceding_doc(siblings, idx, content_bytes)
                    methods.append(FunctionSig(name=fn_name, params=fn_params, return_type=ret_type, docstring=doc))

    return ClassSkeleton(name=name, bases=bases, docstring=docstring, methods=methods)


class CppExtractor(LanguageExtractor):
    def __init__(self):
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language, Parser

        self._language = Language(tscpp.language())
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
            if child.type == "preproc_include":
                for sub in child.children:
                    if sub.type in ("string_literal", "system_lib_string"):
                        raw = _node_text(sub, content_bytes).strip().strip('"<>')
                        imports.append(raw)
            elif child.type in ("class_specifier", "struct_specifier"):
                doc = _preceding_doc(siblings, idx, content_bytes)
                classes.append(_extract_class(child, content_bytes, docstring=doc))
            elif child.type == "function_definition":
                doc = _preceding_doc(siblings, idx, content_bytes)
                functions.append(_extract_function(child, content_bytes, docstring=doc))
            elif child.type == "namespace_definition":
                for sub in child.children:
                    if sub.type == "declaration_list":
                        inner = list(sub.children)
                        for i2, s2 in enumerate(inner):
                            if s2.type in ("class_specifier", "struct_specifier"):
                                doc = _preceding_doc(inner, i2, content_bytes)
                                classes.append(_extract_class(s2, content_bytes, docstring=doc))
                            elif s2.type == "function_definition":
                                doc = _preceding_doc(inner, i2, content_bytes)
                                functions.append(_extract_function(s2, content_bytes, docstring=doc))

        return CodeSkeleton(
            file_name=file_name,
            language="C/C++",
            module_doc="",
            imports=imports,
            classes=classes,
            functions=functions,
        )
