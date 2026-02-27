# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""JavaScript/TypeScript AST extractor using tree-sitter."""

from typing import List

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import ClassSkeleton, CodeSkeleton, FunctionSig


def _node_text(node, content_bytes: bytes) -> str:
    return content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _parse_jsdoc(raw: str) -> str:
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
    """Return JSDoc block comment immediately before siblings[idx], or ''."""
    if idx == 0:
        return ""
    prev = siblings[idx - 1]
    if prev.type == "comment":
        raw = _node_text(prev, content_bytes).strip()
        if raw.startswith("/*"):
            return _parse_jsdoc(raw)
        if raw.startswith("//"):
            return raw[2:].strip()
    return ""


def _first_string_in_body(body_node, content_bytes: bytes) -> str:
    if body_node is None:
        return ""
    for child in body_node.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type == "string":
                    raw = _node_text(sub, content_bytes).strip()
                    for q in ('"""', "'''", '"', "'", "`"):
                        if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                            return raw[len(q):-len(q)].strip()
                    return raw
            break
    return ""


def _extract_params(params_node, content_bytes: bytes) -> str:
    if params_node is None:
        return ""
    raw = _node_text(params_node, content_bytes).strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    return raw.strip()


def _extract_function(node, content_bytes: bytes, lang_name: str, docstring: str = "") -> FunctionSig:
    name = ""
    params = ""
    return_type = ""
    body_node = None

    for child in node.children:
        if child.type == "identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "property_identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type in ("formal_parameters", "call_signature"):
            params = _extract_params(child, content_bytes)
        elif child.type == "type_annotation":
            # TypeScript return type annotation
            for sub in child.children:
                if sub.type not in (":", ):
                    return_type = _node_text(sub, content_bytes).strip()
                    break
        elif child.type == "statement_block":
            body_node = child

    if not docstring:
        docstring = _first_string_in_body(body_node, content_bytes)
    return FunctionSig(name=name, params=params, return_type=return_type, docstring=docstring)


def _extract_class(node, content_bytes: bytes, lang_name: str, docstring: str = "") -> ClassSkeleton:
    name = ""
    bases: List[str] = []
    body_node = None

    for child in node.children:
        if child.type == "identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "type_identifier" and not name:
            name = _node_text(child, content_bytes)
        elif child.type == "class_heritage":
            for sub in child.children:
                if sub.type == "extends_clause":
                    for s2 in sub.children:
                        if s2.type in ("identifier", "type_identifier", "member_expression"):
                            bases.append(_node_text(s2, content_bytes))
        elif child.type == "class_body":
            body_node = child

    if not docstring:
        docstring = _first_string_in_body(body_node, content_bytes)
    methods: List[FunctionSig] = []

    if body_node:
        siblings = list(body_node.children)
        for idx, child in enumerate(siblings):
            if child.type == "method_definition":
                doc = _preceding_doc(siblings, idx, content_bytes)
                methods.append(_extract_function(child, content_bytes, lang_name, docstring=doc))
            elif child.type == "public_field_definition":
                # arrow function fields
                for sub in child.children:
                    if sub.type == "arrow_function":
                        doc = _preceding_doc(siblings, idx, content_bytes)
                        fn = _extract_function(sub, content_bytes, lang_name, docstring=doc)
                        # get field name
                        for s2 in child.children:
                            if s2.type in ("property_identifier", "identifier"):
                                fn.name = _node_text(s2, content_bytes)
                                break
                        methods.append(fn)
                        break

    return ClassSkeleton(name=name, bases=bases, docstring=docstring, methods=methods)


class JsTsExtractor(LanguageExtractor):
    def __init__(self, lang: str):
        """lang: 'javascript' or 'typescript'"""
        self._lang_name = "JavaScript" if lang == "javascript" else "TypeScript"
        if lang == "javascript":
            import tree_sitter_javascript as tsjs
            from tree_sitter import Language, Parser
            self._language = Language(tsjs.language())
        else:
            import tree_sitter_typescript as tsts
            from tree_sitter import Language, Parser
            self._language = Language(tsts.language_typescript())
        from tree_sitter import Parser
        self._parser = Parser(self._language)

    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        content_bytes = content.encode("utf-8")
        tree = self._parser.parse(content_bytes)
        root = tree.root_node

        imports: List[str] = []
        _seen_imports: set = set()
        classes: List[ClassSkeleton] = []
        functions: List[FunctionSig] = []

        siblings = list(root.children)
        for idx, child in enumerate(siblings):
            if child.type == "import_statement":
                # from "module"
                for sub in child.children:
                    if sub.type == "string":
                        raw = _node_text(sub, content_bytes).strip().strip('"\'')
                        if raw not in _seen_imports:
                            imports.append(raw)
                            _seen_imports.add(raw)
                        break
            elif child.type == "class_declaration":
                doc = _preceding_doc(siblings, idx, content_bytes)
                classes.append(_extract_class(child, content_bytes, self._lang_name, docstring=doc))
            elif child.type in ("function_declaration", "generator_function_declaration"):
                doc = _preceding_doc(siblings, idx, content_bytes)
                functions.append(_extract_function(child, content_bytes, self._lang_name, docstring=doc))
            elif child.type == "export_statement":
                # export default class / export function
                for sub in child.children:
                    if sub.type == "class_declaration":
                        doc = _preceding_doc(siblings, idx, content_bytes)
                        classes.append(_extract_class(sub, content_bytes, self._lang_name, docstring=doc))
                        break
                    elif sub.type in ("function_declaration", "generator_function_declaration"):
                        doc = _preceding_doc(siblings, idx, content_bytes)
                        functions.append(_extract_function(sub, content_bytes, self._lang_name, docstring=doc))
                        break
            elif child.type == "lexical_declaration":
                # const foo = () => ...
                for sub in child.children:
                    if sub.type == "variable_declarator":
                        fn_name = ""
                        for s2 in sub.children:
                            if s2.type == "identifier":
                                fn_name = _node_text(s2, content_bytes)
                            elif s2.type == "arrow_function":
                                doc = _preceding_doc(siblings, idx, content_bytes)
                                fn = _extract_function(s2, content_bytes, self._lang_name, docstring=doc)
                                fn.name = fn_name
                                functions.append(fn)

        return CodeSkeleton(
            file_name=file_name,
            language=self._lang_name,
            module_doc="",
            imports=imports,
            classes=classes,
            functions=functions,
        )
