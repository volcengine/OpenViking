# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Go AST extractor using tree-sitter-go."""

from typing import List

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _preceding_doc(siblings: list, idx: int, content_bytes: bytes) -> str:
    """Collect consecutive // comment lines immediately before siblings[idx]."""
    lines = []
    i = idx - 1
    while i >= 0 and siblings[i].type == "comment":
        raw = _node_text(siblings[i], content_bytes).strip()
        # strip leading //
        if raw.startswith("//"):
            raw = raw[2:].strip()
        lines.insert(0, raw)
        i -= 1
    return "\n".join(lines).strip()


def _extract_function(node, content_bytes: bytes, docstring: str = "") -> FunctionSig:
    name = ""
    params = ""
    return_type = ""
    is_method = node.type == "method_declaration"
    param_list_count = 0

    for child in node.children:
        if child.type == "identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "field_identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "parameter_list":
            param_list_count += 1
            if is_method and param_list_count == 1:
                continue  # first parameter_list is receiver (s *Server), not params
            if not params:
                raw = _node_text(child, content_bytes).strip()
                if raw.startswith("(") and raw.endswith(")"):
                    raw = raw[1:-1]
                params = raw.strip()
        elif child.type == "type_identifier":
            return_type = _node_text(child, content_bytes)

    return FunctionSig(name=name, params=params, return_type=return_type, docstring=docstring)


def _extract_struct(node, content_bytes: bytes, docstring: str = "") -> ClassSkeleton:
    name = ""
    for child in node.children:
        if child.type == "type_identifier":
            name = _node_text(child, content_bytes)
            break
    return ClassSkeleton(name=name, bases=[], docstring=docstring, methods=[])


class GoExtractor(LanguageExtractor):
    def __init__(self):
        import tree_sitter_go as tsgo
        from tree_sitter import Language, Parser

        self._language = Language(tsgo.language())
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
            if child.type == "import_declaration":
                for sub in child.children:
                    if sub.type == "import_spec":
                        for s2 in sub.children:
                            if s2.type == "interpreted_string_literal":
                                imports.append(_node_text(s2, content_bytes).strip().strip('"'))
                    elif sub.type == "import_spec_list":
                        for s2 in sub.children:
                            if s2.type == "import_spec":
                                for s3 in s2.children:
                                    if s3.type == "interpreted_string_literal":
                                        imports.append(_node_text(s3, content_bytes).strip().strip('"'))
            elif child.type in ("function_declaration", "method_declaration"):
                doc = _preceding_doc(siblings, idx, content_bytes)
                functions.append(_extract_function(child, content_bytes, docstring=doc))
            elif child.type == "type_declaration":
                for sub in child.children:
                    if sub.type == "type_spec":
                        for s2 in sub.children:
                            if s2.type in ("struct_type", "interface_type"):
                                doc = _preceding_doc(siblings, idx, content_bytes)
                                classes.append(_extract_struct(sub, content_bytes, docstring=doc))
                                break

        return CodeSkeleton(
            file_name=file_name,
            language="Go",
            module_doc="",
            imports=imports,
            classes=classes,
            functions=functions,
        )
