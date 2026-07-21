# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
ParserRouter: Route parsing requests between ParserRegistry and UnderstandingAPI.

Routing is controlled by ov.conf (OpenVikingConfig.parser_api).
"""

from pathlib import Path
from typing import Union
from urllib.parse import urlparse

from openviking.parse.accessors.base import LocalResource, SourceType
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

    def understanding_api_enabled(self) -> bool:
        """Return whether the external parser is enabled."""
        try:
            from openviking_cli.utils.config.open_viking_config import get_openviking_config

            parser_api = getattr(get_openviking_config(), "parser_api", None)
        except Exception:
            return False
        return bool(parser_api and getattr(parser_api, "enable", False))

    def should_use_understanding_api(
        self,
        source: Union[str, Path, LocalResource],
        resolved_extension: str = "",
    ) -> bool:
        """
        Decide whether to use UnderstandingAPI.
        """
        # FeishuAccessor has already normalized proprietary content to Markdown.
        if isinstance(source, LocalResource) and source.source_type == SourceType.FEISHU:
            return False

        try:
            from openviking_cli.utils.config.open_viking_config import get_openviking_config

            ov_config = get_openviking_config()
        except Exception:
            return False

        parser_api = getattr(ov_config, "parser_api", None)
        if not parser_api or not getattr(parser_api, "enable", False):
            return False

        source_path = self._extract_source_path(source)
        try:
            from openviking.parse.accessors.feishu_accessor import FeishuAccessor

            if FeishuAccessor._is_feishu_url(str(source_path)):
                return bool(getattr(parser_api, "enable_feishu_url", False))
        except Exception:
            pass

        ext = self._normalize_extension(resolved_extension) or self._extract_extension(source_path)
        extensions = getattr(parser_api, "extensions", None) or []
        return ext in extensions

    @staticmethod
    def _normalize_extension(extension: str) -> str:
        return str(extension or "").lower().lstrip(".")

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

        parser_backend = kwargs.pop("parser_backend", None)
        if parser_backend not in {None, "internal", "understanding"}:
            raise ValueError(f"Unknown parser backend: {parser_backend}")

        normalized_feishu = (
            isinstance(source, LocalResource) and source.source_type == SourceType.FEISHU
        )
        use_understanding = not normalized_feishu and (
            parser_backend == "understanding"
            or (
                parser_backend is None
                and self.should_use_understanding_api(
                    source,
                    resolved_extension=str(kwargs.get("resolved_extension") or ""),
                )
            )
        )

        if use_understanding:
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

    async def submit_file(self, source: Union[str, Path]) -> str:
        return await self._get_understanding_api().submit_file(source)

    def _extract_source_path(self, source: Union[str, Path, LocalResource]) -> Union[str, Path]:
        """Extract a filesystem path from the source."""
        if hasattr(source, "path"):
            return source.path
        return source

    def _get_understanding_api(self):
        if self._understanding_api is None:
            from openviking.parse.understanding_api import UnderstandingAPI

            self._understanding_api = UnderstandingAPI()
        return self._understanding_api
