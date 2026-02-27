# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""CodeSkeleton dataclasses and to_text() serialization."""

import re
from dataclasses import dataclass, field
from typing import List


def _compact_params(params: str) -> str:
    """Collapse multi-line params into a single line."""
    return re.sub(r"\s+", " ", params).strip().strip(",")


@dataclass
class FunctionSig:
    name: str
    params: str       # raw parameter string, e.g. "source, instruction, **kwargs"
    return_type: str  # e.g. "ParseResult" or ""
    docstring: str    # first line only


@dataclass
class ClassSkeleton:
    name: str
    bases: List[str]
    docstring: str
    methods: List[FunctionSig] = field(default_factory=list)


@dataclass
class CodeSkeleton:
    file_name: str
    language: str
    module_doc: str
    imports: List[str]              # flattened, e.g. ["asyncio", "os", "typing.Optional"]
    classes: List[ClassSkeleton]
    functions: List[FunctionSig]    # top-level functions only

    def to_text(self, verbose: bool = False) -> str:
        """Generate skeleton text.

        Args:
            verbose: If True, include full docstrings (for ast_llm mode / LLM input).
                     If False, only keep the first line (for ast mode / direct embedding).
        """
        def _doc(raw: str, indent: str) -> List[str]:
            if not raw:
                return []
            first = raw.split("\n")[0].strip()
            if not verbose:
                return [f'{indent}"""{first}"""']
            # verbose: keep full docstring, re-indent each line
            doc_lines = raw.strip().split("\n")
            if len(doc_lines) == 1:
                return [f'{indent}"""{first}"""']
            return [f'{indent}"""{doc_lines[0]}'] + \
                   [f'{indent}{l.strip()}' for l in doc_lines[1:]] + \
                   [f'{indent}"""']

        lines: List[str] = []

        # Header
        lines.append(f"# {self.file_name} [{self.language}]")

        # Module docstring — always single-line with "module:" label
        if self.module_doc:
            first = self.module_doc.split("\n")[0].strip()
            lines.append(f'module: "{first}"')

        # Imports (compact)
        if self.imports:
            lines.append(f"imports: {', '.join(self.imports)}")

        lines.append("")

        # Classes
        for cls in self.classes:
            bases_str = f"({', '.join(cls.bases)})" if cls.bases else ""
            lines.append(f"class {cls.name}{bases_str}")
            lines.extend(_doc(cls.docstring, "  "))
            for method in cls.methods:
                ret = f" -> {method.return_type}" if method.return_type else ""
                params = _compact_params(method.params)
                lines.append(f"  + {method.name}({params}){ret}")
                lines.extend(_doc(method.docstring, "    "))
            lines.append("")

        # Top-level functions
        for fn in self.functions:
            ret = f" -> {fn.return_type}" if fn.return_type else ""
            params = _compact_params(fn.params)
            lines.append(f"def {fn.name}({params}){ret}")
            lines.extend(_doc(fn.docstring, "  "))

        return "\n".join(lines).strip()
