    # Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""ASTExtractor: language detection + dispatch to per-language extractors."""

import importlib
import logging
from pathlib import Path
from typing import Dict, Optional

from openviking.parse.parsers.code.ast.languages.base import LanguageExtractor
from openviking.parse.parsers.code.ast.skeleton import CodeSkeleton

logger = logging.getLogger(__name__)

# File extension → internal language key
_EXT_MAP: Dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "cpp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".cs": "csharp",
}

# Language key → (module path, class name, constructor kwargs)
_EXTRACTOR_REGISTRY: Dict[str, tuple] = {
    "python": ("openviking.parse.parsers.code.ast.languages.python", "PythonExtractor", {}),
    "javascript": ("openviking.parse.parsers.code.ast.languages.js_ts", "JsTsExtractor", {"lang": "javascript"}),
    "typescript": ("openviking.parse.parsers.code.ast.languages.js_ts", "JsTsExtractor", {"lang": "typescript"}),
    "java": ("openviking.parse.parsers.code.ast.languages.java", "JavaExtractor", {}),
    "cpp": ("openviking.parse.parsers.code.ast.languages.cpp", "CppExtractor", {}),
    "rust": ("openviking.parse.parsers.code.ast.languages.rust", "RustExtractor", {}),
    "go": ("openviking.parse.parsers.code.ast.languages.go", "GoExtractor", {}),
    "csharp": ("openviking.parse.parsers.code.ast.languages.csharp", "CSharpExtractor", {}),
}


class ASTExtractor:
    """Dispatches to per-language tree-sitter extractors for supported languages.

    Unsupported languages return None, signalling the caller to fall back to LLM.
    """

    def __init__(self):
        self._cache: Dict[str, Optional[LanguageExtractor]] = {}

    def _detect_language(self, file_name: str) -> Optional[str]:
        suffix = Path(file_name).suffix.lower()
        return _EXT_MAP.get(suffix)

    def _get_extractor(self, lang: Optional[str]) -> Optional[LanguageExtractor]:
        if lang is None or lang not in _EXTRACTOR_REGISTRY:
            return None

        if lang in self._cache:
            return self._cache[lang]

        module_path, class_name, kwargs = _EXTRACTOR_REGISTRY[lang]
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            extractor = cls(**kwargs)
            self._cache[lang] = extractor
            return extractor
        except Exception as e:
            logger.warning("AST extractor unavailable for language '%s', falling back to LLM: %s", lang, e)
            self._cache[lang] = None
            return None

    def extract_skeleton(self, file_name: str, content: str, verbose: bool = False) -> Optional[str]:
        """Extract skeleton text from source code.

        Returns None for unsupported languages or on extraction failure,
        signalling the caller to fall back to LLM.

        Args:
            verbose: If True, include full docstrings (for ast_llm / LLM input).
                     If False, only first line of each docstring (for ast / embedding).
        """
        lang = self._detect_language(file_name)
        extractor = self._get_extractor(lang)
        if extractor is None:
            return None

        try:
            skeleton: CodeSkeleton = extractor.extract(file_name, content)
            return skeleton.to_text(verbose=verbose)
        except Exception as e:
            logger.warning("AST extraction failed for '%s' (language: %s), falling back to LLM: %s", file_name, lang, e)
            return None


# Module-level singleton
_extractor: Optional[ASTExtractor] = None


def get_extractor() -> ASTExtractor:
    global _extractor
    if _extractor is None:
        _extractor = ASTExtractor()
    return _extractor
