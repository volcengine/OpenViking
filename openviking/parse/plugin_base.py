# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Base contracts for parser providers."""

from abc import ABC, abstractmethod
from typing import List, Optional

from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig


class ParserProvider(ABC):
    """Factory contract for parser plugins."""

    name: str

    @property
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """List of extensions exposed by this provider."""

    def is_available(self) -> bool:
        """Whether this provider can create a parser in the current environment."""
        return True

    @abstractmethod
    def create_parser(self, config: Optional[ParserConfig] = None) -> BaseParser:
        """Build a parser instance for registry registration."""
