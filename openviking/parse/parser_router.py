# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
ParserRouter: Route parsing requests between ParserRegistry and UnderstanderAPI.

Routing is controlled by ov.conf (OpenVikingConfig.parser_api).
"""
from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from openviking.parse.accessors.base import LocalResource

from openviking_cli.utils.logger import get_logger
from openviking.parse.base import ParseResult
from openviking.parse.registry import ParserRegistry

logger = get_logger(__name__)



class ParserRouter:
    """
    ParserRouter: Route parsing to internal ParserRegistry or third-party UnderstanderAPI.

    Routing logic:
    1. Check feature flag and extension whitelist
    2. Default: ParserRegistry
    3. Matched extensions: UnderstanderAPI
    """

    def __init__(self, parser_registry: ParserRegistry):
        self._parser_registry = parser_registry
        self._understander_api = None

    def should_use_understander_api(self, source_path: Union[str, Path]) -> bool:
        """
        Decide whether to use UnderstanderAPI.
        """
        try:
            from openviking_cli.utils.config.open_viking_config import get_openviking_config

            ov_config = get_openviking_config()
        except Exception:
            return False

        parser_api = getattr(ov_config, "parser_api", None)
        if not parser_api or not getattr(parser_api, "enable", False):
            return False

        ext = Path(source_path).suffix.lower().lstrip(".")
        extensions = getattr(parser_api, "extensions", None) or []
        return ext in extensions

    async def parse(self, source: Union[str, Path, "LocalResource"], **kwargs) -> ParseResult:
        """
        Parse with ParserRegistry or UnderstanderAPI based on the routing decision.
        """
        source_path = self._extract_source_path(source)

        if self.should_use_understander_api(source_path):
            display = source_path
            if isinstance(source_path, str) and source_path.startswith(("http://", "https://")):
                display = "<url>"
            else:
                try:
                    display = Path(source_path).name
                except Exception:
                    display = "<path>"
            logger.info(f"[ParserRouter] Using UnderstanderAPI for {display}")
            return await self._get_understander_api().parse(str(source_path), **kwargs)
        else:
            try:
                display = Path(source_path).name
            except Exception:
                display = "<path>"
            logger.info(f"[ParserRouter] Using internal ParserRegistry for {display}")
            return await self._parser_registry.parse(source_path, **kwargs)

    def _extract_source_path(self, source: Union[str, Path, "LocalResource"]) -> Union[str, Path]:
        """Extract a filesystem path from the source."""
        if hasattr(source, "path"):
            return source.path
        return source

    def _get_understander_api(self):
        if self._understander_api is None:
            from openviking.parse.knowledge_parser import UnderstanderAPI
            self._understander_api = UnderstanderAPI()
        return self._understander_api
