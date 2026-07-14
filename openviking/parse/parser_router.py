# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
ParserRouter: Route parsing requests between ParserRegistry and UnderstandingAPI.

Routing is controlled by ov.conf (OpenVikingConfig.parser_api).
"""

from pathlib import Path
from typing import TYPE_CHECKING, Union
from urllib.parse import urlparse

if TYPE_CHECKING:
    from openviking.parse.accessors.base import LocalResource

from openviking.parse.base import ParseResult
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class ParserRouter:
    """
    ParserRouter: Route parsing to internal ParserRegistry or third-party UnderstandingAPI.

    Routing logic:
    1. Check feature flag and extension whitelist
    2. Default: ParserRegistry
    3. Matched extensions: UnderstandingAPI
    """

    def __init__(self, parser_registry: ParserRegistry):
        self._parser_registry = parser_registry
        self._understanding_api = None

    def should_use_understanding_api(self, source_path: Union[str, Path]) -> bool:
        """
        Decide whether to use UnderstandingAPI.
        """
        try:
            from openviking_cli.utils.config.open_viking_config import get_openviking_config

            ov_config = get_openviking_config()
        except Exception:
            return False

        parser_api = getattr(ov_config, "parser_api", None)
        if not parser_api or not getattr(parser_api, "enable", False):
            return False

        if getattr(parser_api, "enable_feishu_url", False):
            try:
                from openviking.parse.accessors.feishu_accessor import FeishuAccessor

                if FeishuAccessor._is_feishu_url(str(source_path)):
                    return True
            except Exception:
                pass

        ext = self._extract_extension(source_path)
        extensions = getattr(parser_api, "extensions", None) or []
        return ext in extensions

    def _extract_extension(self, source_path: Union[str, Path]) -> str:
        source = str(source_path)
        parsed = urlparse(source)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            source = parsed.path
        return Path(source).suffix.lower().lstrip(".")

    async def parse(self, source: Union[str, Path, "LocalResource"], **kwargs) -> ParseResult:
        """
        Parse with ParserRegistry or UnderstandingAPI based on the routing decision.
        """
        source_path = self._extract_source_path(source)

        if self.should_use_understanding_api(source_path):
            display = source_path
            if isinstance(source_path, str) and source_path.startswith(("http://", "https://")):
                display = "<url>"
            else:
                try:
                    display = Path(source_path).name
                except Exception:
                    display = "<path>"
            logger.info(f"[ParserRouter] Using UnderstandingAPI for {display}")
            return await self._get_understanding_api().parse(str(source_path), **kwargs)
        else:
            try:
                display = Path(source_path).name
            except Exception:
                display = "<path>"
            logger.info(f"[ParserRouter] Using internal ParserRegistry for {display}")
            return await self._parser_registry.parse(source_path, **kwargs)

    async def submit_url(self, source: str, **kwargs) -> str:
        if not self.should_use_understanding_api(source):
            raise ValueError("source is not routed to UnderstandingAPI")
        return await self._get_understanding_api().submit_url(source, **kwargs)

    def _extract_source_path(self, source: Union[str, Path, "LocalResource"]) -> Union[str, Path]:
        """Extract a filesystem path from the source."""
        if hasattr(source, "path"):
            return source.path
        return source

    def _get_understanding_api(self):
        if self._understanding_api is None:
            from openviking.parse.understanding_api import UnderstandingAPI

            self._understanding_api = UnderstandingAPI()
        return self._understanding_api
