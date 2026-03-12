# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""C/C++ AST extractor using tree-sitter-cpp."""

from typing import List, Optional

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


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
        elif child.type in (
            "type_specifier",
            "primitive_type",
            "type_identifier",
            "qualified_identifier",
            "auto",
        ):
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
                    if (
                        sub.type
                        in (
                            "type_specifier",
                            "primitive_type",
                            "type_identifier",
                            "qualified_identifier",
                        )
                        and not ret_type
                    ):
                        ret_type = _node_text(sub, content_bytes)
                    elif sub.type == "function_declarator":
                        fn_name, fn_params = _extract_function_declarator(sub, content_bytes)
                        break
                if fn_name:
                    doc = _preceding_doc(siblings, idx, content_bytes)
                    methods.append(
                        FunctionSig(
                            name=fn_name, params=fn_params, return_type=ret_type, docstring=doc
                        )
                    )

    return ClassSkeleton(name=name, bases=bases, docstring=docstring, methods=methods)


def _extract_typedef_struct(
    node, content_bytes: bytes, docstring: str = ""
) -> Optional[ClassSkeleton]:
    """Handle typedef struct { ... } Name; and typedef struct Tag { ... } Name;

    tree-sitter-cpp emits this as a 'type_definition' node with children:
      'typedef', struct_specifier, type_identifier (the alias), ';'
    """
    struct_node = None
    typedef_name = ""

    for child in node.children:
        if child.type in ("struct_specifier", "class_specifier"):
            struct_node = child
        elif child.type == "type_identifier" and struct_node is not None:
            typedef_name = _node_text(child, content_bytes)

    if struct_node is None:
        return None

    skeleton = _extract_class(struct_node, content_bytes, docstring=docstring)
    # Prefer the typedef alias as the canonical name
    if typedef_name:
        skeleton.name = typedef_name
    return skeleton if skeleton.name else None


def _extract_function_proto(
    node, content_bytes: bytes, docstring: str = ""
) -> Optional[FunctionSig]:
    """Extract a function prototype from a top-level declaration node."""
    fn = _extract_function(node, content_bytes, docstring=docstring)
    return fn if fn.name else None


def _process_siblings(
    siblings: list,
    content_bytes: bytes,
    classes: List[ClassSkeleton],
    functions: List[FunctionSig],
) -> None:
    """Extract classes and functions from a list of sibling nodes (shared by top-level and namespace)."""
    for idx, child in enumerate(siblings):
        doc = _preceding_doc(siblings, idx, content_bytes)
        if child.type in ("class_specifier", "struct_specifier"):
            classes.append(_extract_class(child, content_bytes, docstring=doc))
        elif child.type == "function_definition":
            functions.append(_extract_function(child, content_bytes, docstring=doc))
        elif child.type == "type_definition":
            cls = _extract_typedef_struct(child, content_bytes, docstring=doc)
            if cls:
                classes.append(cls)
        elif child.type == "declaration":
            fn = _extract_function_proto(child, content_bytes, docstring=doc)
            if fn:
                functions.append(fn)


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
        top_level = []
        for _idx, child in enumerate(siblings):
            if child.type == "preproc_include":
                for sub in child.children:
                    if sub.type in ("string_literal", "system_lib_string"):
                        raw = _node_text(sub, content_bytes).strip().strip('"<>')
                        imports.append(raw)
            elif child.type == "namespace_definition":
                for sub in child.children:
                    if sub.type == "declaration_list":
                        _process_siblings(list(sub.children), content_bytes, classes, functions)
            else:
                top_level.append(child)
        _process_siblings(top_level, content_bytes, classes, functions)

        return CodeSkeleton(
            file_name=file_name,
            language="C/C++",
            module_doc="",
            imports=imports,
            classes=classes,
            functions=functions,
        )
