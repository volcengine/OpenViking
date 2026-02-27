# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Abstract base class for language-specific AST extractors."""

from abc import ABC, abstractmethod

from openviking.parse.parsers.code.ast.skeleton import CodeSkeleton


class LanguageExtractor(ABC):
    @abstractmethod
    def extract(self, file_name: str, content: str) -> CodeSkeleton:
        """Extract code skeleton from source. Raises on unrecoverable error."""
